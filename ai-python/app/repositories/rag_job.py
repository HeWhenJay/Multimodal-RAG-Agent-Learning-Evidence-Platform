"""RAG 索引任务、Kafka 消费去重和 PostgreSQL 终态回写。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
from typing import Any

from app.schemas.kafka import KafkaEnvelope
from rag.kafka.producer import build_envelope


SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TERMINAL_JOB_STATUSES = {"SUCCEEDED", "FAILED", "DLQ", "STALE_IGNORED"}
TERMINAL_MATERIAL_STATUSES = {"READY", "PARTIAL", "FAILED"}


@dataclass(frozen=True)
class RagIndexJobRecord:
    """本地耐久 worker 抢占到的一条索引任务。"""

    id: str
    material_id: int
    user_id: str
    operation: str
    request_version: int
    request_json: str
    attempt: int
    delivery_mode: str


@dataclass(frozen=True)
class IndexExecutionClaim:
    """Kafka 索引执行权的原子抢占结果。"""

    status: str
    retry_after_seconds: float = 0.0

    @property
    def acquired(self) -> bool:
        return self.status == "ACQUIRED"


class RagJobRepository:
    """以资料当前 active job 防止迟到结果覆盖新版本索引。"""

    def __init__(self, database_url: str | None = None, schema: str | None = None) -> None:
        self.database_url = database_url or os.getenv("RAG_DATABASE_URL") or os.getenv("DATABASE_URL", "")
        self.schema = schema or os.getenv("RAG_DATABASE_SCHEMA", "learning_evidence")
        if not self.database_url:
            raise RuntimeError("未配置 RAG_DATABASE_URL 或 DATABASE_URL")
        if not SCHEMA_PATTERN.fullmatch(self.schema):
            raise RuntimeError("RAG_DATABASE_SCHEMA 必须是合法的 PostgreSQL schema 标识符")

    def is_active(self, material_id: int, job_id: str, request_version: int) -> bool:
        """仅当资料当前任务和请求版本均匹配时允许 staging 提升。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        SELECT active_index_job_id, index_request_version
                        FROM {schema}.learning_material
                        WHERE id = %s
                        """
                    ),
                    (material_id,),
                )
                row: dict[str, Any] | None = cursor.fetchone()
        return self._is_active_row(row, job_id, request_version)

    def claim_index_execution(
        self,
        material_id: int,
        job_id: str,
        request_version: int,
        *,
        execution_owner: str,
        lease_seconds: int,
        delivery_attempt: int = 1,
    ) -> IndexExecutionClaim:
        """原子抢占 Kafka 索引执行权，租约内的重复投递不得重复建索引。"""
        now = datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=max(10, int(lease_seconds)))
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            SELECT m.active_index_job_id,
                                   m.index_request_version AS material_request_version,
                                   j.status AS job_status,
                                   j.request_version AS job_request_version,
                                   j.delivery_mode,
                                   j.next_attempt_at,
                                   j.lease_until,
                                   j.locked_by
                            FROM {schema}.learning_material m
                            JOIN {schema}.rag_index_job j ON j.material_id = m.id
                            WHERE m.id = %s AND j.id = %s
                            FOR UPDATE OF m, j
                            """
                        ),
                        (material_id, job_id),
                    )
                    row: dict[str, Any] | None = cursor.fetchone()
                    if not row or str(row.get("delivery_mode") or "").upper() != "KAFKA":
                        return IndexExecutionClaim("STALE")
                    if (
                        str(row.get("active_index_job_id") or "") != job_id
                        or int(row.get("material_request_version") or 0) != int(request_version)
                        or int(row.get("job_request_version") or 0) != int(request_version)
                    ):
                        self._mark_stale(cursor, job_id, "过期索引请求已忽略")
                        return IndexExecutionClaim("STALE")

                    status = str(row.get("job_status") or "")
                    if status == "REQUESTED":
                        retry_after = seconds_until(row.get("next_attempt_at"), now)
                        if retry_after > 0:
                            return IndexExecutionClaim("BUSY", retry_after)
                    elif status == "RUNNING":
                        retry_after = seconds_until(row.get("lease_until"), now)
                        if retry_after > 0:
                            return IndexExecutionClaim("BUSY", retry_after)
                    else:
                        return IndexExecutionClaim("STALE")

                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_index_job
                            SET status = 'RUNNING',
                                attempt = GREATEST(attempt, %s),
                                started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                                lease_until = %s,
                                locked_by = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                            """
                        ),
                        (max(1, int(delivery_attempt)), lease_until, execution_owner[:120], job_id),
                    )
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.learning_material
                            SET status = CASE
                                WHEN status IN ('PENDING', 'REINDEXING') THEN 'PARSING'
                                ELSE status
                            END,
                            updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                              AND active_index_job_id = %s
                            """
                        ),
                        (material_id, job_id),
                    )
        return IndexExecutionClaim("ACQUIRED")

    def owns_index_execution(self, job_id: str, execution_owner: str) -> bool:
        """确认执行令牌仍持有未过期租约，并且 job 仍是资料当前版本。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._sql(
                        """
                        SELECT 1
                        FROM {schema}.rag_index_job j
                        JOIN {schema}.learning_material m ON m.id = j.material_id
                        WHERE j.id = %s
                          AND j.status = 'RUNNING'
                          AND j.locked_by = %s
                          AND j.lease_until > CURRENT_TIMESTAMP
                          AND m.active_index_job_id = j.id
                          AND m.index_request_version = j.request_version
                        """
                    ),
                    (job_id, execution_owner[:120]),
                )
                return cursor.fetchone() is not None

    def renew_index_execution(self, job_id: str, execution_owner: str, lease_seconds: int) -> bool:
        """仅允许尚未过期的当前执行令牌续租，过期后必须重新抢占。"""
        lease_until = datetime.now(timezone.utc) + timedelta(seconds=max(10, int(lease_seconds)))
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_index_job j
                            SET lease_until = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE j.id = %s
                              AND j.status = 'RUNNING'
                              AND j.locked_by = %s
                              AND j.lease_until > CURRENT_TIMESTAMP
                              AND EXISTS (
                                  SELECT 1
                                  FROM {schema}.learning_material m
                                  WHERE m.id = j.material_id
                                    AND m.active_index_job_id = j.id
                                    AND m.index_request_version = j.request_version
                              )
                            RETURNING j.id
                            """
                        ),
                        (lease_until, job_id, execution_owner[:120]),
                    )
                    return cursor.fetchone() is not None

    def reschedule_index_execution(
        self,
        job_id: str,
        execution_owner: str,
        *,
        not_before: datetime,
        error_message: str,
    ) -> bool:
        """执行令牌持有者发送重试消息后释放任务，迟到 worker 无权改写任务。"""
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_index_job
                            SET status = 'REQUESTED',
                                next_attempt_at = %s,
                                lease_until = NULL,
                                locked_by = NULL,
                                error_code = 'RAG_TRANSIENT_INDEX_ERROR',
                                error_message = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                              AND status = 'RUNNING'
                              AND locked_by = %s
                            RETURNING id
                            """
                        ),
                        (not_before, truncate(error_message, 1000), job_id, execution_owner[:120]),
                    )
                    return cursor.fetchone() is not None

    def mark_stale_execution(self, job_id: str, execution_owner: str, reason: str) -> bool:
        """仅把仍由当前令牌持有、但已不再 active 的任务收敛为 stale。"""
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_index_job
                            SET status = 'STALE_IGNORED',
                                error_message = %s,
                                finished_at = CURRENT_TIMESTAMP,
                                lease_until = NULL,
                                locked_by = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                              AND status = 'RUNNING'
                              AND locked_by = %s
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM {schema}.learning_material m
                                  WHERE m.id = rag_index_job.material_id
                                    AND m.active_index_job_id = rag_index_job.id
                                    AND m.index_request_version = rag_index_job.request_version
                              )
                            RETURNING id
                            """
                        ),
                        (truncate(reason, 1000), job_id, execution_owner[:120]),
                    )
                    return cursor.fetchone() is not None

    def update_remote_material_source(
        self,
        material_id: int,
        job_id: str,
        request_version: int,
        *,
        execution_owner: str | None = None,
        title: str,
        filename: str,
        source_url: str,
        platform: str,
    ) -> bool:
        """下载元数据只允许写回当前 active job 对应的资料。"""
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    material = self._find_material_for_update(cursor, material_id)
                    if not self._is_active_row(material, job_id, request_version):
                        return False
                    if execution_owner:
                        job = self._find_job_for_update(cursor, job_id)
                        if not self._execution_owned_by(job, execution_owner, require_unexpired=True):
                            return False
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.learning_material
                            SET title = %s,
                                source = %s,
                                original_filename = %s,
                                original_file_path = NULL,
                                storage_type = 'remote',
                                object_key = NULL,
                                public_url = %s,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                              AND active_index_job_id = %s
                              AND index_request_version = %s
                            """
                        ),
                        (title[:255], platform[:50], filename[:255], source_url, material_id, job_id, request_version),
                    )
        return True

    def claim_local_jobs(
        self,
        *,
        worker_id: str,
        batch_size: int = 4,
        lease_seconds: int = 120,
    ) -> list[RagIndexJobRecord]:
        """以租约抢占 Kafka 关闭时的 `LOCAL` 索引任务，支持进程崩溃恢复。"""
        safe_batch = max(1, min(int(batch_size), 100))
        safe_lease = max(10, int(lease_seconds))
        now = datetime.now(timezone.utc)
        lease_until = now + timedelta(seconds=safe_lease)
        claimed: list[RagIndexJobRecord] = []
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(
                        self._sql(
                            """
                            SELECT *
                            FROM {schema}.rag_index_job
                            WHERE delivery_mode = 'LOCAL'
                              AND (
                                (status = 'REQUESTED' AND next_attempt_at <= %s)
                                OR (status = 'RUNNING' AND lease_until <= %s)
                              )
                            ORDER BY requested_at ASC, id ASC
                            LIMIT %s
                            FOR UPDATE SKIP LOCKED
                            """
                        ),
                        (now, now, safe_batch),
                    )
                    for row in cursor.fetchall():
                        cursor.execute(
                            self._sql(
                                """
                                UPDATE {schema}.rag_index_job
                                SET status = 'RUNNING',
                                    attempt = attempt + 1,
                                    started_at = COALESCE(started_at, CURRENT_TIMESTAMP),
                                    lease_until = %s,
                                    locked_by = %s,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                                """
                            ),
                            (lease_until, worker_id, row["id"]),
                        )
                        claimed.append(
                            RagIndexJobRecord(
                                id=str(row["id"]),
                                material_id=int(row["material_id"]),
                                user_id=str(row["user_id"]),
                                operation=str(row["operation"]),
                                request_version=int(row["request_version"]),
                                request_json=str(row.get("request_json") or "{}"),
                                attempt=int(row.get("attempt") or 0) + 1,
                                delivery_mode=str(row.get("delivery_mode") or "LOCAL"),
                            )
                        )
        return claimed

    def reschedule_local_job(
        self,
        job_id: str,
        *,
        worker_id: str,
        not_before: datetime,
        error_message: str,
    ) -> bool:
        """将可恢复的本地索引失败放回耐久队列，不在请求进程中重试。"""
        return self.reschedule_index_execution(
            job_id,
            worker_id,
            not_before=not_before,
            error_message=error_message,
        )

    def renew_local_job_lease(self, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        """由长视频任务心跳续租，避免多实例重复抢占仍在执行的任务。"""
        return self.renew_index_execution(job_id, worker_id, lease_seconds)

    def consume_progress(self, envelope: KafkaEnvelope) -> bool:
        """消费 Python index progress，去重后写入日志并保护终态资料。"""
        payload = dict(envelope.payload or {})
        job_id = nullable_text(payload.get("jobId"))
        material_id = as_int(payload.get("materialId"))
        request_version = as_int(payload.get("requestVersion"))
        progress_sequence = as_int(payload.get("progressSequence"))
        execution_owner = nullable_text(payload.get("executionOwner"))
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    material = self._find_material_for_update(cursor, material_id) if material_id is not None else None
                    if not (material and job_id and request_version is not None and self._is_active_row(material, job_id, request_version)):
                        # 旧 job 的迟到 progress 不得覆盖当前资料的用户可见进度。
                        return True
                    if execution_owner:
                        job = self._find_job_for_update(cursor, job_id)
                        if not self._execution_owned_by(job, execution_owner, require_unexpired=True):
                            return True
                    if not self._remember_consumed(cursor, "rag-python-progress-writers", envelope, job_id, progress_sequence):
                        return False
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.learning_material
                            SET status = CASE
                                WHEN status IN ('PENDING', 'REINDEXING') THEN 'PARSING'
                                ELSE status
                            END,
                            updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                              AND active_index_job_id = %s
                            """
                        ),
                        (material_id, job_id),
                    )
                    self._insert_progress_log(cursor, payload, material_id)
        return True

    def consume_index_result(self, envelope: KafkaEnvelope) -> dict[str, Any] | None:
        """消费 staging 索引结果；当前 job 才能创建 promote 任务。"""
        payload = dict(envelope.payload or {})
        job_id = nullable_text(payload.get("jobId"))
        material_id = as_int(payload.get("materialId"))
        request_version = as_int(payload.get("requestVersion"))
        execution_owner = nullable_text(payload.get("executionOwner"))
        if not job_id or material_id is None or request_version is None:
            raise ValueError("RAG 索引结果缺少 jobId、materialId 或 requestVersion")
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    material = self._find_material_for_update(cursor, material_id)
                    job = self._find_job_for_update(cursor, job_id)
                    if not self._is_active_job(material, job, request_version):
                        self._mark_stale(cursor, job_id, "过期索引结果已忽略")
                        return None
                    # 同一 job 租约过期后可被新令牌接管，旧令牌结果只能丢弃，不能把新执行标成 stale。
                    if not self._execution_result_allowed(job, execution_owner):
                        return None
                    if not self._remember_consumed(cursor, "rag-python-result-writers", envelope, job_id, None):
                        return None
                    serialized = to_json(payload)
                    if str(payload.get("status") or "FAILED") == "FAILED":
                        self._finish_failed_job_and_material(
                            cursor,
                            job_id=job_id,
                            material_id=material_id,
                            error_code=nullable_text(payload.get("errorCode")) or "RAG_KAFKA_INDEX_FAILED",
                            error_message=nullable_text(payload.get("errorMessage")) or "Python staging 索引失败",
                            result_json=serialized,
                            parser="python-rag-error",
                        )
                        return None
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_index_job
                            SET status = 'INDEXED',
                                result_json = %s,
                                error_code = NULL,
                                error_message = NULL,
                                indexed_at = CURRENT_TIMESTAMP,
                                lease_until = NULL,
                                locked_by = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                            """
                        ),
                        (serialized, job_id),
                    )
                    # progress/result 分属不同 topic，终态不能依赖跨 topic 的到达顺序。
                    self._insert_progress_log(
                        cursor,
                        build_index_completed_progress_payload(payload, job),
                        material_id,
                    )
                    promote_payload = {
                        "jobId": job_id,
                        "materialId": material_id,
                        "canonicalDocumentId": str(job["canonical_document_id"]),
                        "stagingDocumentId": str(job["staging_document_id"]),
                        "requestVersion": request_version,
                        "chunkCount": as_int(payload.get("chunkCount")),
                    }
                    if str(job.get("delivery_mode") or "KAFKA").upper() == "KAFKA":
                        self._enqueue_promote_outbox(cursor, promote_payload)
                        return None
                    return promote_payload

    def consume_promote_result(self, envelope: KafkaEnvelope) -> bool:
        """消费 promote 结果，只有仍为 active 的 job 能改变资料终态。"""
        payload = dict(envelope.payload or {})
        job_id = nullable_text(payload.get("jobId"))
        material_id = as_int(payload.get("materialId"))
        request_version = as_int(payload.get("requestVersion"))
        if not job_id or material_id is None or request_version is None:
            raise ValueError("RAG promote 结果缺少 jobId、materialId 或 requestVersion")
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    if not self._remember_consumed(cursor, "rag-python-promote-result-writers", envelope, job_id, None):
                        return False
                    material = self._find_material_for_update(cursor, material_id)
                    job = self._find_job_for_update(cursor, job_id)
                    if not self._is_active_job(material, job, request_version):
                        self._mark_stale(cursor, job_id, "过期提升结果已忽略")
                        return False
                    serialized = to_json(payload)
                    if str(payload.get("status") or "FAILED") != "SUCCEEDED":
                        self._finish_failed_job_and_material(
                            cursor,
                            job_id=job_id,
                            material_id=material_id,
                            error_code=nullable_text(payload.get("errorCode")) or "RAG_PROMOTE_FAILED",
                            error_message=nullable_text(payload.get("errorMessage")) or "Python promote 失败",
                            result_json=serialized,
                            parser="python-rag-promote-error",
                        )
                        return True
                    index_result = parse_json_object(job.get("result_json"))
                    final_status = str(index_result.get("status") or "READY")
                    if final_status not in {"READY", "PARTIAL"}:
                        final_status = "READY"
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.learning_material
                            SET status = %s,
                                parser = %s,
                                document_summary = %s,
                                chunk_count = %s,
                                active_index_job_id = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                              AND active_index_job_id = %s
                            """
                        ),
                        (
                            final_status,
                            nullable_text(index_result.get("parser")),
                            nullable_text(index_result.get("documentSummary")),
                            as_int(index_result.get("chunkCount")) or 0,
                            material_id,
                            job_id,
                        ),
                    )
                    cursor.execute(
                        self._sql(
                            """
                            UPDATE {schema}.rag_index_job
                            SET status = 'SUCCEEDED',
                                result_json = %s,
                                error_code = NULL,
                                error_message = NULL,
                                promoted_at = CURRENT_TIMESTAMP,
                                finished_at = CURRENT_TIMESTAMP,
                                lease_until = NULL,
                                locked_by = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = %s
                            """
                        ),
                        (serialized, job_id),
                    )
        return True

    def consume_dlq(self, envelope: KafkaEnvelope) -> bool:
        """消费终态死信并回写资料失败状态，禁止资料长期停留在解析中。"""
        payload = dict(envelope.payload or {})
        job_id = nullable_text(payload.get("jobId"))
        material_id = as_int(payload.get("materialId"))
        source_topic = nullable_text(payload.get("sourceTopic")) or nullable_text(payload.get("topic"))
        error_code = nullable_text(payload.get("errorCode")) or "RAG_KAFKA_DLQ"
        error_message = nullable_text(payload.get("errorMessage")) or "RAG Kafka 消息进入 DLQ"
        execution_owner = nullable_text(payload.get("executionOwner"))
        with self._connect() as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    if material_id is None and job_id:
                        cursor.execute(
                            self._sql("SELECT material_id FROM {schema}.rag_index_job WHERE id = %s"),
                            (job_id,),
                        )
                        located_job = cursor.fetchone() or {}
                        material_id = as_int(located_job.get("material_id"))
                    material = (
                        self._find_material_for_update(cursor, material_id)
                        if self._is_terminal_dlq_topic(source_topic) and material_id is not None
                        else None
                    )
                    job = self._find_job_for_update(cursor, job_id) if job_id else None
                    if self._is_terminal_dlq_topic(source_topic) and not self._execution_result_allowed(job, execution_owner):
                        return True
                    if not self._remember_consumed(cursor, "rag-python-dlq-writers", envelope, job_id, None):
                        return False
                    if self._is_terminal_dlq_topic(source_topic) and job and material_id is not None:
                        cursor.execute(
                            self._sql(
                                """
                                UPDATE {schema}.rag_index_job
                                SET status = 'DLQ',
                                    result_json = %s,
                                    error_code = %s,
                                    error_message = %s,
                                    finished_at = CURRENT_TIMESTAMP,
                                    lease_until = NULL,
                                    locked_by = NULL,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = %s
                                  AND status NOT IN ('SUCCEEDED', 'STALE_IGNORED')
                                """
                            ),
                            (to_json(payload), error_code, truncate(error_message, 1000), job_id),
                        )
                        if material and str(material.get("active_index_job_id") or "") == job_id:
                            cursor.execute(
                                self._sql(
                                    """
                                    UPDATE {schema}.learning_material
                                    SET status = 'FAILED',
                                        parser = 'kafka-dlq',
                                        document_summary = %s,
                                        chunk_count = 0,
                                        active_index_job_id = NULL,
                                        updated_at = CURRENT_TIMESTAMP
                                    WHERE id = %s
                                      AND active_index_job_id = %s
                                    """
                                ),
                                (truncate(error_message, 500), material_id, job_id),
                            )
                    self._insert_dlq_log(cursor, payload, material_id, error_code, error_message)
        return True

    def _enqueue_promote_outbox(self, cursor: Any, payload: dict[str, Any]) -> None:
        """在索引结果事务内创建 promote Outbox，避免 result 与 promote 之间丢消息。"""
        canonical = str(payload["canonicalDocumentId"])
        job_id = str(payload["jobId"])
        idempotency_key = f"RAG_PROMOTE:{canonical}:{job_id}:v1"
        envelope = build_envelope(
            message_type="RAG_PROMOTE_REQUESTED",
            partition_key=canonical,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        cursor.execute(
            self._sql(
                """
                INSERT INTO {schema}.rag_outbox_event (
                    topic, message_key, event_type, idempotency_key, payload_json,
                    status, attempt, next_attempt_at
                )
                VALUES (%s, %s, 'RAG_PROMOTE_REQUESTED', %s, %s, 'NEW', 0, CURRENT_TIMESTAMP)
                ON CONFLICT (topic, idempotency_key) DO NOTHING
                """
            ),
            (
                os.getenv("RAG_KAFKA_TOPIC_PROMOTE_REQUEST", "rag.material.index.promote.request.v1"),
                canonical,
                idempotency_key,
                to_json(envelope.model_dump(mode="json")),
            ),
        )

    def _finish_failed_job_and_material(
        self,
        cursor: Any,
        *,
        job_id: str,
        material_id: int,
        error_code: str,
        error_message: str,
        result_json: str,
        parser: str,
    ) -> None:
        """同一事务更新失败 job、资料状态和 active job，避免出现幽灵解析中。"""
        cursor.execute(
            self._sql(
                """
                UPDATE {schema}.rag_index_job
                SET status = 'FAILED',
                    result_json = %s,
                    error_code = %s,
                    error_message = %s,
                    finished_at = CURRENT_TIMESTAMP,
                    lease_until = NULL,
                    locked_by = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """
            ),
            (result_json, error_code, truncate(error_message, 1000), job_id),
        )
        cursor.execute(
            self._sql(
                """
                UPDATE {schema}.learning_material
                SET status = 'FAILED',
                    parser = %s,
                    document_summary = %s,
                    chunk_count = 0,
                    active_index_job_id = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND active_index_job_id = %s
                """
            ),
            (parser, truncate(error_message, 500), material_id, job_id),
        )

    def _remember_consumed(
        self,
        cursor: Any,
        consumer_name: str,
        envelope: KafkaEnvelope,
        job_id: str | None,
        progress_sequence: int | None,
    ) -> bool:
        """以消息 ID 与幂等键去重，offset replay 不会重复覆盖终态。"""
        cursor.execute(
            self._sql(
                """
                INSERT INTO {schema}.rag_consumed_event (
                    consumer_name, message_id, message_type, idempotency_key,
                    job_id, progress_sequence, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'CONSUMED')
                ON CONFLICT DO NOTHING
                """
            ),
            (
                consumer_name,
                envelope.messageId,
                envelope.messageType,
                envelope.idempotencyKey,
                job_id,
                progress_sequence,
            ),
        )
        return cursor.rowcount > 0

    def _insert_progress_log(self, cursor: Any, payload: dict[str, Any], material_id: int | None) -> None:
        """进度事件只保存显示字段与任务定位，不把原文写入日志。"""
        safe_context = {
            key: payload.get(key)
            for key in (
                "jobId", "materialId", "canonicalDocumentId", "stagingDocumentId", "requestVersion",
                "progressSequence", "stageCode", "stageLabel", "message", "status", "currentStep",
                "totalSteps", "currentChunk", "totalChunks", "chunkId", "blockId", "percent", "detail", "parser",
            )
            if payload.get(key) is not None
        }
        stage = nullable_text(payload.get("stageCode")) or "kafka.progress"
        action = "kafka_progress_" + re.sub(r"[^A-Za-z0-9_]+", "_", stage).strip("_")
        cursor.execute(
            self._sql(
                """
                INSERT INTO {schema}.log_event (
                    trace_id, user_id, source, domain, level, module, stage, event_type,
                    action, message, success, material_id, document_id, parser, context_json
                )
                VALUES (%s, %s, 'python', 'rag', 'INFO', 'material', %s, 'rag_progress',
                        %s, %s, %s, %s, %s, %s, %s)
                """
            ),
            (
                "kafka_" + hashlib.sha256(str(safe_context).encode("utf-8")).hexdigest()[:24],
                str(payload.get("userId") or "anonymous")[:120],
                stage[:80],
                action[:120] or "kafka_progress",
                (nullable_text(payload.get("message")) or "RAG 索引进度更新")[:500],
                str(payload.get("status") or "RUNNING") != "FAILED",
                material_id,
                nullable_text(payload.get("canonicalDocumentId"))[:120] if nullable_text(payload.get("canonicalDocumentId")) else None,
                nullable_text(payload.get("parser"))[:80] if nullable_text(payload.get("parser")) else None,
                to_json(safe_context),
            ),
        )

    def _insert_dlq_log(
        self,
        cursor: Any,
        payload: dict[str, Any],
        material_id: int | None,
        error_code: str,
        error_message: str,
    ) -> None:
        """以稳定 fingerprint 聚合 DLQ，不写入原始 Kafka 消息或资料正文。"""
        context = {
            key: payload.get(key)
            for key in (
                "jobId", "materialId", "canonicalDocumentId", "requestVersion", "attempt", "topic",
                "sourceTopic", "partition", "offset", "messageHash", "sourceMessageId", "sourceMessageType",
                "sourceIdempotencyKey", "uploadId", "errorCode",
            )
            if payload.get(key) is not None
        }
        identity = "|".join(str(context.get(key) or "") for key in sorted(context))
        fingerprint = hashlib.sha256(("kafka.dlq|" + identity).encode("utf-8")).hexdigest()
        cursor.execute(
            self._sql(
                """
                INSERT INTO {schema}.log_error (
                    trace_id, user_id, source, domain, severity, module, stage, action,
                    error_type, error_code, message, fingerprint, material_id, document_id, context_json
                )
                VALUES (%s, 'anonymous', 'python', 'rag', 'ERROR', 'material', 'kafka.dlq',
                        'rag_kafka_dlq_received', 'RagKafkaDlq', %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fingerprint) DO UPDATE
                SET last_seen_at = CURRENT_TIMESTAMP,
                    occurrence_count = {schema}.log_error.occurrence_count + 1,
                    message = EXCLUDED.message,
                    context_json = EXCLUDED.context_json,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            (
                "dlq_" + fingerprint[:24],
                error_code[:120],
                ("RAG Kafka 消息进入 DLQ：" + error_message)[:1000],
                fingerprint,
                material_id,
                nullable_text(payload.get("canonicalDocumentId"))[:120] if nullable_text(payload.get("canonicalDocumentId")) else None,
                to_json(context),
            ),
        )

    def _find_material_for_update(self, cursor: Any, material_id: int | None) -> dict[str, Any] | None:
        if material_id is None:
            return None
        cursor.execute(
            self._sql("SELECT * FROM {schema}.learning_material WHERE id = %s FOR UPDATE"),
            (material_id,),
        )
        return cursor.fetchone()

    def _find_job_for_update(self, cursor: Any, job_id: str | None) -> dict[str, Any] | None:
        if not job_id:
            return None
        cursor.execute(
            self._sql("SELECT * FROM {schema}.rag_index_job WHERE id = %s FOR UPDATE"),
            (job_id,),
        )
        return cursor.fetchone()

    @staticmethod
    def _is_active_row(material: dict[str, Any] | None, job_id: str, request_version: int) -> bool:
        return bool(
            material
            and str(material.get("active_index_job_id") or "") == job_id
            and int(material.get("index_request_version") or 0) == int(request_version)
        )

    def _is_active_job(self, material: dict[str, Any] | None, job: dict[str, Any] | None, request_version: int) -> bool:
        return bool(
            material
            and job
            and self._is_active_row(material, str(job.get("id") or ""), request_version)
            and int(job.get("request_version") or 0) == int(request_version)
        )

    @staticmethod
    def _execution_owned_by(
        job: dict[str, Any] | None,
        execution_owner: str,
        *,
        require_unexpired: bool,
    ) -> bool:
        """用唯一执行令牌围栏迟到 worker；写入阶段还必须持有未过期租约。"""
        if not (
            job
            and str(job.get("status") or "") == "RUNNING"
            and str(job.get("locked_by") or "") == execution_owner
        ):
            return False
        return not require_unexpired or seconds_until(job.get("lease_until"), datetime.now(timezone.utc)) > 0

    @classmethod
    def _execution_result_allowed(cls, job: dict[str, Any] | None, execution_owner: str | None) -> bool:
        """终态必须匹配当前令牌；无令牌旧消息只能处理尚未被抢占的任务。"""
        if execution_owner:
            return cls._execution_owned_by(job, execution_owner, require_unexpired=False)
        return not job or nullable_text(job.get("locked_by")) is None

    def _mark_stale(self, cursor: Any, job_id: str, reason: str) -> None:
        cursor.execute(
            self._sql(
                """
                UPDATE {schema}.rag_index_job
                SET status = 'STALE_IGNORED',
                    error_message = %s,
                    finished_at = CURRENT_TIMESTAMP,
                    lease_until = NULL,
                    locked_by = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                  AND status NOT IN ('SUCCEEDED', 'FAILED', 'DLQ')
                """
            ),
            (truncate(reason, 1000), job_id),
        )

    @staticmethod
    def _is_terminal_dlq_topic(topic: str | None) -> bool:
        return topic in {
            os.getenv("RAG_KAFKA_TOPIC_INDEX_REQUEST", "rag.material.index.request.v1"),
            os.getenv("RAG_KAFKA_TOPIC_INDEX_RESULT", "rag.material.index.result.v1"),
            os.getenv("RAG_KAFKA_TOPIC_PROMOTE_REQUEST", "rag.material.index.promote.request.v1"),
            os.getenv("RAG_KAFKA_TOPIC_PROMOTE_RESULT", "rag.material.index.promote.result.v1"),
        }

    def _connect(self):
        """延迟导入 psycopg，单元测试替身无需数据库驱动。"""
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("RAG 索引任务仓储需要安装 psycopg[binary]") from exc
        return psycopg.connect(self.database_url, row_factory=dict_row)

    def _sql(self, statement: str):
        from psycopg import sql

        return sql.SQL(statement).format(schema=sql.Identifier(self.schema))


def parse_json_object(value: object) -> dict[str, Any]:
    """容错读取历史 result JSON，旧记录异常不阻断终态恢复。"""
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def to_json(value: object) -> str:
    """保存 Kafka 或任务快照时保留中文，不让 Python repr 进入数据库。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def build_index_completed_progress_payload(
    result_payload: dict[str, Any],
    job: dict[str, Any],
) -> dict[str, Any]:
    """由索引结果生成确定性的 100% 终态进度，消除 Kafka 跨 topic 乱序影响。"""
    chunk_count = max(0, as_int(result_payload.get("chunkCount")) or 0)
    status = str(result_payload.get("status") or "READY")
    return {
        "jobId": result_payload.get("jobId") or job.get("id"),
        "materialId": result_payload.get("materialId") or job.get("material_id"),
        "canonicalDocumentId": result_payload.get("canonicalDocumentId") or job.get("canonical_document_id"),
        "stagingDocumentId": result_payload.get("stagingDocumentId") or job.get("staging_document_id"),
        "userId": result_payload.get("userId") or job.get("user_id"),
        "requestVersion": result_payload.get("requestVersion") or job.get("request_version"),
        "stageCode": "index.completed",
        "stageLabel": "索引完成",
        "message": f"索引完成：状态 {status}，共 {chunk_count} 个切块",
        "status": "COMPLETED",
        "currentStep": 8,
        "totalSteps": 8,
        "currentChunk": chunk_count,
        "totalChunks": chunk_count,
        "percent": 100,
        "parser": result_payload.get("parser"),
    }


def as_int(value: object) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def nullable_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]


def seconds_until(value: object, now: datetime | None = None) -> float:
    """把数据库时间转换为剩余秒数，空值或非法值视为已经到期。"""
    if isinstance(value, datetime):
        target = value
    else:
        try:
            target = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return 0.0
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return max(0.0, (target - reference).total_seconds())
