from __future__ import annotations

import os
import logging
import socket
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.repositories.rag_job import RagJobRepository
from app.schemas.kafka import (
    IndexRequestPayload,
    KafkaEnvelope,
    PromoteRequestPayload,
)
from app.storage.object_storage import (
    OpenedStorageObject,
    RagObjectStorage,
    download_storage_source,
)
from app.schemas.rag import IndexResponse
from app.services.video_parallel_indexing import parse_video_source_with_worker_pool
from app.services.remote_video_import import (
    RemoteVideoError,
    RemoteVideoPermanentError,
    download_bilibili_video,
    sanitize_remote_video_parse_result,
)
from rag.loaders.document_parsers import DocumentParserRouter
from rag.observability.progress import RagProgressReporter
from rag.kafka.producer import KafkaJsonProducer, KafkaProgressProducer, build_envelope, redacted_json
from rag.retrievers.retrieval import create_rag_store


logger = logging.getLogger(__name__)


class IndexExecutionLostError(RuntimeError):
    """索引 worker 已失去数据库执行权，禁止继续写入或发布终态。"""


@dataclass
class IndexExecutionLease:
    """单次索引执行的唯一令牌和进程内失租信号。"""

    job_id: str
    owner_id: str
    lease_seconds: int
    heartbeat_seconds: float
    managed_retry: bool
    lost_event: threading.Event = field(default_factory=threading.Event)
    stop_event: threading.Event = field(default_factory=threading.Event)
    heartbeat_thread: threading.Thread | None = None


class LeaseGuardProgressProducer:
    """在每次进度/向量写入阶段前检查失租信号，并附带执行令牌。"""

    def __init__(self, delegate: Any, execution: IndexExecutionLease) -> None:
        self.delegate = delegate
        self.execution = execution

    def send_progress(self, **kwargs: Any) -> None:
        if self.execution.lost_event.is_set():
            raise IndexExecutionLostError("索引任务租约已失效")
        extra_context = dict(kwargs.get("extra_context") or {})
        extra_context["executionOwner"] = self.execution.owner_id
        self.delegate.send_progress(**{**kwargs, "extra_context": extra_context})


