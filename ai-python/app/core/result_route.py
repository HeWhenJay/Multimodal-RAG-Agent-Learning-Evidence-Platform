"""将公开路由参数校验错误转换为 Java 兼容结果信封。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from app.core.result import Result


class ResultValidationRoute(APIRoute):
    """仅对挂载该路由类的公开接口返回稳定的参数错误信封。"""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        """包装 FastAPI 默认处理器并拦截请求校验异常。"""
        original_handler = super().get_route_handler()

        async def result_route_handler(request: Request) -> Response:
            """将校验异常转换为 HTTP 200 的既有业务响应格式。"""
            try:
                return await original_handler(request)
            except RequestValidationError as error:
                return JSONResponse(
                    status_code=200,
                    content=Result.failure(validation_message(error)).model_dump(),
                )

        return result_route_handler


def validation_message(error: RequestValidationError) -> str:
    """提取日志和页面数据常见必填字段的中文错误提示。"""
    messages: dict[str, str] = {
        "module": "模块不能为空",
        "action": "动作不能为空",
        "errorType": "错误类型不能为空",
        "message": "错误消息不能为空",
    }
    for item in error.errors():
        location: Any = item.get("loc", ())
        field = location[-1] if location else ""
        if field in messages:
            return messages[field]
    return "请求参数不合法"
