"""为复习模型统一提供与 Cockpit 长等待方案一致的显式重试策略。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging
import os
import time
from typing import TypeVar


T = TypeVar("T")
RETRYABLE_COCKPIT_STATUS_CODES = frozenset({401, 403, 408, 409, 425, 429})


@dataclass(frozen=True)
class CockpitRetryPolicy:
    """Cockpit 长等待、启动重试和请求重试参数。"""

    enabled: bool
    stream_open_timeout_seconds: float
    stream_idle_timeout_seconds: float
    bootstrap_retries: int
    request_retries: int
    retry_base_delay_seconds: float
    retry_max_delay_seconds: float
    keepalive_seconds: float
    request_timeout_seconds: float

    @property
    def max_attempts(self) -> int:
        """返回项目客户端向 Cockpit 发起请求的总次数。"""
        return 1 + self.request_retries if self.enabled else 1

    def retry_delay_seconds(self, retry_number: int) -> float:
        """按 Cockpit 的 300-1500ms 窗口计算有界指数退避。"""
        exponent = max(0, retry_number - 1)
        return min(self.retry_max_delay_seconds, self.retry_base_delay_seconds * (2**exponent))


def cockpit_retry_policy() -> CockpitRetryPolicy:
    """每次调用读取环境变量，支持服务启动后调整本地重试方案。"""
    open_timeout = positive_float_environment("REVIEW_COCKPIT_STREAM_OPEN_TIMEOUT_SECONDS", 180.0)
    idle_timeout = positive_float_environment("REVIEW_COCKPIT_STREAM_IDLE_TIMEOUT_SECONDS", 240.0)
    bootstrap_retries = bounded_int_environment("REVIEW_COCKPIT_BOOTSTRAP_RETRIES", 1, 0, 3)
    request_retries = bounded_int_environment("REVIEW_COCKPIT_REQUEST_RETRIES", 1, 0, 3)
    keepalive = positive_float_environment("REVIEW_COCKPIT_KEEPALIVE_SECONDS", 15.0)
    base_delay_ms = positive_float_environment("REVIEW_COCKPIT_RETRY_BASE_DELAY_MS", 300.0)
    max_delay_ms = positive_float_environment("REVIEW_COCKPIT_RETRY_MAX_DELAY_MS", 1500.0)
    derived_timeout = open_timeout * (bootstrap_retries + 1) + idle_timeout + keepalive
    request_timeout = positive_float_environment("REVIEW_EXTRACTION_TIMEOUT_SECONDS", derived_timeout)
    return CockpitRetryPolicy(
        enabled=boolean_environment("REVIEW_COCKPIT_RETRY_ENABLED", True),
        stream_open_timeout_seconds=open_timeout,
        stream_idle_timeout_seconds=idle_timeout,
        bootstrap_retries=bootstrap_retries,
        request_retries=request_retries,
        retry_base_delay_seconds=base_delay_ms / 1000.0,
        retry_max_delay_seconds=max(base_delay_ms, max_delay_ms) / 1000.0,
        keepalive_seconds=keepalive,
        request_timeout_seconds=request_timeout,
    )


def call_cockpit_with_retry(
    call: Callable[[], T],
    *,
    operation: str,
    logger: logging.Logger,
    policy: CockpitRetryPolicy | None = None,
    on_retry: Callable[[Exception, int, int, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """对 Cockpit 可恢复错误重试，尝试耗尽后把原异常交给 DeepSeek 降级层。"""
    active_policy = policy or cockpit_retry_policy()
    max_attempts = active_policy.max_attempts
    for attempt in range(1, max_attempts + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - 仅 OpenAI 可恢复错误会进入重试，其余立即原样抛出。
            if attempt >= max_attempts or not cockpit_error_is_retryable(exc):
                raise
            next_attempt = attempt + 1
            delay = active_policy.retry_delay_seconds(attempt)
            logger.warning(
                "%s Cockpit 请求失败，准备重试 %s/%s：%s，等待 %.1f 秒",
                operation,
                next_attempt,
                max_attempts,
                type(exc).__name__,
                delay,
            )
            if on_retry is not None:
                on_retry(exc, next_attempt, max_attempts, delay)
            sleep(delay)
    raise RuntimeError("Cockpit 重试循环未返回结果")


def cockpit_error_is_retryable(error: Exception) -> bool:
    """只重试连接、超时、限流、服务端错误和账号池授权切换类错误。"""
    try:
        from openai import APIConnectionError, APITimeoutError, APIStatusError, OpenAIError
    except ImportError:
        return False
    if not isinstance(error, OpenAIError):
        return False
    if isinstance(error, (APIConnectionError, APITimeoutError)):
        return True
    if not isinstance(error, APIStatusError):
        return False
    status_code = int(getattr(error, "status_code", 0) or 0)
    return status_code in RETRYABLE_COCKPIT_STATUS_CODES or status_code >= 500


def boolean_environment(name: str, default: bool) -> bool:
    """读取常见布尔环境变量写法。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def positive_float_environment(name: str, default: float) -> float:
    """读取正浮点配置，非法值回退默认值。"""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return value if value > 0 else default


def bounded_int_environment(name: str, default: int, minimum: int, maximum: int) -> int:
    """读取带上下界的整数配置。"""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))
