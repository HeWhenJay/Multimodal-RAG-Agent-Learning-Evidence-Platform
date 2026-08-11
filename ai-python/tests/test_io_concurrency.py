"""外部模型 I/O 线程池行为测试。"""

import asyncio
import threading
import time

import pytest

from app.core.io_concurrency import LlmIoTimeoutError, run_llm_io, run_llm_io_async


def test_run_llm_io_uses_dedicated_worker_thread() -> None:
    """同步调用方也必须把实际模型 I/O 放入 llm-io worker。"""
    caller_thread = threading.current_thread().name

    worker_thread = run_llm_io(lambda: threading.current_thread().name)

    assert worker_thread.startswith("llm-io")
    assert worker_thread != caller_thread


def test_run_llm_io_async_does_not_block_event_loop() -> None:
    """异步入口应等待同一专用线程池，并保留事件循环线程。"""
    async def run() -> tuple[str, str]:
        event_loop_thread = threading.current_thread().name
        worker_thread = await run_llm_io_async(lambda: threading.current_thread().name)
        return event_loop_thread, worker_thread

    event_loop_thread, worker_thread = asyncio.run(run())

    assert worker_thread.startswith("llm-io")
    assert worker_thread != event_loop_thread


def test_nested_llm_io_call_executes_inline_without_deadlock() -> None:
    """线程池 worker 内的复用调用应就地执行，避免嵌套等待耗尽 worker。"""
    outer_thread, inner_thread = run_llm_io(
        lambda: (threading.current_thread().name, run_llm_io(lambda: threading.current_thread().name))
    )

    assert outer_thread == inner_thread


def test_run_llm_io_timeout_releases_caller_without_waiting_forever() -> None:
    """调用方预算耗尽时应立即退出等待，底层测试任务随后自行释放。"""
    started = threading.Event()
    release = threading.Event()

    def block() -> None:
        started.set()
        release.wait(timeout=1)

    before = time.monotonic()
    try:
        with pytest.raises(LlmIoTimeoutError):
            run_llm_io(block, timeout_seconds=0.03)
    finally:
        release.set()

    assert started.is_set()
    assert time.monotonic() - before < 0.2
