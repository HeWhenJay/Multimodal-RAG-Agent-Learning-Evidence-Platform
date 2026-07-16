from __future__ import annotations

import os
import signal
import time
import hashlib
from typing import Callable

from app.core.runtime_config import load_runtime_config, parse_args
from app.schemas.kafka import KafkaEnvelope
from rag.kafka.producer import KafkaJsonProducer, build_envelope
from rag.kafka.worker import RagKafkaIndexWorker, RagKafkaPromoteWorker, RagKafkaRetryScheduler, RetryNotReady


class KafkaWorkerConnectionError(RuntimeError):
    """Kafka Broker 连接或传输层暂时不可用。"""


def main() -> None:
    """启动 RAG Kafka worker，默认同时处理 index 和 promote 请求。"""
    load_runtime_config(parse_args(None))
    if os.getenv("RAG_KAFKA_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("RAG_KAFKA_ENABLED 未开启，已拒绝启动 Kafka worker")
    index_worker = RagKafkaIndexWorker()
    promote_worker = RagKafkaPromoteWorker(producer=index_worker.producer)
    retry_scheduler = RagKafkaRetryScheduler(producer=index_worker.producer)
    run_consumer_forever(
        {
            os.getenv("RAG_KAFKA_TOPIC_INDEX_REQUEST", "rag.material.index.request.v1"): index_worker.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_PROMOTE_REQUEST", "rag.material.index.promote.request.v1"): promote_worker.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_1M", "rag.material.index.retry.1m.v1"): retry_scheduler.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_10M", "rag.material.index.retry.10m.v1"): retry_scheduler.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_1H", "rag.material.index.retry.1h.v1"): retry_scheduler.handle_envelope,
        }
    )


def run_consumer_forever(handlers: dict[str, Callable[[KafkaEnvelope], object]]) -> None:
    """Kafka 暂时不可用时按指数退避重连，恢复后继续消费原有 consumer group。"""
    delay_seconds = reconnect_initial_seconds()
    max_delay_seconds = reconnect_max_seconds(delay_seconds)
    while True:
        try:
            run_consumer_loop(handlers)
            return
        except KafkaWorkerConnectionError as exc:
            print(f"Kafka worker 连接不可用，将在 {delay_seconds:g} 秒后重连：{exc}")
            time.sleep(delay_seconds)
            delay_seconds = min(max_delay_seconds, delay_seconds * 2)


def run_consumer_loop(handlers: dict[str, Callable[[KafkaEnvelope], object]]) -> None:
    """使用 manual commit 消费 Kafka；handler 成功返回后才提交 offset。"""
    try:
        from confluent_kafka import Consumer
        from confluent_kafka import KafkaError
        from confluent_kafka import KafkaException
        from confluent_kafka import TopicPartition
    except ImportError as exc:
        raise RuntimeError("使用 RAG Kafka worker 需要安装 confluent-kafka") from exc

    running = True

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        consumer = Consumer(
            {
                "bootstrap.servers": os.getenv("RAG_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"),
                "group.id": os.getenv("RAG_KAFKA_GROUP_ID", "rag-python-index-workers"),
                "enable.auto.commit": False,
                "auto.offset.reset": "earliest",
            }
        )
    except KafkaException as exc:
        raise KafkaWorkerConnectionError(str(exc)) from exc
    dead_letter_producer = KafkaJsonProducer()
    consumer.subscribe(list(handlers))
    try:
        while running:
            try:
                message = consumer.poll(1.0)
            except KafkaException as exc:
                raise KafkaWorkerConnectionError(str(exc)) from exc
            if message is None:
                continue
            if message.error():
                if is_reconnectable_error(message.error(), KafkaError):
                    raise KafkaWorkerConnectionError(str(message.error()))
                if message.error().code() == getattr(KafkaError, "_PARTITION_EOF", None):
                    continue
                raise KafkaWorkerConnectionError(str(message.error()))
            topic = message.topic()
            handler = handlers.get(topic)
            if handler is None:
                continue
            envelope: KafkaEnvelope | None = None
            try:
                envelope = KafkaEnvelope.model_validate_json(message.value())
                handler(envelope)
            except RetryNotReady as exc:
                topic_partition = TopicPartition(topic, message.partition(), message.offset())
                consumer.pause([TopicPartition(topic, message.partition())])
                time.sleep(min(exc.delay_seconds, float(os.getenv("RAG_KAFKA_RETRY_MAX_SLEEP_SECONDS", "30"))))
                consumer.seek(topic_partition)
                consumer.resume([TopicPartition(topic, message.partition())])
                continue
            except Exception as exc:
                if is_connection_exception(exc):
                    raise KafkaWorkerConnectionError(str(exc)) from exc
                try:
                    publish_consumer_dlq(dead_letter_producer, message, exc, envelope)
                except Exception as dlq_error:
                    raise KafkaWorkerConnectionError(str(dlq_error)) from dlq_error
            try:
                consumer.commit(message=message)
            except KafkaException as exc:
                raise KafkaWorkerConnectionError(str(exc)) from exc
    finally:
        consumer.close()