class RagKafkaIndexWorker:
    """处理 RAG_INDEX_REQUESTED 消息，只写 staging 索引。"""

    def __init__(
        self,
        *,
        store=None,
        parser_router: DocumentParserRouter | None = None,
        producer: KafkaJsonProducer | None = None,
        progress_producer: KafkaProgressProducer | None = None,
        job_repository: RagJobRepository | None = None,
        object_storage: RagObjectStorage | None = None,
        execution_owner_id: str | None = None,
        execution_lease_seconds: int | None = None,
        execution_heartbeat_seconds: float | None = None,
    ) -> None:
        if parser_router is None:
            from rag.loaders.mineru_loader import MineruDocumentLoader

            parser_router = DocumentParserRouter(MineruDocumentLoader())
        self.store = store or create_rag_store()
        self.parser_router = parser_router
        self.producer = producer or KafkaJsonProducer()
        self.progress_producer = progress_producer or KafkaProgressProducer(self.producer)
        self.job_repository = job_repository
        self.object_storage = object_storage
        self.execution_owner_id = execution_owner_id
        self.execution_lease_seconds = execution_lease_seconds or positive_int_env(
            "RAG_INDEX_EXECUTION_LEASE_SECONDS",
            180,
        )
        self.execution_heartbeat_seconds = execution_heartbeat_seconds
        self.result_topic = os.getenv("RAG_KAFKA_TOPIC_INDEX_RESULT", "rag.material.index.result.v1")
        self.dlq_topic = os.getenv("RAG_KAFKA_TOPIC_INDEX_DLQ", "rag.material.index.dlq.v1")
        self.retry_topics = {
            1: os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_1M", "rag.material.index.retry.1m.v1"),
            2: os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_10M", "rag.material.index.retry.10m.v1"),
            3: os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_1H", "rag.material.index.retry.1h.v1"),
        }
        self.retry_delays = {
            1: int(os.getenv("RAG_KAFKA_RETRY_1M_SECONDS", "60")),
            2: int(os.getenv("RAG_KAFKA_RETRY_10M_SECONDS", "600")),
            3: int(os.getenv("RAG_KAFKA_RETRY_1H_SECONDS", "3600")),
        }
        self.max_attempts = int(os.getenv("RAG_KAFKA_MAX_ATTEMPTS", "3"))

    def handle_envelope(self, envelope: KafkaEnvelope) -> IndexResponse | dict[str, Any]:
        """处理一条索引请求，发送成功或失败结果后再允许 caller 提交 offset。"""
        payload = IndexRequestPayload.model_validate(envelope.payload)
        execution_state, execution = self._begin_execution(payload, envelope)
        if execution_state == "STALE":
            # active job 已改变时不写 staging，offset 可以安全提交。
            return {"status": "STALE_IGNORED", "jobId": payload.jobId}
        try:
            result = (
                self._index_to_staging(payload, execution=execution)
                if execution is not None
                else self._index_to_staging(payload)
            )
            self._assert_execution_active(execution, verify_repository=True)
            self._send_result(envelope, payload, result, execution=execution)
            return result
        except IndexExecutionLostError as exc:
            raise RetryNotReady(execution_retry_seconds(execution), str(exc)) from exc
        except Exception as exc:
            try:
                self._assert_execution_active(execution, verify_repository=True)
            except IndexExecutionLostError as lease_error:
                raise RetryNotReady(execution_retry_seconds(execution), str(lease_error)) from lease_error
            if is_permanent_source_error(exc) or envelope.attempt >= self.max_attempts:
                self._send_failed_result(envelope, payload, exc, execution=execution)
                self._send_dlq(envelope, payload, exc, execution=execution)
                return {"status": "FAILED", "errorMessage": safe_error_summary(exc)}
            not_before = self._send_retry(envelope, payload, exc)
            if execution is not None and execution.managed_retry:
                reschedule = getattr(self.job_repository, "reschedule_index_execution", None)
                if not callable(reschedule) or not reschedule(
                    payload.jobId,
                    execution.owner_id,
                    not_before=not_before,
                    error_message=safe_error_summary(exc),
                ):
                    execution.lost_event.set()
                    raise RetryNotReady(execution_retry_seconds(execution), "索引任务重试释放执行权失败") from exc
            return {
                "status": "RETRY_SCHEDULED",
                "jobId": payload.jobId,
                "attempt": envelope.attempt,
                "nextAttempt": envelope.attempt + 1,
            }
        finally:
            self._stop_execution_heartbeat(execution)

    def _begin_execution(
        self,
        payload: IndexRequestPayload,
        envelope: KafkaEnvelope,
    ) -> tuple[str, IndexExecutionLease | None]:
        """为 Kafka 新投递原子抢占令牌；local worker 只验证已持有的租约。"""
        if self.job_repository is None:
            return "ACTIVE", None

        owner_id = self.execution_owner_id
        managed_retry = owner_id is None
        if owner_id is not None:
            owns = getattr(self.job_repository, "owns_index_execution", None)
            if not callable(owns):
                # 兼容只关注解析行为的轻量测试仓储；生产仓储必须实现执行权检查。
                return "ACTIVE", None
            if not owns(payload.jobId, owner_id):
                is_active = getattr(self.job_repository, "is_active", None)
                mark_stale = getattr(self.job_repository, "mark_stale_execution", None)
                if callable(is_active) and not is_active(payload.materialId, payload.jobId, payload.requestVersion):
                    if callable(mark_stale):
                        mark_stale(payload.jobId, owner_id, "本地索引任务已被新版本替换")
                    return "STALE", None
                raise RetryNotReady(1.0, "本地索引任务已失去租约")
        else:
            claim = getattr(self.job_repository, "claim_index_execution", None)
            if not callable(claim):
                legacy_mark = getattr(self.job_repository, "mark_index_processing", None)
                if callable(legacy_mark) and not legacy_mark(payload.materialId, payload.jobId, payload.requestVersion):
                    return "STALE", None
                return "ACTIVE", None
            owner_id = new_execution_owner()
            claim_result = claim(
                payload.materialId,
                payload.jobId,
                payload.requestVersion,
                execution_owner=owner_id,
                lease_seconds=self.execution_lease_seconds,
                delivery_attempt=max(1, envelope.attempt + 1),
            )
            if str(getattr(claim_result, "status", "STALE")) == "BUSY":
                raise RetryNotReady(
                    max(0.1, float(getattr(claim_result, "retry_after_seconds", 1.0) or 1.0)),
                    "同一索引任务已有执行者",
                )
            if not bool(getattr(claim_result, "acquired", False)):
                return "STALE", None

        heartbeat_seconds = self.execution_heartbeat_seconds
        if heartbeat_seconds is None:
            heartbeat_seconds = max(1.0, min(float(self.execution_lease_seconds) / 3, 60.0))
        execution = IndexExecutionLease(
            job_id=payload.jobId,
            owner_id=owner_id,
            lease_seconds=self.execution_lease_seconds,
            heartbeat_seconds=max(0.01, float(heartbeat_seconds)),
            managed_retry=managed_retry,
        )
        self._start_execution_heartbeat(execution)
        return "ACTIVE", execution

    def _start_execution_heartbeat(self, execution: IndexExecutionLease) -> None:
        """后台续租；任何失败都关闭写入闸门，防止旧 worker 继续提交。"""
        renew = getattr(self.job_repository, "renew_index_execution", None)
        if not callable(renew):
            renew = getattr(self.job_repository, "renew_local_job_lease", None)
        if not callable(renew):
            return

        def heartbeat_loop() -> None:
            while not execution.stop_event.wait(execution.heartbeat_seconds):
                try:
                    renewed = bool(renew(execution.job_id, execution.owner_id, execution.lease_seconds))
                except Exception:
                    logger.exception("索引任务续租失败，已停止后续写入，jobId=%s", execution.job_id)
                    execution.lost_event.set()
                    return
                if not renewed:
                    logger.warning("索引任务已失去租约，已停止后续写入，jobId=%s", execution.job_id)
                    execution.lost_event.set()
                    return

        execution.heartbeat_thread = threading.Thread(
            target=heartbeat_loop,
            name=f"rag-index-lease-{execution.job_id[:16]}",
            daemon=True,
        )
        execution.heartbeat_thread.start()

    def _assert_execution_active(
        self,
        execution: IndexExecutionLease | None,
        *,
        verify_repository: bool,
    ) -> None:
        """写入或发布结果前执行围栏检查，失租后只允许 Kafka 重放。"""
        if execution is None:
            return
        if execution.lost_event.is_set():
            raise IndexExecutionLostError("索引任务租约已失效")
        if verify_repository:
            owns = getattr(self.job_repository, "owns_index_execution", None)
            if callable(owns) and not owns(execution.job_id, execution.owner_id):
                execution.lost_event.set()
                raise IndexExecutionLostError("索引任务执行权已转移")

    @staticmethod
    def _stop_execution_heartbeat(execution: IndexExecutionLease | None) -> None:
        """结束本次执行的续租线程，不主动释放数据库令牌。"""
        if execution is None:
            return
        execution.stop_event.set()
        if execution.heartbeat_thread is not None:
            execution.heartbeat_thread.join(timeout=2)

    def _index_to_staging(
        self,
        payload: IndexRequestPayload,
        *,
        execution: IndexExecutionLease | None = None,
    ) -> IndexResponse:
        progress_producer: Any = self.progress_producer
        if isinstance(progress_producer, KafkaProgressProducer):
            # handler 可并行执行；每个 job 使用独立节流器和序号，避免不同资料互相抑制进度。
            progress_producer = KafkaProgressProducer(self.producer)
        if execution is not None:
            progress_producer = LeaseGuardProgressProducer(progress_producer, execution)
        progress = RagProgressReporter(
            document_id=payload.stagingDocumentId,
            user_id=payload.userId,
            delivery_mode="kafka",
            kafka_producer=progress_producer,
            kafka_context={
                "jobId": payload.jobId,
                "materialId": payload.materialId,
                "canonicalDocumentId": payload.canonicalDocumentId,
                "stagingDocumentId": payload.stagingDocumentId,
                "requestVersion": payload.requestVersion,
                "executionOwner": execution.owner_id if execution is not None else None,
            },
        )
        progress.emit("index.request", "已接收 Kafka 索引请求", current_step=1, total_steps=8, percent=5)
        source_title = payload.title
        if payload.sourceRef.type == "INLINE_TEXT":
            if not (payload.text or "").strip():
                raise ValueError("INLINE_TEXT 内容为空")
            parsed = self.parser_router.parse_text(
                document_id=payload.stagingDocumentId,
                title=source_title,
                document_type=payload.documentType,
                source_path=None,
                content=payload.text or "",
                parser=payload.sourceRef.parser,
                progress_reporter=progress,
            )
            source_path = None
        elif payload.sourceRef.type == "REMOTE_VIDEO":
            progress.emit("upload.remote_video", "正在读取 Bilibili 公开视频", current_step=1, total_steps=8, percent=6)

            def report_download(download_percent: int | None) -> None:
                mapped = 6 + round(max(0, min(download_percent or 0, 100)) * 0.09)
                progress.emit(
                    "upload.remote_video",
                    "正在获取 Bilibili 视频资源",
                    current_step=1,
                    total_steps=8,
                    percent=mapped,
                )

            downloaded = download_bilibili_video(payload.sourceRef.url, on_progress=report_download)
            try:
                source_path = downloaded.source_url
                source_title = downloaded.title
                self._assert_execution_active(execution, verify_repository=True)
                if self.job_repository is not None and not self.job_repository.update_remote_material_source(
                    payload.materialId,
                    payload.jobId,
                    payload.requestVersion,
                    execution_owner=execution.owner_id if execution is not None else None,
                    title=source_title,
                    filename=f"{payload.sourceRef.videoId}{downloaded.path.suffix.lower()}",
                    source_url=downloaded.source_url,
                    platform=payload.sourceRef.platform,
                ):
                    raise PermanentSourceError("远程视频任务已过期")
                progress.emit(
                    "upload.remote_video",
                    "Bilibili 视频获取完成，开始多模态解析",
                    status="COMPLETED",
                    current_step=1,
                    total_steps=8,
                    percent=15,
                )
                parsed = parse_video_source_with_worker_pool(
                    parser_router=self.parser_router,
                    document_id=payload.stagingDocumentId,
                    title=source_title,
                    document_type=payload.documentType,
                    source=payload.source,
                    user_id=payload.userId,
                    visibility_scope=payload.stagingVisibilityScope,
                    source_path=str(downloaded.path),
                    source_reference=downloaded.source_url,
                    filename=downloaded.filename,
                    content_type=downloaded.content_type,
                    high_precision=payload.highPrecision,
                    progress_reporter=progress,
                ) or self.parser_router.parse_video_source(
                    document_id=payload.stagingDocumentId,
                    title=source_title,
                    document_type=payload.documentType,
                    source=payload.source,
                    user_id=payload.userId,
                    visibility_scope=payload.stagingVisibilityScope,
                    source_path=str(downloaded.path),
                    source_reference=downloaded.source_url,
                    filename=downloaded.filename,
                    content_type=downloaded.content_type,
                    high_precision=payload.highPrecision,
                    progress_reporter=progress,
                )
                parsed = sanitize_remote_video_parse_result(parsed)
            finally:
                downloaded.cleanup()
        else:
            downloaded = open_storage_source(
                payload.sourceRef,
                user_id=payload.userId,
                object_storage=self.object_storage,
            )
            source_path = downloaded.source_path
            source_reference = source_path or str(downloaded.path)
            try:
                filename = downloaded.filename or payload.title
                if is_video_source(filename, payload.documentType, downloaded.content_type):
                    parsed = parse_video_source_with_worker_pool(
                        parser_router=self.parser_router,
                        document_id=payload.stagingDocumentId,
                        title=source_title,
                        document_type=payload.documentType,
                        source=payload.source,
                        user_id=payload.userId,
                        visibility_scope=payload.stagingVisibilityScope,
                        source_path=str(downloaded.path),
                        source_reference=source_reference,
                        filename=filename,
                        content_type=downloaded.content_type,
                        high_precision=payload.highPrecision,
                        progress_reporter=progress,
                    ) or self.parser_router.parse_video_source(
                        document_id=payload.stagingDocumentId,
                        title=source_title,
                        document_type=payload.documentType,
                        source=payload.source,
                        user_id=payload.userId,
                        visibility_scope=payload.stagingVisibilityScope,
                        source_path=str(downloaded.path),
                        source_reference=source_reference,
                        filename=filename,
                        content_type=downloaded.content_type,
                        high_precision=payload.highPrecision,
                        progress_reporter=progress,
                    )
                else:
                    parsed = self.parser_router.parse_file(
                        source_path=str(downloaded.path),
                        filename=filename,
                        document_id=payload.stagingDocumentId,
                        source_title=source_title,
                        document_type=payload.documentType,
                        content_type=downloaded.content_type,
                        source_reference=downloaded.source_path,
                        high_precision=payload.highPrecision,
                        progress_reporter=progress,
                    )
            finally:
                downloaded.cleanup()
        self._assert_execution_active(execution, verify_repository=True)
        return self.store.index_blocks(
            document_id=payload.stagingDocumentId,
            title=source_title,
            document_type=payload.documentType,
            source=payload.source,
            user_id=payload.userId,
            visibility_scope=payload.stagingVisibilityScope,
            language="zh-CN",
            parser=parsed.parser,
            blocks=parsed.blocks,
            parse_quality=parsed.parse_quality,
            status=parsed.status,
            source_path=source_path,
            progress_reporter=progress,
            extra_metadata={
                "canonicalDocumentId": payload.canonicalDocumentId,
                "stagingDocumentId": payload.stagingDocumentId,
                "jobId": payload.jobId,
                "sourceJobId": payload.jobId,
                "requestVersion": payload.requestVersion,
            },
        )

    def _send_result(
        self,
        envelope: KafkaEnvelope,
        payload: IndexRequestPayload,
        result: IndexResponse,
        *,
        execution: IndexExecutionLease | None = None,
    ) -> None:
        # 逐块进度已通过独立 topic 持久化，终态消息只保留可用于状态收敛的索引摘要。
        result_payload = result.model_dump(mode="json", exclude={"progressEvents"})
        result_payload.update(
            {
                "jobId": payload.jobId,
                "materialId": payload.materialId,
                "canonicalDocumentId": payload.canonicalDocumentId,
                "stagingDocumentId": payload.stagingDocumentId,
                "requestVersion": payload.requestVersion,
                "executionOwner": execution.owner_id if execution is not None else None,
                "errorCode": None,
                "errorMessage": None,
            }
        )
        out = build_envelope(
            message_type="RAG_INDEX_RESULT",
            partition_key=payload.canonicalDocumentId,
            idempotency_key=f"RAG_INDEX_RESULT:{payload.canonicalDocumentId}:{payload.jobId}:v1",
            payload=result_payload,
            attempt=envelope.attempt,
            original_message_id=envelope.originalMessageId or envelope.messageId,
        )
        self.producer.send(self.result_topic, payload.canonicalDocumentId, out)

    def _send_failed_result(
        self,
        envelope: KafkaEnvelope,
        payload: IndexRequestPayload,
        exc: Exception,
        *,
        execution: IndexExecutionLease | None = None,
    ) -> None:
        result_payload = build_failed_result_payload(payload, exc, "RAG_KAFKA_INDEX_FAILED")
        result_payload["executionOwner"] = execution.owner_id if execution is not None else None
        out = build_envelope(
            message_type="RAG_INDEX_RESULT",
            partition_key=payload.canonicalDocumentId,
            idempotency_key=f"RAG_INDEX_RESULT:{payload.canonicalDocumentId}:{payload.jobId}:v1",
            payload=result_payload,
            attempt=envelope.attempt,
            original_message_id=envelope.originalMessageId or envelope.messageId,
        )
        self.producer.send(self.result_topic, payload.canonicalDocumentId, out)

    def _send_retry(self, envelope: KafkaEnvelope, payload: IndexRequestPayload, exc: Exception) -> datetime:
        next_attempt = envelope.attempt + 1
        bucket = min(max(next_attempt, 1), 3)
        not_before = datetime.now(timezone.utc) + timedelta(seconds=self.retry_delays[bucket])
        retry_payload = payload.model_dump(mode="json")
        retry_payload["lastErrorCode"] = "RAG_KAFKA_TRANSIENT_INDEX_ERROR"
        retry_payload["lastErrorMessage"] = safe_error_summary(exc)
        out = build_envelope(
            message_type="RAG_INDEX_RETRY",
            partition_key=payload.canonicalDocumentId,
            idempotency_key=f"RAG_INDEX:{payload.canonicalDocumentId}:{payload.jobId}:v1",
            payload=retry_payload,
            attempt=envelope.attempt,
            original_message_id=envelope.originalMessageId or envelope.messageId,
            not_before=not_before.isoformat(),
        )
        self.producer.send(self.retry_topics[bucket], payload.canonicalDocumentId, out)
        return not_before

    def _send_dlq(
        self,
        envelope: KafkaEnvelope,
        payload: IndexRequestPayload,
        exc: Exception,
        *,
        execution: IndexExecutionLease | None = None,
    ) -> None:
        dlq_payload = {
            "jobId": payload.jobId,
            "materialId": payload.materialId,
            "canonicalDocumentId": payload.canonicalDocumentId,
            "stagingDocumentId": payload.stagingDocumentId,
            "requestVersion": payload.requestVersion,
            "executionOwner": execution.owner_id if execution is not None else None,
            "attempt": envelope.attempt,
            "topic": os.getenv("RAG_KAFKA_TOPIC_INDEX_REQUEST", "rag.material.index.request.v1"),
            "errorCode": "RAG_KAFKA_PERMANENT_SOURCE_ERROR",
            "errorMessage": safe_error_summary(exc),
            "request": redacted_json(payload.model_dump(mode="json")),
        }
        out = build_envelope(
            message_type="RAG_INDEX_DLQ",
            partition_key=payload.canonicalDocumentId,
            idempotency_key=f"RAG_DLQ:{payload.canonicalDocumentId}:{payload.jobId}:{envelope.attempt}:v1",
            payload=dlq_payload,
            attempt=envelope.attempt,
            original_message_id=envelope.originalMessageId or envelope.messageId,
        )
        self.producer.send(self.dlq_topic, payload.canonicalDocumentId, out)


