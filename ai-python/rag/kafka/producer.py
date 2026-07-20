from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.schemas.kafka import KafkaEnvelope
from app.schemas.rag import ProgressEvent


def utc_now() -> str:
    """生成 Kafka envelope 使用的 UTC 时间。"""
    return datetime.now(timezone.utc).isoformat()


def build_envelope(
    *,
    message_type: str,
    partition_key: str,
    idempotency_key: str,
    payload: dict[str, Any],
    attempt: int = 0,
    producer: str = "ai-python",
    original_message_id: str | None = None,
    not_before: str | None = None,
) -> KafkaEnvelope:
    """构造统一 Kafka envelope。"""
    message_id = str(uuid.uuid4())
    return KafkaEnvelope(
        messageId=message_id,
        originalMessageId=original_message_id or message_id,
        messageType=message_type,
        eventTime=utc_now(),
        producer=producer,
        traceId="py_" + uuid.uuid4().hex,
        correlationId=partition_key,
        partitionKey=partition_key,
        idempotencyKey=idempotency_key,
        attempt=attempt,
        notBefore=not_before,
        payload=payload,
    )


class KafkaJsonProducer:
    """封装 confluent-kafka Producer，统一发送 JSON envelope。"""

    def __init__(self, bootstrap_servers: str | None = None, producer: Any | None = None) -> None:
        if producer is not None:
            self.producer = producer
            return
        try:
            from confluent_kafka import Producer
        except ImportError as exc:
            raise RuntimeError("使用 RAG Kafka worker 需要安装 confluent-kafka") from exc
        self.producer = Producer(
            {
                "bootstrap.servers": bootstrap_servers or os.getenv("RAG_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"),
                "message.timeout.ms": positive_int("RAG_KAFKA_PRODUCER_MESSAGE_TIMEOUT_MS", 5000),
            }
        )

    def send(self, topic: str, key: str, envelope: KafkaEnvelope) -> None:
        """发送消息并等待本地 flush，失败抛异常供 caller 决定是否提交 offset。"""
        self.send_serialized(topic, key, envelope.model_dump_json())

    def send_serialized(self, topic: str, key: str, payload_json: str, *, flush_seconds: float | None = None) -> None:
        """原样发送已序列化的 Outbox payload，避免重编码改变持久化消息。"""
        error_holder: list[Exception] = []

        def callback(error, _message) -> None:
            if error is not None:
                error_holder.append(RuntimeError(str(error)))

        self.producer.produce(
            topic,
            key=key,
            value=payload_json,
            callback=callback,
        )
        timeout_seconds = flush_seconds if flush_seconds is not None else positive_float("RAG_KAFKA_PRODUCER_FLUSH_SECONDS", 5.0)
        remaining = self.producer.flush(timeout_seconds)
        if remaining and remaining > 0:
            raise RuntimeError(f"Kafka 消息发送超时，仍有 {remaining} 条消息未投递")
        if error_holder:
            raise error_holder[0]


def positive_int(name: str, default: int) -> int:
    """读取正整数配置，非法值使用安全默认值。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def positive_float(name: str, default: float) -> float:
    """读取正数秒级配置，非法值使用安全默认值。"""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


@dataclass
class KafkaProgressThrottler:
    """控制 chunk 级进度发送频率，阶段开始/完成/失败和终态必发。"""

    chunk_interval: int = int(os.getenv("RAG_KAFKA_PROGRESS_CHUNK_INTERVAL", "10"))
    min_seconds: float = float(os.getenv("RAG_KAFKA_PROGRESS_MIN_SECONDS", "2"))

    def __post_init__(self) -> None:
        self.last_sent_at = 0.0

    def should_emit(self, event: ProgressEvent) -> bool:
        """判断当前事件是否需要发送到 Kafka。"""
        if event.status in {"COMPLETED", "FAILED"} or event.stageCode in {"index.completed", "index.failed"}:
            self.last_sent_at = time.monotonic()
            return True
        if event.stageCode not in {"embedding.chunk", "vector.upsert.chunk", "memory.upsert.chunk"}:
            self.last_sent_at = time.monotonic()
            return True
        current = event.currentChunk or 0
        total = event.totalChunks or 0
        now = time.monotonic()
        if current <= 1 or (total and current >= total):
            self.last_sent_at = now
            return True
        if self.chunk_interval > 0 and current % self.chunk_interval == 0:
            self.last_sent_at = now
            return True
        if now - self.last_sent_at >= self.min_seconds:
            self.last_sent_at = now
            return True
        return False


class KafkaProgressProducer:
    """把 RagProgressReporter 事件转换成 RAG_INDEX_PROGRESS 消息。"""

    def __init__(self, producer: KafkaJsonProducer, topic: str | None = None, throttler: KafkaProgressThrottler | None = None) -> None:
        self.producer = producer
        self.topic = topic or os.getenv("RAG_KAFKA_TOPIC_PROGRESS", "rag.material.index.progress.v1")
        self.throttler = throttler or KafkaProgressThrottler()
        self.sequence = 0

    def send_progress(
        self,
        *,
        event: ProgressEvent,
        document_id: str,
        material_id: int | None,
        user_id: str,
        parser: str | None,
        extra_context: dict[str, Any],
    ) -> None:
        """发送一条节流后的 progress 消息。"""
        if not self.throttler.should_emit(event):
            return
        self.sequence += 1
        canonical_document_id = str(extra_context.get("canonicalDocumentId") or document_id)
        job_id = str(extra_context.get("jobId") or "")
        request_version = int(extra_context.get("requestVersion") or 0)
        payload = event.model_dump(mode="json")
        payload.update(
            {
                "jobId": job_id,
                "materialId": int(extra_context.get("materialId") or material_id or 0),
                "canonicalDocumentId": canonical_document_id,
                "stagingDocumentId": str(extra_context.get("stagingDocumentId") or document_id),
                "userId": user_id,
                "parser": parser,
                "requestVersion": request_version,
                "progressSequence": self.sequence,
            }
        )
        idempotency_key = f"RAG_PROGRESS:{canonical_document_id}:{job_id}:{self.sequence}:v1"
        envelope = build_envelope(
            message_type="RAG_INDEX_PROGRESS",
            partition_key=canonical_document_id,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        self.producer.send(self.topic, canonical_document_id, envelope)


def redacted_json(payload: dict[str, Any]) -> str:
    """序列化脱敏 payload，避免 DLQ 写入正文和密钥。"""
    blocked = {
        "text",
        "content",
        "resumeText",
        "apiKey",
        "accessKeySecret",
        "accessKeyId",
        "token",
        "secret",
        "objectKey",
        "publicUrl",
        "sourcePath",
    }
    blocked_lower = {key.lower() for key in blocked}

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for key, item in value.items():
                safe_key = str(key)
                result[safe_key] = "<redacted>" if safe_key.lower() in blocked_lower else redact(item)
            return result
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    safe = redact(payload)
    return json.dumps(safe, ensure_ascii=False)
