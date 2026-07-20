"""RAG 公开控制面使用的 PostgreSQL 仓储。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import os
import re
from typing import Any, Protocol
from uuid import uuid4

from rag.kafka.producer import build_envelope


DEFAULT_SCHEMA = "learning_evidence"
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class MaterialRecord:
    """`learning_material` 中公开控制面需要的字段。"""

    id: int
    title: str
    user_id: str
    document_type: str
    source: str | None
    status: str
    parser: str | None
    document_summary: str | None
    chunk_count: int
    original_filename: str | None
    original_file_path: str | None
    storage_type: str | None
    object_key: str | None
    public_url: str | None
    active_index_job_id: str | None
    index_request_version: int
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class QueryHistoryRecord:
    """`rag_query_history` 的持久化查询快照。"""

    id: int
    user_id: str
    task_id: str | None
    question: str
    answer: str | None
    status: str
    top_k: int
    evidence_count: int
    expanded_queries_json: str
    evidences_json: str
    diagnostics_json: str
    progress_events_json: str
    error_message: str | None
    duration_ms: int | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ProgressLogRecord:
    """从 `log_event` 读取并转为公开进度所需的字段。"""

    stage: str | None
    message: str | None
    success: bool | None
    context_json: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class IndexJobSchedule:
    """控制面创建索引任务后返回的当前资料快照。"""

    material: MaterialRecord
    job_id: str
    delivery_mode: str


class RagControlTransaction(Protocol):
    """公开 RAG service 对事务层的最小依赖，支持测试替身。"""

    def overview(self, user_id: str) -> tuple[int, int, str | None]: ...

    def list_materials(self, user_id: str, limit: int) -> list[MaterialRecord]: ...

    def find_material(self, material_id: int, user_id: str) -> MaterialRecord | None: ...

    def insert_material(
        self,
        *,
        title: str,
        user_id: str,
        document_type: str,
        source: str,
        status: str,
        original_filename: str | None,
        original_file_path: str | None,
        storage_type: str,
        object_key: str | None,
        public_url: str | None,
    ) -> MaterialRecord: ...

    def update_material_status(self, material_id: int, status: str) -> MaterialRecord | None: ...

    def update_material_storage(
        self,
        material_id: int,
        *,
        original_file_path: str,
        storage_type: str,
        object_key: str | None,
        public_url: str | None,
    ) -> MaterialRecord | None: ...

    def update_material_result(
        self,
        material_id: int,
        *,
        status: str,
        parser: str | None,
        document_summary: str | None,
        chunk_count: int,
    ) -> MaterialRecord | None: ...

    def enqueue_index_job(
        self,
        *,
        material: MaterialRecord,
        operation: str,
        status: str,
        high_precision: bool,
        source_ref: dict[str, Any],
        text: str | None,
    ) -> IndexJobSchedule: ...

    def list_progress(self, material_id: int, limit: int) -> list[ProgressLogRecord]: ...

    def insert_query_history(
        self,
        *,
        user_id: str,
        task_id: str | None,
        question: str,
        status: str,
        top_k: int,
        answer: str | None = None,
        evidence_count: int = 0,
        expanded_queries: list[str] | None = None,
        evidences: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
        progress_events: list[dict[str, Any]] | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
    ) -> QueryHistoryRecord: ...

    def find_query_history(self, task_id: str, user_id: str) -> QueryHistoryRecord | None: ...

    def list_query_history(
        self,
        user_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
        limit: int,
    ) -> list[QueryHistoryRecord]: ...

    def update_query_history(
        self,
        *,
        task_id: str,
        user_id: str,
        answer: str | None,
        status: str,
        evidence_count: int,
        expanded_queries: list[str],
        evidences: list[dict[str, Any]],
        diagnostics: dict[str, Any],
        progress_events: list[dict[str, Any]],
        error_message: str | None,
        duration_ms: int | None,
    ) -> QueryHistoryRecord | None: ...


class RagControlRepositoryProtocol(Protocol):
    """方便 service 在单元测试中注入内存仓储。"""

    def transaction(self) -> Iterator[RagControlTransaction]: ...


class DatabaseRagControlTransaction:
    """单个 PostgreSQL 事务中的 RAG 资料和历史操作。"""

    def __init__(self, cursor: Any, schema: str) -> None:
        self._cursor = cursor
        self._schema = schema

    def overview(self, user_id: str) -> tuple[int, int, str | None]:
        """读取当前用户的资料、切块与最近标题统计。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT COUNT(1) AS material_count, COALESCE(SUM(chunk_count), 0) AS chunk_count
                FROM {schema}.learning_material
                WHERE user_id = %s
                """
            ),
            (user_id,),
        )
        counts = self._cursor.fetchone() or {}
        self._cursor.execute(
            self._statement(
                """
                SELECT title
                FROM {schema}.learning_material
                WHERE user_id = %s
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """
            ),
            (user_id,),
        )
        latest = self._cursor.fetchone()
        return (
            int(counts.get("material_count") or 0),
            int(counts.get("chunk_count") or 0),
            str(latest["title"]) if latest and latest.get("title") is not None else None,
        )

    def list_materials(self, user_id: str, limit: int) -> list[MaterialRecord]:
        """按更新时间倒序读取用户资料。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT *
                FROM {schema}.learning_material
                WHERE user_id = %s
                ORDER BY updated_at DESC, id DESC
                LIMIT %s
                """
            ),
            (user_id, limit),
        )
        return [self._to_material(row) for row in self._cursor.fetchall()]

    def find_material(self, material_id: int, user_id: str) -> MaterialRecord | None:
        """仅按资料 ID 与所有者联合读取，避免越权。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT *
                FROM {schema}.learning_material
                WHERE id = %s AND user_id = %s
                """
            ),
            (material_id, user_id),
        )
        return self._to_material(self._cursor.fetchone())

    def enqueue_index_job(
        self,
        *,
        material: MaterialRecord,
        operation: str,
        status: str,
        high_precision: bool,
        source_ref: dict[str, Any],
        text: str | None,
    ) -> IndexJobSchedule:
        """同一事务写入 active job、索引任务和 Kafka Outbox 或 local worker 任务。"""
        job_id = "job_" + uuid4().hex
        canonical_document_id = f"material-{material.id}"
        staging_document_id = f"{canonical_document_id}__job-{job_id}"
        request_version = max(0, material.index_request_version) + 1
        delivery_mode = "KAFKA" if kafka_enabled() else "LOCAL"
        idempotency_key = f"RAG_INDEX:{canonical_document_id}:{job_id}:v1"
        payload: dict[str, Any] = {
            "jobId": job_id,
            "operation": operation,
            "materialId": material.id,
            "canonicalDocumentId": canonical_document_id,
            "stagingDocumentId": staging_document_id,
            "userId": material.user_id,
            "title": material.title,
            "documentType": material.document_type,
            "source": material.source or "upload",
            "visibilityScope": "private",
            "stagingVisibilityScope": "staging",
            "highPrecision": bool(high_precision),
            "requestVersion": request_version,
            "sourceRef": source_ref,
        }
        if text is not None:
            payload["text"] = text
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_material
                SET active_index_job_id = %s,
                    index_request_version = %s,
                    status = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *
                """
            ),
            (job_id, request_version, status, material.id),
        )
        scheduled_material = self._to_material(self._cursor.fetchone())
        if scheduled_material is None:
            raise RuntimeError("创建 RAG 索引任务时未找到资料")
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.rag_index_job (
                    id, material_id, canonical_document_id, staging_document_id, user_id,
                    operation, status, request_version, idempotency_key, attempt,
                    request_json, result_json, delivery_mode, next_attempt_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'REQUESTED', %s, %s, 0, %s, '{{}}', %s, CURRENT_TIMESTAMP)
                """
            ),
            (
                job_id,
                material.id,
                canonical_document_id,
                staging_document_id,
                material.user_id,
                operation,
                request_version,
                idempotency_key,
                to_json(payload),
                delivery_mode,
            ),
        )
        if delivery_mode == "KAFKA":
            envelope = build_envelope(
                message_type="RAG_INDEX_REQUESTED",
                partition_key=canonical_document_id,
                idempotency_key=idempotency_key,
                payload=payload,
            )
            self._cursor.execute(
                self._statement(
                    """
                    INSERT INTO {schema}.rag_outbox_event (
                        topic, message_key, event_type, idempotency_key, payload_json,
                        status, attempt, next_attempt_at
                    )
                    VALUES (%s, %s, 'RAG_INDEX_REQUESTED', %s, %s, 'NEW', 0, CURRENT_TIMESTAMP)
                    ON CONFLICT (topic, idempotency_key) DO NOTHING
                    """
                ),
                (
                    os.getenv("RAG_KAFKA_TOPIC_INDEX_REQUEST", "rag.material.index.request.v1"),
                    canonical_document_id,
                    idempotency_key,
                    to_json(envelope.model_dump(mode="json")),
                ),
            )
        return IndexJobSchedule(material=scheduled_material, job_id=job_id, delivery_mode=delivery_mode)

    def insert_material(
        self,
        *,
        title: str,
        user_id: str,
        document_type: str,
        source: str,
        status: str,
        original_filename: str | None,
        original_file_path: str | None,
        storage_type: str,
        object_key: str | None,
        public_url: str | None,
    ) -> MaterialRecord:
        """创建待处理资料记录，并返回数据库分配的主键。"""
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.learning_material (
                    title, user_id, document_type, source, status, chunk_count,
                    original_filename, original_file_path, storage_type, object_key, public_url
                )
                VALUES (%s, %s, %s, %s, %s, 0, %s, %s, %s, %s, %s)
                RETURNING *
                """
            ),
            (
                title,
                user_id,
                document_type,
                source,
                status,
                original_filename,
                original_file_path,
                storage_type,
                object_key,
                public_url,
            ),
        )
        row = self._cursor.fetchone()
        if row is None:
            raise RuntimeError("创建学习资料失败")
        return self._to_material(row)

    def update_material_status(self, material_id: int, status: str) -> MaterialRecord | None:
        """更新资料状态，索引前后均保留同一个资料 ID。"""
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_material
                SET status = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *
                """
            ),
            (status, material_id),
        )
        return self._to_material(self._cursor.fetchone())

    def update_material_storage(
        self,
        material_id: int,
        *,
        original_file_path: str,
        storage_type: str,
        object_key: str | None,
        public_url: str | None,
    ) -> MaterialRecord | None:
        """补写对象存储来源，供预览和重建索引使用。"""
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_material
                SET original_file_path = %s,
                    storage_type = %s,
                    object_key = %s,
                    public_url = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *
                """
            ),
            (original_file_path, storage_type, object_key, public_url, material_id),
        )
        return self._to_material(self._cursor.fetchone())

    def update_material_result(
        self,
        material_id: int,
        *,
        status: str,
        parser: str | None,
        document_summary: str | None,
        chunk_count: int,
    ) -> MaterialRecord | None:
        """回写 Python 解析结果与可检索切块数量。"""
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_material
                SET status = %s,
                    parser = %s,
                    document_summary = %s,
                    chunk_count = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                RETURNING *
                """
            ),
            (status, parser, document_summary, chunk_count, material_id),
        )
        return self._to_material(self._cursor.fetchone())

    def list_progress(self, material_id: int, limit: int) -> list[ProgressLogRecord]:
        """读取最近进度事件，日志不存在时由调用方安全降级。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT stage, message, success, context_json, created_at
                FROM {schema}.log_event
                WHERE material_id = %s AND event_type = 'rag_progress'
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """
            ),
            (material_id, limit),
        )
        return [
            ProgressLogRecord(
                stage=row.get("stage"),
                message=row.get("message"),
                success=row.get("success"),
                context_json=row.get("context_json"),
                created_at=row.get("created_at"),
            )
            for row in self._cursor.fetchall()
        ]

    def insert_query_history(
        self,
        *,
        user_id: str,
        task_id: str | None,
        question: str,
        status: str,
        top_k: int,
        answer: str | None = None,
        evidence_count: int = 0,
        expanded_queries: list[str] | None = None,
        evidences: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
        progress_events: list[dict[str, Any]] | None = None,
        error_message: str | None = None,
        duration_ms: int | None = None,
    ) -> QueryHistoryRecord:
        """创建同步或异步查询的持久化快照。"""
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.rag_query_history (
                    user_id, task_id, question, answer, status, top_k, evidence_count,
                    expanded_queries_json, evidences_json, diagnostics_json, progress_events_json,
                    error_message, duration_ms
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """
            ),
            (
                user_id,
                task_id,
                question,
                answer,
                status,
                top_k,
                evidence_count,
                to_json(expanded_queries or []),
                to_json(evidences or []),
                to_json(diagnostics or {}),
                to_json(progress_events or []),
                error_message,
                duration_ms,
            ),
        )
        row = self._cursor.fetchone()
        if row is None:
            raise RuntimeError("创建 RAG 查询历史失败")
        return self._to_history(row)

    def find_query_history(self, task_id: str, user_id: str) -> QueryHistoryRecord | None:
        """读取指定用户的任务历史，任务 ID 不可跨用户使用。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT *
                FROM {schema}.rag_query_history
                WHERE task_id = %s AND user_id = %s
                """
            ),
            (task_id, user_id),
        )
        return self._to_history(self._cursor.fetchone())

    def list_query_history(
        self,
        user_id: str,
        *,
        start_at: datetime,
        end_at: datetime,
        limit: int,
    ) -> list[QueryHistoryRecord]:
        """读取当前用户日期范围内的最近查询。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT *
                FROM {schema}.rag_query_history
                WHERE user_id = %s AND created_at >= %s AND created_at < %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """
            ),
            (user_id, start_at, end_at, limit),
        )
        return [self._to_history(row) for row in self._cursor.fetchall()]

    def update_query_history(
        self,
        *,
        task_id: str,
        user_id: str,
        answer: str | None,
        status: str,
        evidence_count: int,
        expanded_queries: list[str],
        evidences: list[dict[str, Any]],
        diagnostics: dict[str, Any],
        progress_events: list[dict[str, Any]],
        error_message: str | None,
        duration_ms: int | None,
    ) -> QueryHistoryRecord | None:
        """按 task ID 回写任务结果，确保归属条件在 SQL 中生效。"""
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.rag_query_history
                SET answer = %s,
                    status = %s,
                    evidence_count = %s,
                    expanded_queries_json = %s,
                    evidences_json = %s,
                    diagnostics_json = %s,
                    progress_events_json = %s,
                    error_message = %s,
                    duration_ms = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE task_id = %s AND user_id = %s
                RETURNING *
                """
            ),
            (
                answer,
                status,
                evidence_count,
                to_json(expanded_queries),
                to_json(evidences),
                to_json(diagnostics),
                to_json(progress_events),
                error_message,
                duration_ms,
                task_id,
                user_id,
            ),
        )
        return self._to_history(self._cursor.fetchone())

    def _statement(self, query: str) -> Any:
        """使用 psycopg 标识符拼接 schema，拒绝来自配置的 SQL 注入。"""
        from psycopg import sql

        return sql.SQL(query).format(schema=sql.Identifier(self._schema))

    @staticmethod
    def _to_material(row: dict[str, Any] | None) -> MaterialRecord | None:
        if row is None:
            return None
        return MaterialRecord(
            id=int(row["id"]),
            title=str(row["title"]),
            user_id=str(row["user_id"]),
            document_type=str(row["document_type"]),
            source=row.get("source"),
            status=str(row["status"]),
            parser=row.get("parser"),
            document_summary=row.get("document_summary"),
            chunk_count=int(row.get("chunk_count") or 0),
            original_filename=row.get("original_filename"),
            original_file_path=row.get("original_file_path"),
            storage_type=row.get("storage_type"),
            object_key=row.get("object_key"),
            public_url=row.get("public_url"),
            active_index_job_id=row.get("active_index_job_id"),
            index_request_version=int(row.get("index_request_version") or 0),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _to_history(row: dict[str, Any] | None) -> QueryHistoryRecord | None:
        if row is None:
            return None
        return QueryHistoryRecord(
            id=int(row["id"]),
            user_id=str(row["user_id"]),
            task_id=row.get("task_id"),
            question=str(row["question"]),
            answer=row.get("answer"),
            status=str(row["status"]),
            top_k=int(row.get("top_k") or 5),
            evidence_count=int(row.get("evidence_count") or 0),
            expanded_queries_json=str(row.get("expanded_queries_json") or "[]"),
            evidences_json=str(row.get("evidences_json") or "[]"),
            diagnostics_json=str(row.get("diagnostics_json") or "{}"),
            progress_events_json=str(row.get("progress_events_json") or "[]"),
            error_message=row.get("error_message"),
            duration_ms=row.get("duration_ms"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


class RagControlRepository:
    """通过 psycopg 管理 RAG 公开控制面的事务。"""

    def __init__(self, database_url: str | None = None, schema: str | None = None) -> None:
        self._database_url = database_url or resolve_database_url()
        self._schema = validate_schema(schema or os.getenv("RAG_DATABASE_SCHEMA", DEFAULT_SCHEMA))

    @contextmanager
    def transaction(self) -> Iterator[RagControlTransaction]:
        """打开一个提交或回滚一致的 PostgreSQL 事务。"""
        connection = self._connect()
        try:
            with connection:
                with connection.cursor() as cursor:
                    yield DatabaseRagControlTransaction(cursor, self._schema)
        finally:
            connection.close()

    def _connect(self) -> Any:
        """延迟导入 psycopg，测试替身不需要数据库驱动或连接。"""
        if not self._database_url:
            raise RuntimeError("未配置 RAG_CONTROL_DATABASE_URL、RAG_DATABASE_URL 或 DATABASE_URL")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("RAG 控制面仓储需要安装 psycopg[binary]") from exc
        return psycopg.connect(self._database_url, row_factory=dict_row)


def resolve_database_url() -> str:
    """按控制面专用、RAG 复用、通用数据库顺序读取连接串。"""
    return (
        os.getenv("RAG_CONTROL_DATABASE_URL", "").strip()
        or os.getenv("RAG_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
    )


def validate_schema(value: str) -> str:
    """只允许合法简单 PostgreSQL schema 名称。"""
    if not SCHEMA_PATTERN.fullmatch(value):
        raise RuntimeError("RAG_DATABASE_SCHEMA 必须是合法的 PostgreSQL schema 标识符")
    return value


def to_json(value: object) -> str:
    """以 UTF-8 友好方式保存结构化快照。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def kafka_enabled() -> bool:
    """索引任务的唯一投递通道由 Python 运行配置决定。"""
    return os.getenv("RAG_KAFKA_ENABLED", "false").strip().lower() in {"1", "true", "yes", "y", "on"}
