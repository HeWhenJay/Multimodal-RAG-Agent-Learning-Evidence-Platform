from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from app.schemas.kafka import (
    IndexRequestPayload,
    KafkaEnvelope,
    PromoteRequestPayload,
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
    ) -> None:
        if parser_router is None:
            from rag.loaders.mineru_loader import MineruDocumentLoader

            parser_router = DocumentParserRouter(MineruDocumentLoader())
        self.store = store or create_rag_store()
        self.parser_router = parser_router
        self.producer = producer or KafkaJsonProducer()
        self.progress_producer = progress_producer or KafkaProgressProducer(self.producer)
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
        try:
            result = self._index_to_staging(payload)
            self._send_result(envelope, payload, result)
            return result
        except Exception as exc:
            if is_permanent_source_error(exc) or envelope.attempt >= self.max_attempts:
                self._send_failed_result(envelope, payload, exc)
                self._send_dlq(envelope, payload, exc)
                return {"status": "FAILED", "errorMessage": str(exc)[:500]}
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
            downloaded = download_java_source(payload.sourceRef, payload.jobId, payload.requestVersion)
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
                    parsed = self.parser_router.parse_bytes(
                        content=downloaded.path.read_bytes(),
                        filename=filename,
                        document_id=payload.stagingDocumentId,
                        source_title=payload.title,
                        document_type=payload.documentType,
                        content_type=downloaded.content_type,
                        source_path=downloaded.source_path,
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
        retry_payload["lastErrorMessage"] = str(exc)[:500]
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
            "errorMessage": str(exc)[:500],
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
            "errorMessage": str(exc)[:500],
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
        "documentSummary": str(exc)[:500],
        "parseQuality": {"score": 0.0, "messages": [str(exc)[:300]]},
        "progressEvents": [],
        "jobId": payload.jobId,
        "materialId": payload.materialId,
        "canonicalDocumentId": payload.canonicalDocumentId,
        "stagingDocumentId": payload.stagingDocumentId,
        "requestVersion": payload.requestVersion,
        "errorCode": error_code,
        "errorMessage": str(exc)[:1000],
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
        self.active_checker = active_checker or assert_java_active_job
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
                "errorMessage": str(exc)[:1000],
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


def assert_java_active_job(payload: PromoteRequestPayload) -> None:
    """向 Java 内部接口确认 promote 请求仍属于当前 active job。"""
    base_url = os.getenv("RAG_JAVA_BASE_URL", "http://127.0.0.1:7080").rstrip("/")
    url = f"{base_url}/api/internal/rag/materials/{payload.materialId}/index-jobs/{payload.jobId}/active"
    token = os.getenv("RAG_JAVA_INTERNAL_TOKEN", "")
    headers = {"X-RAG-Internal-Token": token} if token else {}
    try:
        with httpx.Client(timeout=float(os.getenv("RAG_JAVA_ACTIVE_CHECK_TIMEOUT_SECONDS", "10"))) as client:
            response = client.get(url, params={"requestVersion": payload.requestVersion}, headers=headers)
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Java active-check 调用失败: {exc}") from exc
    if response.status_code in {403, 404, 410}:
        raise StalePromoteRequestError(f"Java active-check 判定 promote 已过期: status={response.status_code}")
    response.raise_for_status()
    body = response.json()
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict) or data.get("active") is not True:
        raise StalePromoteRequestError("Java active-check 返回非 active 状态")


@dataclass
class DownloadedJavaSource:
    """保存 Java Source API 流式下载后的临时文件信息。"""

    path: Path
    filename: str | None
    content_type: str | None
    source_path: str | None

    def cleanup(self) -> None:
        """清理临时文件。"""
        self.path.unlink(missing_ok=True)


def download_java_source(source_ref, job_id: str, request_version: int) -> DownloadedJavaSource:
    """从 Java Source API 流式下载资料到临时文件。"""
    base_url = source_ref.javaBaseUrl.rstrip("/")
    url = base_url + source_ref.downloadPath
    token = os.getenv("RAG_JAVA_INTERNAL_TOKEN", "")
    headers = {"X-RAG-Internal-Token": token} if token else {}
    temp_path: Path | None = None
    with httpx.Client(timeout=float(os.getenv("RAG_JAVA_SOURCE_TIMEOUT_SECONDS", "60"))) as client:
        with client.stream("GET", url, headers=headers) as response:
            if response.status_code in {404, 410}:
                body = response.read()
                raise PermanentSourceError(f"Java Source API 返回 {response.status_code}: {body[:200].decode('utf-8', errors='ignore')}")
            response.raise_for_status()
            suffix = Path(source_ref.filename or "material.bin").suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as file:
                temp_path = Path(file.name)
                for chunk in response.iter_bytes():
                    file.write(chunk)
            return DownloadedJavaSource(
                path=temp_path,
                filename=source_ref.filename or guess_filename(response),
                content_type=response.headers.get("content-type") or source_ref.contentType,
                source_path=source_ref.sourcePath,
            )


def guess_filename(response: httpx.Response) -> str | None:
    """从响应头中尽量读取文件名。"""
    disposition = response.headers.get("content-disposition") or ""
    if "filename=" not in disposition:
        return None
    return disposition.split("filename=", 1)[1].strip('" ')


def is_video_source(filename: str, document_type: str | None, content_type: str | None) -> bool:
    """判断下载文件是否应走基于临时文件路径的视频解析路线。"""
    suffix = Path(filename or "").suffix.lower().lstrip(".")
    if suffix in {"mp4", "mov", "webm", "mkv", "avi"}:
        return True
    if (document_type or "").lower() in {"video", "mp4", "mov", "webm", "mkv", "avi"}:
        return True
    return bool(content_type and content_type.lower().startswith("video/"))


class PermanentSourceError(RuntimeError):
    """表示 404/410 等无需继续重试的 Java source 错误。"""


class StalePromoteRequestError(RuntimeError):
    """表示 promote 请求已经不是当前 active job。"""


def is_permanent_source_error(exc: Exception) -> bool:
    return isinstance(exc, PermanentSourceError)
