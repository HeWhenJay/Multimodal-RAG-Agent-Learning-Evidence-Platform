"""把索引后的复习 LLM 生成与 RAG Kafka 终态写回解耦。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
import logging
import threading
from typing import Any

from app.core.io_concurrency import configured_io_workers


LOGGER = logging.getLogger(__name__)
DEFAULT_REVIEW_SYNC_WORKERS = 16
MAX_REVIEW_SYNC_WORKERS = 64


class ReviewSyncDispatcher:
    """使用独立线程池执行索引后的复习生成，并按资料 ID 去重待执行任务。"""

    def __init__(
        self,
        callback: Callable[[int], Any],
        *,
        max_workers: int | None = None,
    ) -> None:
        self.callback = callback
        self.max_workers = resolve_review_sync_workers(max_workers)
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="rag-review-sync",
        )
        self._pending_material_ids: set[int] = set()
        self._lock = threading.Lock()

    def submit(self, material_id: int) -> bool:
        """非阻塞提交一条复习生成任务；同一资料已排队时直接复用。"""
        normalized_id = int(material_id)
        with self._lock:
            if normalized_id in self._pending_material_ids:
                return False
            self._pending_material_ids.add(normalized_id)
        try:
            self._executor.submit(self._run, normalized_id)
        except Exception:
            with self._lock:
                self._pending_material_ids.discard(normalized_id)
            raise
        return True

    def _run(self, material_id: int) -> None:
        """在线程池执行真实 LLM 流程，失败只影响复习派生能力。"""
        try:
            self.callback(material_id)
        except Exception:
            LOGGER.exception("RAG 入库后异步生成复习卡片失败，materialId=%s", material_id)
        finally:
            with self._lock:
                self._pending_material_ids.discard(material_id)

    def close(self, *, wait: bool = True) -> None:
        """供受监督 worker 退出或测试回收线程池。"""
        self._executor.shutdown(wait=wait, cancel_futures=not wait)


def resolve_review_sync_workers(value: int | None = None) -> int:
    """读取索引后复习生成并发，LLM/数据库等待阶段默认按 2n=16。"""
    if value is None:
        return configured_io_workers(
            "RAG_REVIEW_SYNC_WORKERS",
            default=DEFAULT_REVIEW_SYNC_WORKERS,
            maximum=MAX_REVIEW_SYNC_WORKERS,
        )
    return max(1, min(MAX_REVIEW_SYNC_WORKERS, int(value)))
