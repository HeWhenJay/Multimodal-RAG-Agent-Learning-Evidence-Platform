from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


def post_log_event(payload: dict[str, Any]) -> bool:
    """向 Java 内部日志接口上报事件，失败时返回 False 交给调用方降级。"""
    return post_json(os.getenv("RAG_EVENT_CALLBACK_URL"), payload)


def post_log_error(payload: dict[str, Any]) -> bool:
    """向 Java 内部错误日志接口上报错误，失败时返回 False 交给调用方降级。"""
    return post_json(os.getenv("RAG_ERROR_CALLBACK_URL"), payload)


def post_json(url: str | None, payload: dict[str, Any]) -> bool:
    """使用标准库发送 JSON，避免为日志回调引入额外依赖。"""
    if not url:
        return False
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Internal-Log-Token": os.getenv("EVIDENCE_INTERNAL_LOG_TOKEN", ""),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310 - 本地服务回调地址来自运行配置
            if not 200 <= response.status < 300:
                return False
            return response_business_success(response.read())
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
        return False


def response_business_success(body: bytes) -> bool:
    """兼容 Java Result<T> 包装，业务失败时允许调用方继续降级落库。"""
    if not body:
        return True
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return True
    if isinstance(data, dict) and "code" in data:
        return data.get("code") == 1
    return True
