"""复习模型 Cockpit 长等待与重试策略测试。"""

from dataclasses import replace
import logging

from httpx import Request, Response
import pytest
from openai import APIConnectionError, BadRequestError, InternalServerError, PermissionDeniedError

from app.review.cockpit_retry import (
    call_cockpit_with_retry,
    cockpit_error_is_retryable,
    cockpit_retry_policy,
)


LOGGER = logging.getLogger(__name__)
COCKPIT_ENV_NAMES = (
    "REVIEW_COCKPIT_RETRY_ENABLED",
    "REVIEW_COCKPIT_STREAM_OPEN_TIMEOUT_SECONDS",
    "REVIEW_COCKPIT_STREAM_IDLE_TIMEOUT_SECONDS",
    "REVIEW_COCKPIT_BOOTSTRAP_RETRIES",
    "REVIEW_COCKPIT_REQUEST_RETRIES",
    "REVIEW_COCKPIT_RETRY_BASE_DELAY_MS",
    "REVIEW_COCKPIT_RETRY_MAX_DELAY_MS",
    "REVIEW_COCKPIT_KEEPALIVE_SECONDS",
    "REVIEW_EXTRACTION_TIMEOUT_SECONDS",
)


def test_cockpit_long_wait_defaults_match_local_gateway_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认值应覆盖图片中的 180/240 秒窗口、一次启动重试和 15 秒余量。"""
    for name in COCKPIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    policy = cockpit_retry_policy()

    assert policy.stream_open_timeout_seconds == 180.0
    assert policy.stream_idle_timeout_seconds == 240.0
    assert policy.bootstrap_retries == 1
    assert policy.request_retries == 1
    assert policy.keepalive_seconds == 15.0
    assert policy.request_timeout_seconds == 615.0
    assert policy.max_attempts == 2
    assert policy.retry_delay_seconds(1) == 0.3


def test_cockpit_connection_error_retries_before_returning_result() -> None:
    """连接错误应先让 Cockpit 再次调度账号，第二次成功时不得触发降级。"""
    policy = cockpit_retry_policy()
    attempts: list[int] = []
    delays: list[float] = []

    def request() -> str:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise APIConnectionError(request=Request("POST", "http://localhost:58966/v1/chat/completions"))
        return "terra-ok"

    result = call_cockpit_with_retry(
        request,
        operation="Terra 测试",
        logger=LOGGER,
        policy=replace(policy, request_retries=1),
        sleep=delays.append,
    )

    assert result == "terra-ok"
    assert attempts == [1, 2]
    assert delays == [0.3]


def test_cockpit_permission_denied_is_retryable_for_account_rotation() -> None:
    """Cockpit 某个上游账号返回 403 时应允许一次账号池重新调度。"""
    request = Request("POST", "http://localhost:58966/v1/chat/completions")
    error = PermissionDeniedError(
        "当前账号无权限",
        response=Response(403, request=request),
        body={"error": {"message": "permission denied"}},
    )

    assert cockpit_error_is_retryable(error) is True


def test_cockpit_internal_server_error_is_retryable() -> None:
    """Cockpit 上游平台返回 500 时应先重新调度账号或平台。"""
    request = Request("POST", "http://localhost:58966/v1/chat/completions")
    error = InternalServerError(
        "上游服务异常",
        response=Response(500, request=request),
        body={"error": {"message": "internal server error"}},
    )

    assert cockpit_error_is_retryable(error) is True


def test_cockpit_bad_request_does_not_retry() -> None:
    """确定性的 400 参数错误应立即返回，避免重复错误请求。"""
    request = Request("POST", "http://localhost:58966/v1/chat/completions")
    error = BadRequestError(
        "请求参数错误",
        response=Response(400, request=request),
        body={"error": {"message": "bad request"}},
    )
    attempts: list[int] = []

    def invalid_request() -> None:
        attempts.append(1)
        raise error

    with pytest.raises(BadRequestError):
        call_cockpit_with_retry(
            invalid_request,
            operation="Terra 测试",
            logger=LOGGER,
            sleep=lambda _delay: None,
        )

    assert attempts == [1]
