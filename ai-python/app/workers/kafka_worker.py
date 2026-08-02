"""RAG Kafka 消费 worker 的正式模块入口。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import os
import signal
import time
import hashlib
import threading
from typing import Any, Callable

from app.core.runtime_config import load_runtime_config, parse_args
from app.repositories.rag_job import RagJobRepository
from app.schemas.kafka import KafkaEnvelope
from app.workers.rag_kafka_state import RagKafkaStateWriter
from rag.kafka.producer import KafkaJsonProducer, build_envelope
from rag.kafka.worker import RagKafkaIndexWorker, RagKafkaPromoteWorker, RagKafkaRetryScheduler, RetryNotReady


class KafkaWorkerConnectionError(RuntimeError):
    """Kafka Broker 连接或传输层暂时不可用。"""


@dataclass(frozen=True)
class MessageHandlingOutcome:
    """后台 handler 返回给 poll 线程的 offset 处理决策。"""

    action: str
    retry_delay_seconds: float = 0.0


@dataclass
class InFlightMessage:
    """同一 topic-partition 上唯一在途的 Kafka 消息。"""

    message: Any
    future: Future[MessageHandlingOutcome]


class KafkaMessageDispatcher:
    """让 poll 线程持续心跳，并按分区串行提交已完成消息的 offset。"""

    def __init__(
        self,
        *,
        consumer: Any,
        handlers: dict[str, Callable[[KafkaEnvelope], object]],
        topic_partition_type: Any,
        dead_letter_producer: KafkaJsonProducer,
        worker_count: int,
        control_worker_count: int,
        retry_max_delay_seconds: float,
    ) -> None:
        self.consumer = consumer
        self.handlers = handlers
        self.topic_partition_type = topic_partition_type
        self.dead_letter_producer = dead_letter_producer
        self.worker_count = max(1, worker_count)
        self.control_worker_count = max(1, control_worker_count)
        self.long_task_topics = {
            os.getenv("RAG_KAFKA_TOPIC_INDEX_REQUEST", "rag.material.index.request.v1")
        }
        self.retry_max_delay_seconds = max(0.1, retry_max_delay_seconds)
        self.executor = ThreadPoolExecutor(
            max_workers=self.worker_count + self.control_worker_count,
            thread_name_prefix="rag-kafka-handler",
        )
        self.in_flight: dict[tuple[str, int], InFlightMessage] = {}
        self.pending: dict[tuple[str, int], Any] = {}
        self.retry_waiting: dict[tuple[str, int], tuple[Any, float]] = {}
        self.fatal_error: KafkaWorkerConnectionError | None = None

    def accept(self, message: Any) -> None:
        """暂停消息所属分区；只有真正空闲的执行槽才启动 handler。"""
        key = message_partition_key(message)
        if key in self.in_flight or key in self.pending or key in self.retry_waiting:
            raise KafkaWorkerConnectionError(f"Kafka 分区出现多条并发在途消息：{key[0]}-{key[1]}")
        self.consumer.pause([self._topic_partition(message)])
        if self._can_submit(message):
            self._submit(key, message)
        else:
            self.pending[key] = message

    def advance(self) -> None:
        """在 poll 线程收割后台结果，成功后同步提交并恢复对应分区。"""
        completed = [key for key, item in self.in_flight.items() if item.future.done()]
        for key in completed:
            item = self.in_flight.pop(key)
            try:
                outcome = item.future.result()
            except KafkaWorkerConnectionError as exc:
                self.fatal_error = exc
                continue
            except Exception as exc:
                self.fatal_error = KafkaWorkerConnectionError(str(exc))
                continue
            if outcome.action == "RETRY":
                delay = min(self.retry_max_delay_seconds, max(0.1, outcome.retry_delay_seconds))
                self.retry_waiting[key] = (item.message, time.monotonic() + delay)
                continue
            try:
                self.consumer.commit(message=item.message, asynchronous=False)
                self.consumer.resume([self._topic_partition(item.message)])
            except Exception as exc:
                self.fatal_error = KafkaWorkerConnectionError(str(exc))

        now = time.monotonic()
        for key, (message, ready_at) in list(self.retry_waiting.items()):
            if ready_at <= now:
                self.retry_waiting.pop(key, None)
                self.pending[key] = message
        self._fill_available_slots()

    def discard_not_started(self) -> None:
        """停止或连接故障时丢弃未执行消息，保留未提交 offset 交给下一实例。"""
        self.pending.clear()
        self.retry_waiting.clear()

    @property
    def has_active_handlers(self) -> bool:
        return bool(self.in_flight)

    def close(self) -> None:
        """调用方保持 poll 直到 handler 结束后，再关闭线程池。"""
        self.executor.shutdown(wait=True, cancel_futures=True)

    def _fill_available_slots(self) -> None:
        if self.fatal_error is not None:
            return
        while self.pending:
            candidate = next(
                ((key, message) for key, message in self.pending.items() if self._can_submit(message)),
                None,
            )
            if candidate is None:
                return
            key, _message = candidate
            message = self.pending.pop(key)
            self._submit(key, message)

    def _can_submit(self, message: Any) -> bool:
        """长任务使用独立上限，始终为状态与终态消息保留控制容量。"""
        if len(self.in_flight) >= self.worker_count + self.control_worker_count:
            return False
        if message.topic() not in self.long_task_topics:
            return True
        active_long_tasks = sum(
            1
            for item in self.in_flight.values()
            if item.message.topic() in self.long_task_topics
        )
        return active_long_tasks < self.worker_count

    def _submit(self, key: tuple[str, int], message: Any) -> None:
        handler = self.handlers.get(message.topic())
        if handler is None:
            raise KafkaWorkerConnectionError(f"Kafka topic 缺少 handler：{message.topic()}")
        future = self.executor.submit(
            process_consumer_message,
            handler,
            message,
            self.dead_letter_producer,
        )
        self.in_flight[key] = InFlightMessage(message=message, future=future)

    def _topic_partition(self, message: Any) -> Any:
        return self.topic_partition_type(message.topic(), message.partition())


def main() -> None:
    """启动 Python-only RAG Kafka worker，处理索引及全部 PostgreSQL 状态回写。"""
    load_runtime_config(parse_args(None))
    if os.getenv("RAG_KAFKA_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("RAG_KAFKA_ENABLED 未开启，已拒绝启动 Kafka worker")
    job_repository = RagJobRepository()
    state_writer = RagKafkaStateWriter(repository=job_repository)
    index_worker = RagKafkaIndexWorker(job_repository=job_repository)
    promote_worker = RagKafkaPromoteWorker(producer=index_worker.producer)
    retry_scheduler = RagKafkaRetryScheduler(producer=index_worker.producer)
    run_consumer_forever(
        {
            os.getenv("RAG_KAFKA_TOPIC_INDEX_REQUEST", "rag.material.index.request.v1"): index_worker.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_PROMOTE_REQUEST", "rag.material.index.promote.request.v1"): promote_worker.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_1M", "rag.material.index.retry.1m.v1"): retry_scheduler.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_10M", "rag.material.index.retry.10m.v1"): retry_scheduler.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_1H", "rag.material.index.retry.1h.v1"): retry_scheduler.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_PROGRESS", "rag.material.index.progress.v1"): state_writer.handle_progress,
            os.getenv("RAG_KAFKA_TOPIC_INDEX_RESULT", "rag.material.index.result.v1"): state_writer.handle_index_result,
            os.getenv("RAG_KAFKA_TOPIC_PROMOTE_RESULT", "rag.material.index.promote.result.v1"): state_writer.handle_promote_result,
            os.getenv("RAG_KAFKA_TOPIC_INDEX_DLQ", "rag.material.index.dlq.v1"): state_writer.handle_dlq,
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


def run_consumer_loop(
    handlers: dict[str, Callable[[KafkaEnvelope], object]],
    *,
    stop_event: threading.Event | None = None,
) -> None:
    """poll 与业务执行解耦；同一分区按完成顺序同步提交 offset。"""
    try:
        from confluent_kafka import Consumer
        from confluent_kafka import KafkaError
        from confluent_kafka import KafkaException
        from confluent_kafka import TopicPartition
    except ImportError as exc:
        raise RuntimeError("使用 RAG Kafka worker 需要安装 confluent-kafka") from exc

    requested_stop = stop_event or threading.Event()

    def stop(_signum, _frame) -> None:
        requested_stop.set()

    try:
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
    except ValueError:
        # 单元测试或嵌入式启动可能不在主线程，由调用方 stop_event 负责退出。
        pass
    try:
        consumer = Consumer(
            {
                "bootstrap.servers": os.getenv("RAG_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"),
                "group.id": os.getenv("RAG_KAFKA_GROUP_ID", "rag-python-index-workers"),
                "enable.auto.commit": False,
                "auto.offset.reset": kafka_auto_offset_reset(),
                # poll 线程不会执行长任务；保留宽松上限兼容 Broker 短暂阻塞和优雅停机。
                "max.poll.interval.ms": positive_milliseconds("RAG_KAFKA_MAX_POLL_INTERVAL_MS", 21_600_000),
            }
        )
    except KafkaException as exc:
        raise KafkaWorkerConnectionError(str(exc)) from exc
    dead_letter_producer = KafkaJsonProducer()
    consumer.subscribe(list(handlers))
    dispatcher = KafkaMessageDispatcher(
        consumer=consumer,
        handlers=handlers,
        topic_partition_type=TopicPartition,
        dead_letter_producer=dead_letter_producer,
        worker_count=kafka_handler_concurrency(),
        control_worker_count=kafka_control_concurrency(),
        retry_max_delay_seconds=positive_seconds("RAG_KAFKA_RETRY_MAX_SLEEP_SECONDS", 30.0),
    )
    stopping = False
    try:
        while not stopping or dispatcher.has_active_handlers:
            dispatcher.advance()
            if dispatcher.fatal_error is not None:
                stopping = True
                dispatcher.discard_not_started()
            if requested_stop.is_set() and not stopping:
                stopping = True
                dispatcher.discard_not_started()
            if dispatcher.fatal_error is not None and not dispatcher.has_active_handlers:
                raise dispatcher.fatal_error
            try:
                # 即使所有业务线程都在解析长视频，poll 仍持续驱动 group heartbeat。
                message = consumer.poll(0.2)
            except KafkaException as exc:
                raise KafkaWorkerConnectionError(str(exc)) from exc
            if message is None or stopping:
                continue
            if message.error():
                if is_reconnectable_error(message.error(), KafkaError):
                    raise KafkaWorkerConnectionError(str(message.error()))
                if message.error().code() == getattr(KafkaError, "_PARTITION_EOF", None):
                    continue
                raise KafkaWorkerConnectionError(str(message.error()))
            dispatcher.accept(message)
    finally:
        dispatcher.discard_not_started()
        dispatcher.close()
        consumer.close()


def process_consumer_message(
    handler: Callable[[KafkaEnvelope], object],
    message: Any,
    dead_letter_producer: KafkaJsonProducer,
) -> MessageHandlingOutcome:
    """在线程池解析并执行消息；poll 线程只接收可提交或延后的结构化结果。"""
    envelope: KafkaEnvelope | None = None
    try:
        envelope = KafkaEnvelope.model_validate_json(message.value())
        handler(envelope)
        return MessageHandlingOutcome("COMMIT")
    except RetryNotReady as exc:
        return MessageHandlingOutcome("RETRY", exc.delay_seconds)
    except Exception as exc:
        if is_connection_exception(exc):
            raise KafkaWorkerConnectionError(str(exc)) from exc
        if message.topic() == os.getenv("RAG_KAFKA_TOPIC_INDEX_DLQ", "rag.material.index.dlq.v1"):
            # DLQ 写回失败不能再投递回同一 DLQ，保留原 offset 等待数据库恢复。
            raise KafkaWorkerConnectionError(str(exc)) from exc
        try:
            publish_consumer_dlq(dead_letter_producer, message, exc, envelope)
        except Exception as dlq_error:
            raise KafkaWorkerConnectionError(str(dlq_error)) from dlq_error
        return MessageHandlingOutcome("COMMIT")


def message_partition_key(message: Any) -> tuple[str, int]:
    """以 topic-partition 限制单分区只有一条在途消息，保持 Kafka 顺序语义。"""
    return str(message.topic()), int(message.partition())


def kafka_handler_concurrency() -> int:
    """限制 Kafka 索引长任务线程数，防止视频解析耗尽 CPU、内存和下载带宽。"""
    try:
        value = int(os.getenv("RAG_KAFKA_HANDLER_CONCURRENCY", "4"))
    except ValueError:
        return 4
    return min(32, max(1, value))


def kafka_control_concurrency() -> int:
    """为 progress/result/promote/DLQ 保留独立线程，避免长任务占满后自阻塞。"""
    try:
        value = int(os.getenv("RAG_KAFKA_CONTROL_CONCURRENCY", "1"))
    except ValueError:
        return 1
    return min(8, max(1, value))


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


def positive_milliseconds(name: str, default: int) -> int:
    """读取 Kafka 长任务最大 poll 间隔，非法值回退到一小时。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def kafka_auto_offset_reset() -> str:
    """读取 Kafka 首次订阅的 offset 策略，仅允许官方支持的两个枚举。"""
    value = os.getenv("RAG_KAFKA_AUTO_OFFSET_RESET", "earliest").strip().lower()
    return value if value in {"earliest", "latest"} else "earliest"


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