class RagKafkaRetryScheduler:
    """消费 retry topic，到期后重投 index request，超过上限后写 FAILED 与 DLQ。"""

    def __init__(self, *, producer: KafkaJsonProducer | None = None) -> None:
        self.producer = producer or KafkaJsonProducer()
        self.request_topic = os.getenv("RAG_KAFKA_TOPIC_INDEX_REQUEST", "rag.material.index.request.v1")
        self.result_topic = os.getenv("RAG_KAFKA_TOPIC_INDEX_RESULT", "rag.material.index.result.v1")
        self.dlq_topic = os.getenv("RAG_KAFKA_TOPIC_INDEX_DLQ", "rag.material.index.dlq.v1")
        self.max_attempts = int(os.getenv("RAG_KAFKA_MAX_ATTEMPTS", "3"))

    def handle_envelope(self, envelope: KafkaEnvelope) -> dict[str, Any]:
        """处理 retry 消息；未到 notBefore 时通知 consumer loop 暂停等待。"""
        payload = IndexRequestPayload.model_validate(envelope.payload)
        delay_seconds = seconds_until(envelope.notBefore)
        if delay_seconds > 0:
            raise RetryNotReady(delay_seconds)
        next_attempt = envelope.attempt + 1
        if next_attempt > self.max_attempts:
            exhausted = RuntimeError("RAG Kafka retry 次数已耗尽")
            self._send_failed_result(envelope, payload, exhausted)
            self._send_dlq(envelope, payload, exhausted)
            return {"status": "DLQ", "jobId": payload.jobId, "attempt": envelope.attempt}
        request_payload = payload.model_dump(mode="json")
        request_payload.pop("lastErrorCode", None)
        request_payload.pop("lastErrorMessage", None)
        out = build_envelope(
            message_type="RAG_INDEX_REQUESTED",
            partition_key=payload.canonicalDocumentId,
            idempotency_key=f"RAG_INDEX:{payload.canonicalDocumentId}:{payload.jobId}:v1",
            payload=request_payload,
            attempt=next_attempt,
            original_message_id=envelope.originalMessageId or envelope.messageId,
        )
        self.producer.send(self.request_topic, payload.canonicalDocumentId, out)
        return {"status": "RESENT", "jobId": payload.jobId, "attempt": next_attempt}

    def _send_failed_result(self, envelope: KafkaEnvelope, payload: IndexRequestPayload, exc: Exception) -> None:
        result_payload = build_failed_result_payload(payload, exc, "RAG_KAFKA_RETRY_EXHAUSTED")
        out = build_envelope(
            message_type="RAG_INDEX_RESULT",
            partition_key=payload.canonicalDocumentId,
            idempotency_key=f"RAG_INDEX_RESULT:{payload.canonicalDocumentId}:{payload.jobId}:v1",
            payload=result_payload,
            attempt=envelope.attempt,
            original_message_id=envelope.originalMessageId or envelope.messageId,
        )
        self.producer.send(self.result_topic, payload.canonicalDocumentId, out)

    def _send_dlq(self, envelope: KafkaEnvelope, payload: IndexRequestPayload, exc: Exception) -> None:
        dlq_payload = {
            "jobId": payload.jobId,
            "materialId": payload.materialId,
            "canonicalDocumentId": payload.canonicalDocumentId,
            "stagingDocumentId": payload.stagingDocumentId,
            "requestVersion": payload.requestVersion,
            "attempt": envelope.attempt,
            "topic": self.request_topic,
            "errorCode": "RAG_KAFKA_RETRY_EXHAUSTED",
            "errorMessage": safe_error_summary(exc),
            "request": redacted_json(payload.model_dump(mode="json")),
        }
        out = build_envelope(
            message_type="RAG_INDEX_DLQ",
            partition_key=payload.canonicalDocumentId,
            idempotency_key=f"RAG_DLQ:{payload.canonicalDocumentId}:{payload.jobId}:{envelope.attempt}:v1",
            payload=dlq_payload,
            attempt=envelope.attempt,
            original_message_id=envelope.originalMessageId or envelope.messageId,
        )
        self.producer.send(self.dlq_topic, payload.canonicalDocumentId, out)


