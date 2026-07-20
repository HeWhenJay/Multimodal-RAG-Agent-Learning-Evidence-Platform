"""兼容旧导入路径；Kafka worker 的正式实现已迁入 `app.workers`。"""

from app.workers.kafka_worker import (  # noqa: F401
    KafkaWorkerConnectionError,
    is_connection_exception,
    is_reconnectable_error,
    main,
    message_hash,
    positive_seconds,
    publish_consumer_dlq,
    reconnect_initial_seconds,
    reconnect_max_seconds,
    run_consumer_forever,
    run_consumer_loop,
    safe_consumer_error_summary,
)


if __name__ == "__main__":
    main()
