"""Python-only RAG Kafka 状态消费者。"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Any, Protocol

from app.repositories.rag_job import RagJobRepository
from app.schemas.kafka import KafkaEnvelope
from app.workers.review_sync_dispatcher import ReviewSyncDispatcher


logger = logging.getLogger(__name__)


class RagKafkaStateRepository(Protocol):
    """Kafka 状态写回最小仓储契约，方便在测试中替换 PostgreSQL。"""

    def consume_progress(self, envelope: KafkaEnvelope) -> bool: ...

    def consume_index_result(self, envelope: KafkaEnvelope) -> dict[str, Any] | None: ...

    def consume_promote_result(self, envelope: KafkaEnvelope) -> bool: ...

    def consume_dlq(self, envelope: KafkaEnvelope) -> bool: ...


class RagKafkaStateWriter:
    """把 progress、result、promote result 与 DLQ 写回 PostgreSQL。"""

    def __init__(
        self,
        repository: RagKafkaStateRepository | None = None,
        *,
        review_sync: Callable[[int], Any] | None = None,
        review_dispatcher: ReviewSyncDispatcher | None = None,
    ) -> None:
        self.repository = repository or RagJobRepository()
        self.review_dispatcher = review_dispatcher or (
            ReviewSyncDispatcher(review_sync) if review_sync is not None else None
        )

    def handle_progress(self, envelope: KafkaEnvelope) -> bool:
        """消费并去重用户可见的索引进度。"""
        return self.repository.consume_progress(envelope)

    def handle_index_result(self, envelope: KafkaEnvelope) -> dict[str, Any] | None:
        """消费 staging 结果；Kafka 模式会在同一事务写入 promote Outbox。"""
        return self.repository.consume_index_result(envelope)

    def handle_promote_result(self, envelope: KafkaEnvelope) -> bool:
        """先消费 promote 终态，再异步衔接资料复习生成。"""
        persisted = self.repository.consume_promote_result(envelope)
        payload = envelope.payload or {}
        if self.review_dispatcher is not None and str(payload.get("status") or "") == "SUCCEEDED":
            try:
                material_id = int(payload.get("materialId"))
                self.review_dispatcher.submit(material_id)
            except (TypeError, ValueError):
                logger.warning("RAG 提升成功事件缺少合法 materialId，已跳过复习生成")
            except Exception:
                # 调度失败也不能回滚或阻断已经可检索的资料。
                logger.exception("RAG 入库后复习生成任务提交失败，materialId=%s", payload.get("materialId"))
        return persisted

    def handle_dlq(self, envelope: KafkaEnvelope) -> bool:
        """消费死信摘要并将 active job 收敛为终态失败。"""
        return self.repository.consume_dlq(envelope)
