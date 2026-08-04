"""为外部模型和数据库请求提供可配置的进程级 I/O 并发保护。"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
import os
import threading
from collections.abc import Iterator


DEFAULT_IO_WORKERS = 8
MAX_IO_WORKERS = 10


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


class ProcessIoLimiter:
    """按能力名称限制整个 Python 进程中的并发外部请求数。"""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._active: defaultdict[str, int] = defaultdict(int)

    @contextmanager
    def slot(self, group: str, limit: int) -> Iterator[None]:
        """等待并占用一个并发槽，退出时唤醒同组等待线程。"""
        bounded_limit = max(1, int(limit))
        with self._condition:
            self._condition.wait_for(lambda: self._active[group] < bounded_limit)
            self._active[group] += 1
        try:
            yield
        finally:
            with self._condition:
                self._active[group] = max(0, self._active[group] - 1)
                self._condition.notify_all()


process_io_limiter = ProcessIoLimiter()
