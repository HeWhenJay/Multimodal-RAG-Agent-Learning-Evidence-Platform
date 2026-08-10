"""复习生成耐久恢复 worker。"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import logging
import os
import signal
import socket
import threading
from typing import Any
from uuid import uuid4

from app.core.io_concurrency import configured_io_workers
from app.core.runtime_config import load_runtime_config, parse_args
from app.review.repository import MaterialSourceRecord, ReviewRepository
from app.review.service import ReviewService


LOGGER = logging.getLogger(__name__)
DEFAULT_REVIEW_TASK_BATCH_SIZE = 16
DEFAULT_REVIEW_TASK_STALE_SECONDS = 1200
DEFAULT_REVIEW_TASK_POLL_SECONDS = 2.0


class ReviewGenerationTaskWorker:
    """从复习资料状态表领取排队/中断任务，重启后继续执行模型生成。"""

    def __init__(
        self,
        *,
        repository: ReviewRepository | None = None,
        service: ReviewService | None = None,
        worker_id: str | None = None,
        max_workers: int | None = None,
    ) -> None:
        self.repository = repository or ReviewRepository()
        self.service = service or ReviewService(repository=self.repository)
        self.worker_id = worker_id or build_worker_id()
        self.max_workers = resolve_review_task_workers(max_workers)
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="review-durable-worker",
        )
        self._active: dict[int, Future[Any]] = {}
        self._lock = threading.Lock()

    def run_once(self) -> dict[str, int]:
        """领取一轮复习任务并异步执行，不阻塞 worker 主轮询。"""
        self._remove_finished()
        with self._lock:
            available = max(0, self.max_workers - len(self._active))
        if available <= 0:
            return {"claimed": 0, "submitted": 0, "active": len(self._active)}

        batch_size = min(
            available,
            positive_int("REVIEW_TASK_WORKER_BATCH_SIZE", DEFAULT_REVIEW_TASK_BATCH_SIZE),
        )
        stale_seconds = positive_int(
            "REVIEW_TASK_WORKER_STALE_SECONDS",
            DEFAULT_REVIEW_TASK_STALE_SECONDS,
        )
        with self.repository.transaction() as transaction:
            candidates = transaction.claim_review_generation_candidates(
                batch_size=batch_size,
                stale_seconds=stale_seconds,
            )

        submitted = 0
        for material in candidates:
            with self._lock:
                if material.id in self._active:
                    continue
                future = self._executor.submit(self._run_material, material)
                self._active[material.id] = future
                submitted += 1
        return {"claimed": len(candidates), "submitted": submitted, "active": len(self._active)}

    def close(self, *, wait: bool = False) -> None:
        """关闭恢复线程池；服务停止时不等待模型请求自然结束。"""
        self._executor.shutdown(wait=wait, cancel_futures=not wait)

    def _run_material(self, material: MaterialSourceRecord) -> None:
        """执行一份资料的完整复习生成，成功才替换已有卡片。"""
        try:
            self.service.generate_material(material.id, material.user_id)
            LOGGER.info("复习恢复任务完成: materialId=%s, workerId=%s", material.id, self.worker_id)
        except Exception as exc:  # noqa: BLE001 - 单份任务失败不能停止恢复 worker。
            LOGGER.warning(
                "复习恢复任务失败: materialId=%s, errorType=%s",
                material.id,
                exc.__class__.__name__,
            )

    def _remove_finished(self) -> None:
        """清理已完成 Future，释放下一轮领取槽位。"""
        with self._lock:
            finished = [material_id for material_id, future in self._active.items() if future.done()]
            for material_id in finished:
                self._active.pop(material_id, None)

    def run_forever(self, stop_event: threading.Event) -> None:
        """持续恢复数据库中的复习生成任务，直到收到停止信号。"""
        poll_seconds = positive_float("REVIEW_TASK_WORKER_POLL_SECONDS", DEFAULT_REVIEW_TASK_POLL_SECONDS)
        while not stop_event.is_set():
            try:
                summary = self.run_once()
                if summary["claimed"] or summary["active"]:
                    LOGGER.info(
                        "复习恢复 worker: claimed=%s, submitted=%s, active=%s",
                        summary["claimed"],
                        summary["submitted"],
                        summary["active"],
                    )
            except Exception as exc:  # noqa: BLE001 - 数据库短暂不可用时继续轮询恢复。
                LOGGER.warning("复习恢复 worker 本轮失败: errorType=%s", exc.__class__.__name__)
            stop_event.wait(poll_seconds)


def build_worker_id() -> str:
    """生成不含业务数据的恢复 worker 标识。"""
    return f"{socket.gethostname() or 'python'}-{uuid4().hex[:12]}"


def resolve_review_task_workers(value: int | None = None) -> int:
    """复习模型等待属于 I/O，默认按 2n=16 控制并发。"""
    if value is None:
        return configured_io_workers("REVIEW_TASK_WORKER_CONCURRENCY")
    return max(1, min(64, int(value)))


def positive_int(name: str, default: int) -> int:
    """读取正整数配置，非法值回退安全默认值。"""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def positive_float(name: str, default: float) -> float:
    """读取正数轮询间隔，非法值回退安全默认值。"""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def main(argv: list[str] | None = None) -> None:
    """启动复习生成恢复 worker。"""
    args = parse_args(argv)
    load_runtime_config(args)
    worker = ReviewGenerationTaskWorker()
    stop_event = threading.Event()

    def stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
    except ValueError:
        pass
    try:
        LOGGER.info("复习生成恢复 worker 已启动: workerId=%s, workers=%s", worker.worker_id, worker.max_workers)
        worker.run_forever(stop_event)
    finally:
        worker.close(wait=False)


if __name__ == "__main__":
    main()
