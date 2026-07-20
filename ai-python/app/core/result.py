"""对外业务接口的统一响应信封与可预期业务错误。"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel


T = TypeVar("T")


class Result(BaseModel, Generic[T]):
    """保持与原 Java `Result<T>` 一致的响应结构。"""

    code: int
    msg: str | None = None
    data: T | None = None

    @classmethod
    def success(cls, data: T | None = None) -> "Result[T]":
        """构造成功响应。"""
        return cls(code=1, data=data)

    @classmethod
    def failure(cls, message: str) -> "Result[None]":
        """构造可安全展示给调用方的业务失败响应。"""
        return cls(code=0, msg=message, data=None)


class BusinessError(Exception):
    """表示应转换为 `Result.code=0` 的受控业务错误。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
