from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

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
from rag.loaders.document_parsers import DocumentParserRouter
from rag.observability.progress import RagProgressReporter
from rag.kafka.producer import KafkaJsonProducer, KafkaProgressProducer, build_envelope, redacted_json
from rag.retrievers.retrieval import create_rag_store


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
        if self.job_repository is not None and not self.job_repository.mark_index_processing(
            payload.materialId,
            payload.jobId,
            payload.requestVersion,
        ):
            # active job 已改变时不写 staging，offset 可以安全提交。
            return {"status": "STALE_IGNORED", "jobId": payload.jobId}
        try:
            result = self._index_to_staging(payload)
            self._send_result(envelope, payload, result)
            return result
        except Exception as exc:
            if is_permanent_source_error(exc) or envelope.attempt >= self.max_attempts:
                self._send_failed_result(envelope, payload, exc)
                self._send_dlq(envelope, payload, exc)
                return {"status": "FAILED", "errorMessage": safe_error_summary(exc)}
            self._send_retry(envelope, payload, exc)
            return {
                "status": "RETRY_SCHEDULED",
                "jobId": payload.jobId,
                "attempt": envelope.attempt,
                "nextAttempt": envelope.attempt + 1,
            }

    def _index_to_staging(self, payload: IndexRequestPayload) -> IndexResponse:
        progress = RagProgressReporter(
            document_id=payload.stagingDocumentId,
            user_id=payload.userId,
            delivery_mode="kafka",
            kafka_producer=self.progress_producer,
            kafka_context={
                "jobId": payload.jobId,
                "materialId": payload.materialId,
                "canonicalDocumentId": payload.canonicalDocumentId,
                "stagingDocumentId": payload.stagingDocumentId,
                "requestVersion": payload.requestVersion,
            },
        )
        progress.emit("index.request", "已接收 Kafka 索引请求", current_step=1, total_steps=8, percent=5)
        if payload.sourceRef.type == "INLINE_TEXT":
            if not (payload.text or "").strip():
                raise ValueError("INLINE_TEXT 内容为空")
            parsed = self.parser_router.parse_text(
                document_id=payload.stagingDocumentId,
                title=payload.title,
                document_type=payload.documentType,
                source_path=None,
                content=payload.text or "",
                parser=payload.sourceRef.parser,
                progress_reporter=progress,
            )
            source_path = None
        else:
            downloaded = open_storage_source(
                payload.sourceRef,
                user_id=payload.userId,
                object_storage=self.object_storage,
            )
            source_path = downloaded.source_path
            try:
                filename = downloaded.filename or payload.title
                if is_video_source(filename, payload.documentType, downloaded.content_type):
                    parsed = self.parser_router.parse_video_source(
                        document_id=payload.stagingDocumentId,
                        title=payload.title,
                        document_type=payload.documentType,
                        source=payload.source,
                        user_id=payload.userId,
                        visibility_scope=payload.stagingVisibilityScope,
                        source_path=str(downloaded.path),
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
                        source_title=payload.title,
                        document_type=payload.documentType,
                        content_type=downloaded.content_type,
                        source_reference=downloaded.source_path,
                        high_precision=payload.highPrecision,
                        progress_reporter=progress,
                    )
            finally:
                downloaded.cleanup()
        return self.store.index_blocks(
            document_id=payload.stagingDocumentId,
            title=payload.title,
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

    def _send_result(self, envelope: KafkaEnvelope, payload: IndexRequestPayload, result: IndexResponse) -> None:
        result_payload = result.model_dump(mode="json")
        result_payload.update(
            {
                "jobId": payload.jobId,
                "materialId": payload.materialId,
                "canonicalDocumentId": payload.canonicalDocumentId,
                "stagingDocumentId": payload.stagingDocumentId,
                "requestVersion": payload.requestVersion,
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

    def _send_failed_result(self, envelope: KafkaEnvelope, payload: IndexRequestPayload, exc: Exception) -> None:
        result_payload = build_failed_result_payload(payload, exc, "RAG_KAFKA_INDEX_FAILED")
        out = build_envelope(
            message_type="RAG_INDEX_RESULT",
            partition_key=payload.canonicalDocumentId,
            idempotency_key=f"RAG_INDEX_RESULT:{payload.canonicalDocumentId}:{payload.jobId}:v1",
            payload=result_payload,
            attempt=envelope.attempt,
            original_message_id=envelope.originalMessageId or envelope.messageId,
        )
        self.producer.send(self.result_topic, payload.canonicalDocumentId, out)

    def _send_retry(self, envelope: KafkaEnvelope, payload: IndexRequestPayload, exc: Exception) -> None:
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

    def _send_dlq(self, envelope: KafkaEnvelope, payload: IndexRequestPayload, exc: Exception) -> None:
        dlq_payload = {
            "jobId": payload.jobId,
            "materialId": payload.materialId,
            "canonicalDocumentId": payload.canonicalDocumentId,
            "stagingDocumentId": payload.stagingDocumentId,
            "requestVersion": payload.requestVersion,
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

    def __init__(self, delay_seconds: float) -> None:
        super().__init__("RAG Kafka retry 消息尚未到期")
        self.delay_seconds = delay_seconds


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
    if isinstance(exc, StalePromoteRequestError):
        return "索引提升请求已过期"
    if isinstance(exc, ValueError):
        return "消息字段校验失败"
    return f"RAG Kafka 处理失败：{exc.__class__.__name__}"


def is_permanent_source_error(exc: Exception) -> bool:
    return isinstance(exc, PermanentSourceError)
