from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
import socket
from typing import Any
from uuid import uuid4

from rag.kafka.producer import KafkaJsonProducer


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class RagOutboxEvent:
    """从 PostgreSQL 抢占后等待发布的一条 RAG Outbox 事件。"""

    id: int
    topic: str
    message_key: str
    payload_json: str
    attempt: int


@dataclass(frozen=True)
class OutboxPublishResult:
    """记录单轮 Outbox 发布结果，供 cron 日志与测试使用。"""

    claimed: int
    published: int
    failed: int


class PostgresRagOutboxRepository:
    """以 PostgreSQL 行锁和租约管理 `rag_outbox_event`。"""

    def __init__(self, database_url: str | None = None, schema: str | None = None) -> None:
        self.database_url = database_url or os.getenv("RAG_DATABASE_URL") or os.getenv("DATABASE_URL", "")
        self.schema = schema or os.getenv("RAG_DATABASE_SCHEMA", "learning_evidence")
        if not self.database_url:
            raise RuntimeError("RAG_DATABASE_URL 未配置，无法启动 Python Outbox 发布器")

    def claim_due_events(
        self,
        *,
        now: datetime,
        batch_size: int,
        publisher_id: str,
        lease_seconds: int,
    ) -> list[RagOutboxEvent]:
        """在同一事务中锁定并租约到期事件，避免多实例重复抢占。"""
        from psycopg import sql

        table = self._table(sql)
        lease_until = now + timedelta(seconds=lease_seconds)
        claimed: list[RagOutboxEvent] = []
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            SELECT id, topic, message_key, payload_json, attempt
                            FROM {}
                            WHERE (
                                status IN ('NEW', 'FAILED')
                                AND next_attempt_at <= %s
                                AND (lease_until IS NULL OR lease_until <= %s)
                            )
                            OR (
                                status = 'PUBLISHING'
                                AND lease_until <= %s
                            )
                            ORDER BY id ASC
                            LIMIT %s
                            FOR UPDATE SKIP LOCKED
                            """
                        ).format(table),
                        (now, now, now, batch_size),
                    )
                    rows = cursor.fetchall()
                    for row in rows:
                        cursor.execute(
                            sql.SQL(
                                """
                                UPDATE {}
                                SET status = 'PUBLISHING',
                                    locked_by = %s,
                                    lease_until = %s,
                                    attempt = attempt + 1,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                                  AND (
                                    (
                                        status IN ('NEW', 'FAILED')
                                        AND (lease_until IS NULL OR lease_until <= %s)
                                    )
                                    OR (status = 'PUBLISHING' AND lease_until <= %s)
                                  )
                                """
                            ).format(table),
                            (publisher_id, lease_until, row["id"], now, now),
                        )
                        if cursor.rowcount > 0:
                            claimed.append(self._to_event(row))
        return claimed

    def mark_published(self, event_id: int) -> None:
        """在 Kafka 确认投递后将事件标记为已发布。"""
        from psycopg import sql

        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET status = 'PUBLISHED',
                                published_at = CURRENT_TIMESTAMP,
                                lease_until = NULL,
                                locked_by = NULL,
                                error_message = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                            """
                        ).format(self._table(sql)),
                        (event_id,),
                    )

    def mark_failed(self, event_id: int, error_message: str, next_attempt_at: datetime) -> None:
        """记录失败摘要并释放租约，使事件可在退避窗口后重新发布。"""
        from psycopg import sql

        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            """
                            UPDATE {}
                            SET status = 'FAILED',
                                error_message = %s,
                                next_attempt_at = %s,
                                lease_until = NULL,
                                locked_by = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                            """
                        ).format(self._table(sql)),
                        (error_message, next_attempt_at, event_id),
                    )

    def _connect(self):
        """建立字典行 PostgreSQL 连接；连接仅在单次数据库操作中持有。"""
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("使用 Python Outbox 发布器需要安装 psycopg[binary]") from exc
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _table(self, sql_module: Any):
        """使用标识符组合 schema，避免由配置文本拼接 SQL。"""
        return sql_module.Identifier(self.schema, "rag_outbox_event")

    @staticmethod
    def _to_event(row: dict[str, Any]) -> RagOutboxEvent:
        return RagOutboxEvent(
            id=int(row["id"]),
            topic=str(row["topic"]),
            message_key=str(row["message_key"]),
            payload_json=str(row["payload_json"]),
            attempt=int(row.get("attempt") or 0),
        )


class RagOutboxPublisher:
    """迁移 Java `RagOutboxPublisher` 的至少一次 Kafka 发布逻辑。"""

    def __init__(
        self,
        *,
        repository: PostgresRagOutboxRepository | None = None,
        producer: KafkaJsonProducer | None = None,
        publisher_id: str | None = None,
    ) -> None:
        self.repository = repository or PostgresRagOutboxRepository()
        self.producer = producer or KafkaJsonProducer()
        self.publisher_id = publisher_id or build_publisher_id()

    def publish_due_events(self) -> OutboxPublishResult:
        """抢占一批到期事件，Kafka 确认成功后才写入 `PUBLISHED`。"""
        now = datetime.now(timezone.utc)
        events = self.repository.claim_due_events(
            now=now,
            batch_size=positive_int("RAG_OUTBOX_BATCH_SIZE", 50),
            publisher_id=self.publisher_id,
            lease_seconds=positive_int("RAG_OUTBOX_LEASE_SECONDS", 60),
        )
        published = 0
        failed = 0
        for event in events:
            try:
                self.producer.send_serialized(
                    event.topic,
                    event.message_key,
                    event.payload_json,
                    flush_seconds=publish_timeout_seconds(),
                )
                self.repository.mark_published(event.id)
                published += 1
            except Exception as exc:
                failed += 1
                self.repository.mark_failed(
                    event.id,
                    safe_error_summary(exc),
                    datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds(event.attempt)),
                )
                LOGGER.warning(
                    "RAG Outbox 发布失败: id=%s, topic=%s, errorType=%s",
                    event.id,
                    event.topic,
                    exc.__class__.__name__,
                )
        return OutboxPublishResult(claimed=len(events), published=published, failed=failed)


def build_publisher_id() -> str:
    """生成可定位进程但不包含业务数据的发布者标识。"""
    try:
        host = socket.gethostname() or "unknown"
    except OSError:
        host = "unknown"
    return f"{host}-{uuid4()}"


def backoff_seconds(attempt: int | None) -> int:
    """按 Java 既有规则计算有上限的指数退避秒数。"""
    safe_attempt = max(1, attempt or 1)
    max_attempts = positive_int("RAG_OUTBOX_MAX_ATTEMPTS", 8)
    return min(3600, 2 ** min(safe_attempt, max_attempts))


def publish_timeout_seconds() -> float:
    """读取 Java 兼容的单条 Outbox 投递超时，最小保留 100ms。"""
    return max(0.1, positive_int("RAG_KAFKA_PUBLISH_TIMEOUT_MS", 3000) / 1000)


def positive_int(name: str, default: int) -> int:
    """读取正整数配置，异常或非法值回退到安全默认值。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def safe_error_summary(error: Exception) -> str:
    """持久化异常类别而非原始文本，避免错误消息意外回显资料或密钥。"""
    return f"Kafka 发布失败：{error.__class__.__name__}"[:1000]
