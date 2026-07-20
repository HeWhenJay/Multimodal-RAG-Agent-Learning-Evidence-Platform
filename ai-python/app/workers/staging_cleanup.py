from __future__ import annotations

from rag.indexes.pgvector_store import PgVectorRagStore


class StagingIndexCleanupWorker:
    """定时清理已提升或失败超期的 staging RAG 索引。"""

    def __init__(self, store: PgVectorRagStore | None = None) -> None:
        self.store = store or PgVectorRagStore()

    def cleanup(self) -> dict[str, int]:
        """复用 pgvector 存储层的保留期与事务语义执行清理。"""
        return self.store.cleanup_staging_indexes()
