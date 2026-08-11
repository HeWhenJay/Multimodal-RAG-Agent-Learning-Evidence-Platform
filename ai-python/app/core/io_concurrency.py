"""为外部模型和数据库请求提供可配置的进程级 I/O 并发保护。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
import asyncio
import os
import threading
from typing import TypeVar


CONCURRENCY_BASE = 8
DEFAULT_IO_WORKERS = CONCURRENCY_BASE * 2
MAX_IO_WORKERS = 64
DEFAULT_CPU_WORKERS = CONCURRENCY_BASE + 1
MAX_CPU_WORKERS = DEFAULT_CPU_WORKERS
DEFAULT_LLM_IO_WORKERS = DEFAULT_IO_WORKERS
MAX_LLM_IO_WORKERS = 64
T = TypeVar("T")


class LlmIoTimeoutError(TimeoutError):
    """模型 I/O 等待超过调用方预算；已经运行的底层请求仍由 SDK 超时回收。"""


def configured_io_workers(
    env_name: str,
    *,
    default: int = DEFAULT_IO_WORKERS,
    maximum: int = MAX_IO_WORKERS,
) -> int:
    """读取 I/O worker 数，非法配置回退默认值并限制在安全范围。"""
    raw = os.getenv(env_name, str(default))
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


def configured_cpu_workers(
    env_name: str,
    *,
    default: int = DEFAULT_CPU_WORKERS,
    maximum: int = MAX_CPU_WORKERS,
) -> int:
    """读取 CPU/内存密集阶段线程数，默认按 n+1=9 控制本机竞争。"""
    raw = os.getenv(env_name, str(default))
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


class ProcessIoLimiter:
    """按能力名称限制整个 Python 进程中的并发外部请求数。"""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active: defaultdict[str, int] = defaultdict(int)

    @contextmanager
    def slot(
        self,
        group: str,
        limit: int,
        *,
        timeout_seconds: float | None = None,
    ) -> Iterator[None]:
        """等待并占用一个并发槽，超过调用方预算时不再无限排队。"""
        bounded_limit = max(1, int(limit))
        with self._condition:
            timeout = None if timeout_seconds is None else max(0.001, float(timeout_seconds))
            acquired = self._condition.wait_for(
                lambda: self._active[group] < bounded_limit,
                timeout=timeout,
            )
            if not acquired:
                raise LlmIoTimeoutError(f"等待模型并发槽超过 {int(timeout or 0)} 秒")
            self._active[group] += 1
        try:
            yield
        finally:
            with self._condition:
                self._active[group] = max(0, self._active[group] - 1)
                self._condition.notify_all()


process_io_limiter = ProcessIoLimiter()


_llm_io_executor = ThreadPoolExecutor(
    max_workers=configured_io_workers(
        "LLM_IO_MAX_WORKERS",
        default=DEFAULT_LLM_IO_WORKERS,
        maximum=MAX_LLM_IO_WORKERS,
    ),
    thread_name_prefix="llm-io",
)
_llm_io_local = threading.local()


def _execute_llm_action(action: Callable[[], T]) -> T:
    """在线程池 worker 内标记当前调用，供嵌套请求避免再次排队。"""
    _llm_io_local.active = True
    try:
        return action()
    finally:
        _llm_io_local.active = False


def run_llm_io(action: Callable[[], T], *, timeout_seconds: float | None = None) -> T:
    """把同步模型网络请求放入专用 I/O 线程池，并按需限制等待时间。"""
    if bool(getattr(_llm_io_local, "active", False)):
        return action()

    future = _llm_io_executor.submit(_execute_llm_action, action)
    try:
        return future.result(
            timeout=None if timeout_seconds is None else max(0.001, float(timeout_seconds))
        )
    except FutureTimeoutError as exc:
        # cancel 只能阻止尚未开始的任务；已经运行的 SDK 请求依靠自己的 timeout 退出。
        future.cancel()
        raise LlmIoTimeoutError("模型 I/O 等待超过当前调用预算") from exc


async def run_llm_io_async(
    action: Callable[[], T],
    *,
    timeout_seconds: float | None = None,
) -> T:
    """异步等待专用 LLM I/O 线程池，不占用事件循环。"""
    loop = asyncio.get_running_loop()
    future = _llm_io_executor.submit(_execute_llm_action, action)
    wrapped = asyncio.wrap_future(future, loop=loop)
    try:
        if timeout_seconds is None:
            return await wrapped
        return await asyncio.wait_for(wrapped, timeout=max(0.001, float(timeout_seconds)))
    except asyncio.TimeoutError as exc:
        future.cancel()
        raise LlmIoTimeoutError("模型 I/O 异步等待超过当前调用预算") from exc
