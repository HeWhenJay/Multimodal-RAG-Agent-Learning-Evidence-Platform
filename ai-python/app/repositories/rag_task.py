"""RAG 查询耐久任务的 PostgreSQL 仓储。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
import json
import os
import re
from threading import Lock
from typing import Any, Protocol


SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class DurableQueryTask:
    """查询 worker 所需的持久任务与历史快照。"""

    id: int
    task_id: str
    user_id: str
    question: str
    top_k: int
    status: str
    request_json: str
    attempt: int
    answer: str | None = None
    evidence_count: int = 0
    expanded_queries_json: str = "[]"
    evidences_json: str = "[]"
    diagnostics_json: str = "{}"
    progress_events_json: str = "[]"
    error_message: str | None = None
    duration_ms: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None


class RagQueryTaskRepositoryProtocol(Protocol):
    """查询控制面和 worker 的最小耐久任务接口。"""

    def enqueue(
        self,
        *,
        task_id: str,
        user_id: str,
        question: str,
        top_k: int,
        request_payload: dict[str, Any],
    ) -> DurableQueryTask: ...

    def get(self, task_id: str, user_id: str) -> DurableQueryTask | None: ...

    def claim(self, *, worker_id: str, batch_size: int, lease_seconds: int) -> list[DurableQueryTask]: ...

    def append_progress(self, task_id: str, user_id: str, event: dict[str, Any]) -> None: ...

    def complete(self, task_id: str, user_id: str, response: dict[str, Any], duration_ms: int) -> None: ...

    def fail(self, task_id: str, user_id: str, error_message: str, duration_ms: int) -> None: ...

    def expire_due(self) -> int: ...


class PostgresRagQueryTaskRepository:
    """用 `FOR UPDATE SKIP LOCKED` 和租约驱动可恢复的查询任务。"""

    def __init__(self, database_url: str | None = None, schema: str | None = None) -> None:
        self.database_url = database_url or resolve_database_url()
        self.schema = schema or os.getenv("RAG_DATABASE_SCHEMA", "learning_evidence")
        if not self.database_url:
            raise RuntimeError("未配置 RAG_CONTROL_DATABASE_URL、RAG_DATABASE_URL 或 DATABASE_URL")
        if not SCHEMA_PATTERN.fullmatch(self.schema):
            raise RuntimeError("RAG_DATABASE_SCHEMA 必须是合法的 PostgreSQL schema 标识符")

    def enqueue(
        self,
        *,
        task_id: str,
        user_id: str,
        question: str,
        top_k: int,
        request_payload: dict[str, Any],
    ) -> DurableQueryTask:
        """将查询历史和任务投递在同一事务内写入，避免只写到一半。"""
        ttl_seconds = positive_seconds("RAG_QUERY_TASK_TTL_SECONDS", 1800)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            INSERT INTO {schema}.rag_query_history (
                                user_id, task_id, question, status, top_k,
                                expanded_queries_json, evidences_json, diagnostics_json, progress_events_json
                            )
                            VALUES (%s, %s, %s, 'RUNNING', %s, '[]', '[]', '{{}}', '[]')
                            RETURNING *
                            """
                        ),
                        (user_id, task_id, question, top_k),
                    )
                    history = cursor.fetchone()
                    if history is None:
                        raise RuntimeError("创建 RAG 查询历史失败")
                    cursor.execute(
                        self._sql(
                            """
                            INSERT INTO {schema}.rag_query_task (
                                id, user_id, query_history_id, status, request_json,
                                attempt, next_attempt_at, expires_at
                            )
                            VALUES (%s, %s, %s, 'REQUESTED', %s, 0, CURRENT_TIMESTAMP, %s)
                            """
                        ),
                        (task_id, user_id, history["id"], to_json(request_payload), expires_at),
                    )
        return self.get(task_id, user_id) or self._from_history_and_task(history, {"id": task_id, "attempt": 0, "expires_at": expires_at})

    def get(self, task_id: str, user_id: str) -> DurableQueryTask | None:
        """读取用户专属任务快照，并在读取时收敛超时状态。"""
        self.expire_due()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        SELECT h.*, t.id AS durable_task_id, t.request_json, t.attempt,
                               t.status AS durable_status, t.expires_at
                        FROM {schema}.rag_query_task t
                        JOIN {schema}.rag_query_history h ON h.id = t.query_history_id
                        WHERE t.id = %s AND t.user_id = %s
                        """
                    ),
                    (task_id, user_id),
                )
                row = cursor.fetchone()
        return self._from_row(row)

    def claim(self, *, worker_id: str, batch_size: int, lease_seconds: int) -> list[DurableQueryTask]:
        """抢占待执行或租约过期任务；每次更新都在同一数据库事务中完成。"""
        self.expire_due()
        now = datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=max(10, int(lease_seconds)))
        safe_batch = max(1, min(int(batch_size), 100))
        claimed: list[DurableQueryTask] = []
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            SELECT t.id AS durable_task_id,
                                   t.request_json,
                                   t.attempt,
                                   t.expires_at,
                                   h.*
                            FROM {schema}.rag_query_task t
                            JOIN {schema}.rag_query_history h ON h.id = t.query_history_id
                            WHERE t.expires_at > %s
                              AND (
                                (t.status = 'REQUESTED' AND t.next_attempt_at <= %s)
                                OR (t.status = 'RUNNING' AND t.lease_until <= %s)
                              )
                            ORDER BY t.created_at ASC, t.id ASC
                            LIMIT %s
                            FOR UPDATE OF t SKIP LOCKED
                            """
                        ),
                        (now, now, now, safe_batch),
                    )
                    for row in cursor.fetchall():
                        cursor.execute(
                            self._sql(
                                """
                                UPDATE {schema}.rag_query_task
                                SET status = 'RUNNING',
                                    attempt = attempt + 1,
                                    locked_by = %s,
                                    lease_until = %s,
                                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                                """
                            ),
                            (worker_id, lease_until, row["durable_task_id"]),
                        )
                        claimed.append(self._from_row({**row, "attempt": int(row.get("attempt") or 0) + 1}) or self._invalid_row())
        return claimed

    def append_progress(self, task_id: str, user_id: str, event: dict[str, Any]) -> None:
        """逐阶段持久化进度，worker 被终止后轮询仍能看到已完成阶段。"""
        safe_event = redact_progress_event(event)
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            SELECT t.status AS durable_status,
                                   t.expires_at,
                                   h.progress_events_json
                            FROM {schema}.rag_query_task t
                            JOIN {schema}.rag_query_history h ON h.id = t.query_history_id
                            WHERE t.id = %s AND t.user_id = %s
                            FOR UPDATE
                            """
                        ),
                        (task_id, user_id),
                    )
                    row = cursor.fetchone()
                    if row is None or str(row.get("durable_status") or "") != "RUNNING" or is_expired(row.get("expires_at")):
                        return
                    events = parse_json_list(row.get("progress_events_json"))
                    events.append(safe_event)
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_query_history
                            SET progress_events_json = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE task_id = %s AND user_id = %s
                            """
                        ),
                        (to_json(events[-100:]), task_id, user_id),
                    )

    def complete(self, task_id: str, user_id: str, response: dict[str, Any], duration_ms: int) -> None:
        """在一个事务内写入回答快照和任务完成状态。"""
        evidences = response.get("evidences") if isinstance(response.get("evidences"), list) else []
        diagnostics = response.get("diagnostics") if isinstance(response.get("diagnostics"), dict) else {}
        diagnostics = {**diagnostics, "answerGuard": answer_guard(response)}
        response_progress = response.get("progressEvents") if isinstance(response.get("progressEvents"), list) else []
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            SELECT t.status AS durable_status,
                                   t.expires_at,
                                   h.progress_events_json
                            FROM {schema}.rag_query_task t
                            JOIN {schema}.rag_query_history h ON h.id = t.query_history_id
                            WHERE t.id = %s AND t.user_id = %s
                            FOR UPDATE
                            """
                        ),
                        (task_id, user_id),
                    )
                    current = cursor.fetchone() or {}
                    if str(current.get("durable_status") or "") != "RUNNING" or is_expired(current.get("expires_at")):
                        return
                    # worker 已逐阶段写入的事件优先，避免最终 QueryResponse 再次重复追加。
                    progress_events = parse_json_list(current.get("progress_events_json")) or [
                        redact_progress_event(item) for item in response_progress
                    ]
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_query_history
                            SET answer = %s,
                                status = 'COMPLETED',
                                evidence_count = %s,
                                expanded_queries_json = %s,
                                evidences_json = %s,
                                diagnostics_json = %s,
                                progress_events_json = %s,
                                error_message = NULL,
                                duration_ms = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE task_id = %s AND user_id = %s
                            """
                        ),
                        (
                            str(response.get("answer") or ""),
                            len(evidences),
                            to_json(response.get("expandedQueries") if isinstance(response.get("expandedQueries"), list) else []),
                            to_json(evidences),
                            to_json(diagnostics),
                            to_json(progress_events[-100:]),
                            max(0, int(duration_ms)),
                            task_id,
                            user_id,
                        ),
                    )
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_query_task
                            SET status = 'COMPLETED',
                                lease_until = NULL,
                                locked_by = NULL,
                                finished_at = CURRENT_TIMESTAMP,
                                error_message = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s AND user_id = %s AND status = 'RUNNING'
                            """
                        ),
                        (task_id, user_id),
                    )

    def fail(self, task_id: str, user_id: str, error_message: str, duration_ms: int) -> None:
        """失败写入稳定摘要，禁止把模型或资料原文泄漏给任务轮询接口。"""
        message = truncate(error_message or "RAG 查询失败", 1000)
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            SELECT status, expires_at
                            FROM {schema}.rag_query_task
                            WHERE id = %s AND user_id = %s
                            FOR UPDATE
                            """
                        ),
                        (task_id, user_id),
                    )
                    task = cursor.fetchone() or {}
                    if str(task.get("status") or "") != "RUNNING" or is_expired(task.get("expires_at")):
                        return
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_query_history
                            SET status = 'FAILED',
                                error_message = %s,
                                duration_ms = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE task_id = %s AND user_id = %s
                            """
                        ),
                        (message, max(0, int(duration_ms)), task_id, user_id),
                    )
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_query_task
                            SET status = 'FAILED',
                                lease_until = NULL,
                                locked_by = NULL,
                                error_message = %s,
                                finished_at = CURRENT_TIMESTAMP,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s AND user_id = %s AND status = 'RUNNING'
                            """
                        ),
                        (message, task_id, user_id),
                    )

    def expire_due(self) -> int:
        """将长期未完成任务收敛为 `EXPIRED`，历史与任务状态保持一致。"""
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_query_history h
                            SET status = 'EXPIRED',
                                error_message = 'RAG 查询任务已过期',
                                updated_at = CURRENT_TIMESTAMP
                            FROM {schema}.rag_query_task t
                            WHERE h.id = t.query_history_id
                              AND t.expires_at <= %s
                              AND t.status IN ('REQUESTED', 'RUNNING')
                            """
                        ),
                        (now,),
                    )
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_query_task
                            SET status = 'EXPIRED',
                                lease_until = NULL,
                                locked_by = NULL,
                                finished_at = CURRENT_TIMESTAMP,
                                error_message = 'RAG 查询任务已过期',
                                updated_at = CURRENT_TIMESTAMP
                            WHERE expires_at <= %s
                              AND status IN ('REQUESTED', 'RUNNING')
                            """
                        ),
                        (now,),
                    )
                    return cursor.rowcount

    def _connect(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("RAG 查询任务仓储需要安装 psycopg[binary]") from exc
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _sql(self, statement: str):
        from psycopg import sql

        return sql.SQL(statement).format(schema=sql.Identifier(self.schema))

    def _from_row(self, row: dict[str, Any] | None) -> DurableQueryTask | None:
        if row is None:
            return None
        return DurableQueryTask(
            id=int(row["id"]),
            task_id=str(row.get("durable_task_id") or row.get("task_id") or row.get("id")),
            user_id=str(row["user_id"]),
            question=str(row["question"]),
            top_k=int(row.get("top_k") or 5),
            status=str(row.get("status") or row.get("durable_status") or "RUNNING"),
            request_json=str(row.get("request_json") or "{}"),
            attempt=int(row.get("attempt") or 0),
            answer=row.get("answer"),
            evidence_count=int(row.get("evidence_count") or 0),
            expanded_queries_json=str(row.get("expanded_queries_json") or "[]"),
            evidences_json=str(row.get("evidences_json") or "[]"),
            diagnostics_json=str(row.get("diagnostics_json") or "{}"),
            progress_events_json=str(row.get("progress_events_json") or "[]"),
            error_message=row.get("error_message"),
            duration_ms=row.get("duration_ms"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            expires_at=row.get("expires_at"),
        )

    def _from_history_and_task(self, history: dict[str, Any], task: dict[str, Any]) -> DurableQueryTask:
        return DurableQueryTask(
            id=int(history["id"]),
            task_id=str(task["id"]),
            user_id=str(history["user_id"]),
            question=str(history["question"]),
            top_k=int(history.get("top_k") or 5),
            status="RUNNING",
            request_json="{}",
            attempt=int(task.get("attempt") or 0),
            created_at=history.get("created_at"),
            updated_at=history.get("updated_at"),
            expires_at=task.get("expires_at"),
        )

    @staticmethod
    def _invalid_row() -> DurableQueryTask:
        raise RuntimeError("读取 RAG 查询耐久任务失败")


class InMemoryRagQueryTaskRepository:
    """数据库不可用时仅供测试使用的显式内存降级，不用于生产恢复承诺。"""

    def __init__(self) -> None:
        self._tasks: dict[tuple[str, str], DurableQueryTask] = {}
        self._lock = Lock()
        self._sequence = 0

    def enqueue(self, *, task_id: str, user_id: str, question: str, top_k: int, request_payload: dict[str, Any]) -> DurableQueryTask:
        with self._lock:
            self._sequence += 1
            now = datetime.now(timezone.utc)
            task = DurableQueryTask(
                id=self._sequence,
                task_id=task_id,
                user_id=user_id,
                question=question,
                top_k=top_k,
                status="RUNNING",
                request_json=to_json(request_payload),
                attempt=0,
                created_at=now,
                updated_at=now,
                expires_at=now + timedelta(seconds=positive_seconds("RAG_QUERY_TASK_TTL_SECONDS", 1800)),
            )
            self._tasks[(task_id, user_id)] = task
            return task

    def get(self, task_id: str, user_id: str) -> DurableQueryTask | None:
        self.expire_due()
        with self._lock:
            return self._tasks.get((task_id, user_id))

    def claim(self, *, worker_id: str, batch_size: int, lease_seconds: int) -> list[DurableQueryTask]:
        del worker_id, lease_seconds
        self.expire_due()
        with self._lock:
            candidates = [item for item in self._tasks.values() if item.status in {"RUNNING", "REQUESTED"}]
            claimed: list[DurableQueryTask] = []
            for task in candidates[: max(1, batch_size)]:
                updated = replace(task, status="RUNNING", attempt=task.attempt + 1, updated_at=datetime.now(timezone.utc))
                self._tasks[(task.task_id, task.user_id)] = updated
                claimed.append(updated)
            return claimed

    def append_progress(self, task_id: str, user_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            task = self._tasks.get((task_id, user_id))
            if task is None or task.status != "RUNNING" or is_expired(task.expires_at):
                return
            events = parse_json_list(task.progress_events_json)
            events.append(redact_progress_event(event))
            self._tasks[(task_id, user_id)] = replace(task, progress_events_json=to_json(events[-100:]), updated_at=datetime.now(timezone.utc))

    def complete(self, task_id: str, user_id: str, response: dict[str, Any], duration_ms: int) -> None:
        with self._lock:
            task = self._tasks.get((task_id, user_id))
            if task is None or task.status != "RUNNING" or is_expired(task.expires_at):
                return
            evidences = response.get("evidences") if isinstance(response.get("evidences"), list) else []
            diagnostics = response.get("diagnostics") if isinstance(response.get("diagnostics"), dict) else {}
            self._tasks[(task_id, user_id)] = replace(
                task,
                status="COMPLETED",
                answer=str(response.get("answer") or ""),
                evidence_count=len(evidences),
                expanded_queries_json=to_json(response.get("expandedQueries") if isinstance(response.get("expandedQueries"), list) else []),
                evidences_json=to_json(evidences),
                diagnostics_json=to_json({**diagnostics, "answerGuard": answer_guard(response)}),
                progress_events_json=(
                    task.progress_events_json
                    if parse_json_list(task.progress_events_json)
                    else to_json([redact_progress_event(item) for item in response.get("progressEvents", [])][-100:])
                ),
                duration_ms=max(0, int(duration_ms)),
                updated_at=datetime.now(timezone.utc),
            )

    def fail(self, task_id: str, user_id: str, error_message: str, duration_ms: int) -> None:
        with self._lock:
            task = self._tasks.get((task_id, user_id))
            if task is None or task.status != "RUNNING" or is_expired(task.expires_at):
                return
            self._tasks[(task_id, user_id)] = replace(
                task,
                status="FAILED",
                error_message=truncate(error_message or "RAG 查询失败", 1000),
                duration_ms=max(0, int(duration_ms)),
                updated_at=datetime.now(timezone.utc),
            )

    def expire_due(self) -> int:
        now = datetime.now(timezone.utc)
        expired = 0
        with self._lock:
            for key, task in list(self._tasks.items()):
                if task.status in {"RUNNING", "REQUESTED"} and task.expires_at and task.expires_at <= now:
                    self._tasks[key] = replace(task, status="EXPIRED", error_message="RAG 查询任务已过期", updated_at=now)
                    expired += 1
        return expired


def build_query_task_repository() -> RagQueryTaskRepositoryProtocol:
    """生产优先使用 PostgreSQL；内存仅用于显式测试/内存 RAG 降级。"""
    if resolve_database_url():
        return PostgresRagQueryTaskRepository()
    if allow_memory_fallback():
        return InMemoryRagQueryTaskRepository()
    raise RuntimeError("RAG 查询耐久任务需要 PostgreSQL；测试可设置 RAG_TASK_ALLOW_MEMORY_FALLBACK=true")


def resolve_database_url() -> str:
    return (
        os.getenv("RAG_CONTROL_DATABASE_URL", "").strip()
        or os.getenv("RAG_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
    )


def allow_memory_fallback() -> bool:
    configured = os.getenv("RAG_TASK_ALLOW_MEMORY_FALLBACK")
    if configured is not None and configured.strip():
        return configured.strip().lower() in {"1", "true", "yes", "y", "on"}
    return os.getenv("RAG_STORE_BACKEND", "").strip().lower() == "memory" or "PYTEST_CURRENT_TEST" in os.environ


def answer_guard(response: dict[str, Any]) -> dict[str, Any]:
    return {
        "answerStatus": response.get("answerStatus") or "REFUSED",
        "refusalReason": response.get("refusalReason"),
        "refusalPolicy": response.get("refusalPolicy") or "STRICT_EVIDENCE_GUARD_V1",
        "confidence": response.get("confidence") or 0.0,
        "supportingEvidenceIds": response.get("supportingEvidenceIds") or [],
        "refusalMessage": response.get("refusalMessage"),
    }


def redact_progress_event(event: object) -> dict[str, Any]:
    """进度模型没有正文，但仍按白名单持久化以防上游扩展泄漏。"""
    source = event if isinstance(event, dict) else {}
    return {
        key: source.get(key)
        for key in (
            "stageCode", "stageLabel", "message", "status", "currentStep", "totalSteps",
            "currentChunk", "totalChunks", "chunkId", "blockId", "percent", "detail", "createdAt",
        )
        if source.get(key) is not None
    }


def parse_json_list(value: object) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def to_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def positive_seconds(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def is_expired(value: object) -> bool:
    """兼容 PostgreSQL 返回的有/无时区时间，统一按 UTC 判断任务是否到期。"""
    if not isinstance(value, datetime):
        return False
    target = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return target <= datetime.now(timezone.utc)


def truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]
