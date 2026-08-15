"""为外部模型和数据库请求提供可配置的进程级 I/O 并发保护。"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
import asyncio
import os
import threading
from typing import Any, TypeVar

import httpx


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


class AsyncModelNetworkError(RuntimeError):
    """真正异步的模型 HTTP 请求发生可映射的网络错误。"""


@dataclass(frozen=True)
class AsyncModelHttpConfig:
    """约束共享异步模型 HTTP 连接池与在途请求数量。"""

    max_connections: int = 32
    max_keepalive_connections: int = 16
    keepalive_expiry_seconds: float = 30.0
    max_in_flight: int = 16
    acquire_timeout_seconds: float = 5.0
    connect_timeout_seconds: float = 10.0
    default_timeout_seconds: float = 45.0


def configured_positive_float(env_name: str, default: float, *, maximum: float = 3600.0) -> float:
    """读取正浮点配置，非法值回退默认值并限制异常大的等待预算。"""
    raw = os.getenv(env_name, str(default))
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        parsed = default
    if parsed <= 0:
        parsed = default
    return min(maximum, parsed)


def async_model_http_config() -> AsyncModelHttpConfig:
    """从环境变量构造共享异步模型 HTTP 配置。"""
    max_connections = configured_io_workers(
        "ASYNC_MODEL_HTTP_MAX_CONNECTIONS",
        default=32,
        maximum=128,
    )
    max_keepalive_connections = min(
        max_connections,
        configured_io_workers(
            "ASYNC_MODEL_HTTP_MAX_KEEPALIVE_CONNECTIONS",
            default=16,
            maximum=128,
        ),
    )
    return AsyncModelHttpConfig(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive_connections,
        keepalive_expiry_seconds=configured_positive_float(
            "ASYNC_MODEL_HTTP_KEEPALIVE_EXPIRY_SECONDS",
            30.0,
        ),
        max_in_flight=configured_io_workers(
            "ASYNC_MODEL_HTTP_MAX_IN_FLIGHT",
            default=16,
            maximum=128,
        ),
        acquire_timeout_seconds=configured_positive_float(
            "ASYNC_MODEL_HTTP_ACQUIRE_TIMEOUT_SECONDS",
            5.0,
        ),
        connect_timeout_seconds=configured_positive_float(
            "ASYNC_MODEL_HTTP_CONNECT_TIMEOUT_SECONDS",
            10.0,
        ),
        default_timeout_seconds=configured_positive_float(
            "ASYNC_MODEL_HTTP_DEFAULT_TIMEOUT_SECONDS",
            45.0,
        ),
    )


class AsyncModelHttpClientPool:
    """复用单事件循环内的 AsyncClient，并提供连接与在途双重限流。"""

    def __init__(
        self,
        config: AsyncModelHttpConfig | None = None,
        *,
        client_factory: Callable[[httpx.Limits, httpx.Timeout], httpx.AsyncClient] | None = None,
    ) -> None:
        self.config = config or async_model_http_config()
        self._client_factory = client_factory or self._build_client
        self._client: httpx.AsyncClient | None = None
        self._owner_loop: asyncio.AbstractEventLoop | None = None
        self._semaphore: asyncio.Semaphore | None = None
        self._state_lock = threading.Lock()

    @property
    def is_started(self) -> bool:
        """返回连接池是否已在所属事件循环启动。"""
        return self._client is not None

    @property
    def is_closed(self) -> bool:
        """返回连接池当前是否没有可用客户端。"""
        return self._client is None

    def _build_client(self, limits: httpx.Limits, timeout: httpx.Timeout) -> httpx.AsyncClient:
        """按统一连接数和 keep-alive 配置创建唯一 AsyncClient。"""
        return httpx.AsyncClient(limits=limits, timeout=timeout)

    async def start(self) -> httpx.AsyncClient:
        """在当前事件循环创建或复用共享客户端，拒绝跨事件循环误用。"""
        loop = asyncio.get_running_loop()
        with self._state_lock:
            if self._client is not None:
                if self._owner_loop is not loop:
                    raise RuntimeError("异步模型 HTTP 客户端不能跨事件循环复用")
                return self._client
            limits = httpx.Limits(
                max_connections=self.config.max_connections,
                max_keepalive_connections=self.config.max_keepalive_connections,
                keepalive_expiry=self.config.keepalive_expiry_seconds,
            )
            timeout = self._request_timeout(self.config.default_timeout_seconds)
            self._client = self._client_factory(limits, timeout)
            self._owner_loop = loop
            self._semaphore = asyncio.Semaphore(self.config.max_in_flight)
            return self._client

    async def request(
        self,
        method: str,
        url: str,
        *,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """在有界并发和总超时内执行真正异步的 HTTP 请求。"""
        client = await self.start()
        semaphore = self._semaphore
        if semaphore is None:
            raise RuntimeError("异步模型 HTTP 并发保护尚未初始化")
        try:
            await asyncio.wait_for(
                semaphore.acquire(),
                timeout=self.config.acquire_timeout_seconds,
            )
        except TimeoutError as exc:
            raise LlmIoTimeoutError("等待异步模型 HTTP 并发槽超时") from exc

        total_timeout = max(
            0.001,
            float(timeout_seconds or self.config.default_timeout_seconds),
        )
        try:
            return await asyncio.wait_for(
                client.request(
                    method,
                    url,
                    timeout=self._request_timeout(total_timeout),
                    **kwargs,
                ),
                timeout=total_timeout,
            )
        except TimeoutError as exc:
            raise LlmIoTimeoutError("异步模型 HTTP 请求超过调用预算") from exc
        except httpx.TimeoutException as exc:
            raise LlmIoTimeoutError("异步模型 HTTP 请求超时") from exc
        except httpx.RequestError as exc:
            raise AsyncModelNetworkError(f"异步模型 HTTP 网络请求失败: {exc.__class__.__name__}") from exc
        finally:
            semaphore.release()

    async def close(self) -> None:
        """在所属事件循环关闭连接池，并允许后续生命周期重新启动。"""
        loop = asyncio.get_running_loop()
        with self._state_lock:
            client = self._client
            if client is None:
                return
            if self._owner_loop is not loop:
                raise RuntimeError("异步模型 HTTP 客户端必须在所属事件循环关闭")
        try:
            await client.aclose()
        finally:
            with self._state_lock:
                self._client = None
                self._owner_loop = None
                self._semaphore = None

    def _request_timeout(self, total_seconds: float) -> httpx.Timeout:
        """同时约束连接、连接池等待与整体读写预算。"""
        total = max(0.001, float(total_seconds))
        return httpx.Timeout(
            total,
            connect=min(total, self.config.connect_timeout_seconds),
            pool=min(total, self.config.acquire_timeout_seconds),
        )


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


async_model_http_pool = AsyncModelHttpClientPool()


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
