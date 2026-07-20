"""Python-only RAG Kafka 状态消费者。"""

from __future__ import annotations

from typing import Any, Protocol

from app.repositories.rag_job import RagJobRepository
from app.schemas.kafka import KafkaEnvelope


class RagKafkaStateRepository(Protocol):
    """Kafka 状态写回最小仓储契约，方便在测试中替换 PostgreSQL。"""

    def consume_progress(self, envelope: KafkaEnvelope) -> bool: ...

    def consume_index_result(self, envelope: KafkaEnvelope) -> dict[str, Any] | None: ...

    def consume_promote_result(self, envelope: KafkaEnvelope) -> bool: ...

    def consume_dlq(self, envelope: KafkaEnvelope) -> bool: ...


class RagKafkaStateWriter:
    """把 progress、result、promote result 与 DLQ 写回 PostgreSQL。"""

    def __init__(self, repository: RagKafkaStateRepository | None = None) -> None:
        self.repository = repository or RagJobRepository()

    def handle_progress(self, envelope: KafkaEnvelope) -> bool:
        """消费并去重用户可见的索引进度。"""
        return self.repository.consume_progress(envelope)

    def handle_index_result(self, envelope: KafkaEnvelope) -> dict[str, Any] | None:
        """消费 staging 结果；Kafka 模式会在同一事务写入 promote Outbox。"""
        return self.repository.consume_index_result(envelope)

    def handle_promote_result(self, envelope: KafkaEnvelope) -> bool:
        """消费 promote 终态并更新资料可检索状态。"""
        return self.repository.consume_promote_result(envelope)

    def handle_dlq(self, envelope: KafkaEnvelope) -> bool:
        """消费死信摘要并将 active job 收敛为终态失败。"""
        return self.repository.consume_dlq(envelope)
