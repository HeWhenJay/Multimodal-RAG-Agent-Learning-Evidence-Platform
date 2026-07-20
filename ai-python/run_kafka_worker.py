"""兼容旧的单文件启动方式；正式 Kafka worker 位于 `app.workers`。"""

from app.workers.kafka_worker import main


if __name__ == "__main__":
    main()