def reconnect_initial_seconds() -> float:
    """读取首次 Kafka 重连等待时间，防止无效配置导致忙循环。"""
    return positive_seconds("RAG_KAFKA_RECONNECT_INITIAL_SECONDS", 1.0)


def reconnect_max_seconds(initial_seconds: float) -> float:
    """读取 Kafka 重连最大等待时间，确保不小于首次等待时间。"""
    return max(initial_seconds, positive_seconds("RAG_KAFKA_RECONNECT_MAX_SECONDS", 30.0))


def positive_seconds(name: str, default: float) -> float:
    """读取正数秒级配置，非法值使用安全默认值。"""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def is_reconnectable_error(error: object, kafka_error_type: object) -> bool:
    """仅把 Broker 或传输层错误交给重连循环，业务错误保持显式失败。"""
    code_reader = getattr(error, "code", None)
    error_code = code_reader() if callable(code_reader) else code_reader
    reconnectable_codes = {
        code
        for code in (
            getattr(kafka_error_type, "_ALL_BROKERS_DOWN", None),
            getattr(kafka_error_type, "_TRANSPORT", None),
            getattr(kafka_error_type, "_TIMED_OUT", None),
        )
        if code is not None
    }
    return error_code in reconnectable_codes


def is_connection_exception(error: Exception) -> bool:
    """识别 producer / handler 透出的 Broker 连接故障，交给外层重连循环处理。"""
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "all brokers down",
            "broker transport failure",
            "connection refused",
            "network is unreachable",
            "local: timed out",
            "message timed out",
            "_all_brokers_down",
            "_transport",
            "kafka 消息发送超时",
            "消息未投递",
        )
    )


def publish_consumer_dlq(producer: KafkaJsonProducer, message, error: Exception, envelope: KafkaEnvelope | None) -> None:
    """将无法交给业务 handler 的消息转为脱敏 DLQ envelope，成功后 caller 才提交原 offset。"""
    source_payload = envelope.payload if envelope is not None and envelope.payload is not None else {}
    payload: dict[str, object] = {
        "topic": message.topic(),
        "sourceTopic": message.topic(),
        "partition": message.partition(),
        "offset": message.offset(),
        "attempt": envelope.attempt if envelope is not None else 0,
        "errorCode": "RAG_KAFKA_ENVELOPE_INVALID" if envelope is None else "RAG_KAFKA_CONSUMER_FAILED",
        "errorMessage": safe_consumer_error_summary(error),
        "messageHash": message_hash(message.value()),
    }
    if envelope is not None:
        payload["sourceMessageId"] = envelope.messageId
        payload["sourceMessageType"] = envelope.messageType
        payload["sourceIdempotencyKey"] = envelope.idempotencyKey
    for key in ("jobId", "materialId", "canonicalDocumentId", "stagingDocumentId", "requestVersion", "uploadId"):
        if key in source_payload:
            payload[key] = source_payload[key]
    partition_key = str(payload.get("canonicalDocumentId") or payload.get("jobId") or f"{message.topic()}-{message.partition()}")
    out = build_envelope(
        message_type="RAG_KAFKA_CONSUMER_DLQ",
        partition_key=partition_key,
        idempotency_key=f"RAG_CONSUMER_DLQ:{message.topic()}:{message.partition()}:{message.offset()}:v1",
        payload=payload,
        attempt=int(payload["attempt"] or 0),
        original_message_id=(envelope.originalMessageId or envelope.messageId) if envelope is not None else None,
    )
    producer.send(
        os.getenv("RAG_KAFKA_TOPIC_INDEX_DLQ", "rag.material.index.dlq.v1"),
        partition_key,
        out,
    )


def message_hash(value: object) -> str:
    """仅保存原消息哈希用于定位，禁止把可能含正文的原始 value 写入 DLQ。"""
    if isinstance(value, bytes):
        raw = value
    else:
        raw = str(value if value is not None else "").encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def safe_consumer_error_summary(error: Exception) -> str:
    """DLQ 只保留异常类别，Pydantic/Jackson 等错误文本可能回显原始资料正文。"""
    if error.__class__.__name__ == "ValidationError":
        return "Kafka envelope 格式或字段校验失败"
    return f"Kafka 消费处理失败：{error.__class__.__name__}"


if __name__ == "__main__":
    main()
