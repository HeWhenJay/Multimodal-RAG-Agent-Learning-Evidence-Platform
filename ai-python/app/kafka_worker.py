from __future__ import annotations

import os
import signal
import time
from typing import Callable

from app.core.runtime_config import load_runtime_config, parse_args
from app.schemas.kafka import KafkaEnvelope
from rag.kafka.worker import RagKafkaIndexWorker, RagKafkaPromoteWorker, RagKafkaRetryScheduler, RetryNotReady


def main() -> None:
    """启动 RAG Kafka worker，默认同时处理 index 和 promote 请求。"""
    load_runtime_config(parse_args(None))
    if os.getenv("RAG_KAFKA_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise RuntimeError("RAG_KAFKA_ENABLED 未开启，已拒绝启动 Kafka worker")
    index_worker = RagKafkaIndexWorker()
    promote_worker = RagKafkaPromoteWorker(producer=index_worker.producer)
    retry_scheduler = RagKafkaRetryScheduler(producer=index_worker.producer)
    run_consumer_loop(
        {
            os.getenv("RAG_KAFKA_TOPIC_INDEX_REQUEST", "rag.material.index.request.v1"): index_worker.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_PROMOTE_REQUEST", "rag.material.index.promote.request.v1"): promote_worker.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_1M", "rag.material.index.retry.1m.v1"): retry_scheduler.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_10M", "rag.material.index.retry.10m.v1"): retry_scheduler.handle_envelope,
            os.getenv("RAG_KAFKA_TOPIC_INDEX_RETRY_1H", "rag.material.index.retry.1h.v1"): retry_scheduler.handle_envelope,
        }
    )


def run_consumer_loop(handlers: dict[str, Callable[[KafkaEnvelope], object]]) -> None:
    """使用 manual commit 消费 Kafka；handler 成功返回后才提交 offset。"""
    try:
        from confluent_kafka import Consumer
        from confluent_kafka import TopicPartition
    except ImportError as exc:
        raise RuntimeError("使用 RAG Kafka worker 需要安装 confluent-kafka") from exc

    running = True

    def stop(_signum, _frame) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    consumer = Consumer(
        {
            "bootstrap.servers": os.getenv("RAG_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"),
            "group.id": os.getenv("RAG_KAFKA_GROUP_ID", "rag-python-index-workers"),
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe(list(handlers))
    try:
        while running:
            message = consumer.poll(1.0)
            if message is None:
                continue
            if message.error():
                raise RuntimeError(str(message.error()))
            topic = message.topic()
            handler = handlers.get(topic)
            if handler is None:
                continue
            envelope = KafkaEnvelope.model_validate_json(message.value())
            try:
                handler(envelope)
            except RetryNotReady as exc:
                topic_partition = TopicPartition(topic, message.partition(), message.offset())
                consumer.pause([TopicPartition(topic, message.partition())])
                time.sleep(min(exc.delay_seconds, float(os.getenv("RAG_KAFKA_RETRY_MAX_SLEEP_SECONDS", "30"))))
                consumer.seek(topic_partition)
                consumer.resume([TopicPartition(topic, message.partition())])
                continue
            consumer.commit(message=message)
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