class RetryNotReady(RuntimeError):
    """表示 retry 消息尚未到达 notBefore 时间。"""

    def __init__(self, delay_seconds: float, message: str = "RAG Kafka retry 消息尚未到期") -> None:
        super().__init__(message)
        self.delay_seconds = delay_seconds


def new_execution_owner() -> str:
    """为每次 Kafka 抢占生成唯一短令牌，作为数据库写入围栏。"""
    host = (socket.gethostname() or "python")[:36]
    return f"kafka-{host}-{uuid4().hex}"[:120]


def execution_retry_seconds(execution: IndexExecutionLease | None) -> float:
    """失租后短暂延迟源 offset，给新执行者或租约恢复留出时间。"""
    if execution is None:
        return 1.0
    return max(1.0, min(float(execution.lease_seconds), 30.0))


def positive_int_env(name: str, default: int) -> int:
    """读取正整数环境变量，非法值回退到生产安全默认值。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def build_failed_result_payload(payload: IndexRequestPayload, exc: Exception, error_code: str) -> dict[str, Any]:
    """构造对齐 IndexResponse 的失败 result payload。"""
    return {
        "documentId": payload.stagingDocumentId,
        "title": payload.title,
        "status": "FAILED",
        "chunkCount": 0,
        "parser": "kafka-worker-error",
        "documentSummary": safe_error_summary(exc),
        "parseQuality": {"score": 0.0, "messages": [safe_error_summary(exc)]},
        "progressEvents": [],
        "jobId": payload.jobId,
        "materialId": payload.materialId,
        "canonicalDocumentId": payload.canonicalDocumentId,
        "stagingDocumentId": payload.stagingDocumentId,
        "requestVersion": payload.requestVersion,
        "errorCode": error_code,
        "errorMessage": safe_error_summary(exc),
    }


def seconds_until(value: datetime | str | None) -> float:
    """计算 notBefore 距离当前 UTC 的秒数。"""
    if value is None:
        return 0.0
    if isinstance(value, datetime):
        target = value
    else:
        target = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max(0.0, (target.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())


class RagKafkaPromoteWorker:
    """处理 promote request，把 staging 索引提升为 canonical。"""

    def __init__(
        self,
        *,
        store=None,
        producer: KafkaJsonProducer | None = None,
        active_checker: Callable[[PromoteRequestPayload], None] | None = None,
    ) -> None:
        self.store = store or create_rag_store()
        self.producer = producer or KafkaJsonProducer()
        self.active_checker = active_checker or assert_active_job
        self.result_topic = os.getenv("RAG_KAFKA_TOPIC_PROMOTE_RESULT", "rag.material.index.promote.result.v1")

    def handle_envelope(self, envelope: KafkaEnvelope) -> dict[str, Any]:
        payload = PromoteRequestPayload.model_validate(envelope.payload)
        try:
            self.active_checker(payload)
            result = self.store.promote_staged_index(
                canonical_document_id=payload.canonicalDocumentId,
                staging_document_id=payload.stagingDocumentId,
                job_id=payload.jobId,
                request_version=payload.requestVersion,
                expected_chunk_count=payload.chunkCount,
            )
            out_payload = {
                "jobId": payload.jobId,
                "materialId": payload.materialId,
                "canonicalDocumentId": payload.canonicalDocumentId,
                "stagingDocumentId": payload.stagingDocumentId,
                "requestVersion": payload.requestVersion,
                "status": "SUCCEEDED",
                **result,
            }
        except Exception as exc:
            stale = isinstance(exc, StalePromoteRequestError)
            out_payload = {
                "jobId": payload.jobId,
                "materialId": payload.materialId,
                "canonicalDocumentId": payload.canonicalDocumentId,
                "stagingDocumentId": payload.stagingDocumentId,
                "requestVersion": payload.requestVersion,
                "status": "FAILED",
                "alreadyPromoted": False,
                "canonicalChunkCount": 0,
                "stagingChunkCount": 0,
                "errorCode": "RAG_PROMOTE_STALE" if stale else "RAG_PROMOTE_FAILED",
                "errorMessage": safe_error_summary(exc),
            }
        out = build_envelope(
            message_type="RAG_PROMOTE_RESULT",
            partition_key=payload.canonicalDocumentId,
            idempotency_key=f"RAG_PROMOTE_RESULT:{payload.canonicalDocumentId}:{payload.jobId}:v1",
            payload=out_payload,
            attempt=envelope.attempt,
            original_message_id=envelope.originalMessageId or envelope.messageId,
        )
        self.producer.send(self.result_topic, payload.canonicalDocumentId, out)
        return out_payload


def assert_active_job(payload: PromoteRequestPayload) -> None:
    """从 PostgreSQL 当前资料状态判断 promote 是否仍对应活跃索引任务。"""
    repository = RagJobRepository()
    if not repository.is_active(payload.materialId, payload.jobId, payload.requestVersion):
        raise StalePromoteRequestError("索引提升请求已过期")


OpenedStorageSource = OpenedStorageObject


def open_storage_source(
    source_ref,
    *,
    user_id: str | None = None,
    object_storage: RagObjectStorage | None = None,
) -> OpenedStorageSource:
    """打开受控本地原文件，或把当前用户所属 OSS 对象下载到临时文件。"""
    try:
        return download_storage_source(
            source_ref,
            user_id=str(user_id or ""),
            object_storage=object_storage,
        )
    except Exception as exc:
        if isinstance(exc, PermanentSourceError):
            raise
        raise PermanentSourceError(str(exc)) from exc


def is_video_source(filename: str, document_type: str | None, content_type: str | None) -> bool:
    """判断下载文件是否应走基于临时文件路径的视频解析路线。"""
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    if suffix in {"mp4", "mov", "webm", "mkv", "avi"}:
        return True
    if (document_type or "").lower() in {"video", "mp4", "mov", "webm", "mkv", "avi"}:
        return True
    return bool(content_type and content_type.lower().startswith("video/"))


class PermanentSourceError(RuntimeError):
    """表示受控原文件缺失或非法等无需继续重试的错误。"""


class StalePromoteRequestError(RuntimeError):
    """表示 promote 请求已经不是当前 active job。"""


def safe_error_summary(exc: Exception) -> str:
    """Kafka 失败结果和 DLQ 不回显异常文本，避免第三方响应或原始资料进入消息系统。"""
    if isinstance(exc, PermanentSourceError):
        return "原始资料不可读取或已不存在"
    if isinstance(exc, RemoteVideoError):
        return exc.public_message
    if isinstance(exc, StalePromoteRequestError):
        return "索引提升请求已过期"
    if isinstance(exc, ValueError):
        return "消息字段校验失败"
    return f"RAG Kafka 处理失败：{exc.__class__.__name__}"


def is_permanent_source_error(exc: Exception) -> bool:
    return isinstance(exc, (PermanentSourceError, RemoteVideoPermanentError))
