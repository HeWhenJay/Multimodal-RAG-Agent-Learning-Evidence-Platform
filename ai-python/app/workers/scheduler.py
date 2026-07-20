from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import signal
import threading
import time
from collections.abc import Callable
from typing import Any

from app.core.runtime_config import load_runtime_config, parse_args
from app.workers.outbox_publisher import RagOutboxPublisher
from app.workers.staging_cleanup import StagingIndexCleanupWorker


LOGGER = logging.getLogger(__name__)


@dataclass
class FixedDelayTask:
    """固定延迟任务：本轮结束后再等待指定时长。"""

    name: str
    callback: Callable[[], Any]
    delay_seconds: float
    next_run_at: float = 0.0


class FixedDelayScheduler:
    """无外部依赖的轻量 cron 循环，适合耐久任务的独立 worker 进程。"""

    def __init__(
        self,
        tasks: list[FixedDelayTask],
        *,
        clock: Callable[[], float] = time.monotonic,
        logger: logging.Logger | None = None,
    ) -> None:
        self.tasks = tasks
        self._clock = clock
        self._logger = logger or LOGGER

    def run_due(self, now: float | None = None) -> list[str]:
        """执行已到期任务；某一任务失败不会阻塞其它任务或下次重试。"""
        current = self._clock() if now is None else now
        executed: list[str] = []
        for task in self.tasks:
            if current < task.next_run_at:
                continue
            executed.append(task.name)
            try:
                task.callback()
            except Exception as exc:
                self._logger.warning("Python cron 任务失败: name=%s, errorType=%s", task.name, exc.__class__.__name__)
            task.next_run_at = self._clock() + task.delay_seconds
        return executed

    def run_forever(self, stop_event: threading.Event) -> None:
        """持续运行直到收到进程信号，并在空闲时按最近到期时间等待。"""
        while not stop_event.is_set():
            self.run_due()
            stop_event.wait(self.next_wait_seconds())

    def next_wait_seconds(self) -> float:
        """计算到最近任务的等待时间，避免空闲循环占用 CPU。"""
        if not self.tasks:
            return 1.0
        now = self._clock()
        wait_seconds = min(task.next_run_at - now for task in self.tasks)
        return max(0.05, min(wait_seconds, positive_seconds("AI_CRON_POLL_INTERVAL_SECONDS", 0.5)))


def build_default_scheduler(
    *,
    publisher_factory: Callable[[], RagOutboxPublisher] = RagOutboxPublisher,
    cleanup_factory: Callable[[], StagingIndexCleanupWorker] = StagingIndexCleanupWorker,
) -> FixedDelayScheduler:
    """按运行配置注册当前已迁入 Python 的耐久定时任务。"""
    tasks: list[FixedDelayTask] = []
    if read_bool_env("RAG_KAFKA_ENABLED", False) and read_bool_env("RAG_OUTBOX_PUBLISHER_ENABLED", False):
        publisher = publisher_factory()
        tasks.append(
            FixedDelayTask(
                name="rag-outbox-publisher",
                callback=publisher.publish_due_events,
                delay_seconds=positive_milliseconds("RAG_OUTBOX_PUBLISH_FIXED_DELAY_MS", 1000) / 1000,
            )
        )
    if read_bool_env("RAG_STAGING_CLEANUP_ENABLED", False):
        cleanup_worker = cleanup_factory()
        tasks.append(
            FixedDelayTask(
                name="rag-staging-cleanup",
                callback=cleanup_worker.cleanup,
                delay_seconds=positive_seconds("RAG_STAGING_CLEANUP_FIXED_DELAY_SECONDS", 3600.0),
            )
        )
    return FixedDelayScheduler(tasks)


def has_enabled_jobs() -> bool:
    """供 `run.py` 判断是否需要启动 cron 子进程。"""
    return (
        read_bool_env("RAG_KAFKA_ENABLED", False) and read_bool_env("RAG_OUTBOX_PUBLISHER_ENABLED", False)
    ) or read_bool_env("RAG_STAGING_CLEANUP_ENABLED", False)


def main(argv: list[str] | None = None) -> None:
    """启动独立 cron worker，配置加载规则与 FastAPI 服务保持一致。"""
    load_runtime_config(parse_args(argv))
    scheduler = build_default_scheduler()
    if not scheduler.tasks:
        print("Python cron 未启用任何任务，进程退出")
        return

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    print("Python cron 已启动: " + ", ".join(task.name for task in scheduler.tasks))
    scheduler.run_forever(stop_event)


def _install_signal_handlers(stop_event: threading.Event) -> None:
    """在主线程中接收终止信号，确保 `run.py` 能有序停止 cron 子进程。"""
    def stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
    except ValueError:
        # 单元测试或嵌入式调用不在主线程时，不注册信号仍可由 stop_event 停止。
        return


def read_bool_env(name: str, default: bool) -> bool:
    """读取布尔环境变量，格式与运行入口的配置约定一致。"""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def positive_seconds(name: str, default: float) -> float:
    """读取正秒数配置，避免错误配置造成 busy loop 或永久等待。"""
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def positive_milliseconds(name: str, default: int) -> int:
    """读取正毫秒配置，便于沿用 Java `fixedDelayString` 的默认值。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


if __name__ == "__main__":
    main()
