"""认证 API 的请求和响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class AuthLoginRequest(BaseModel):
    """账号密码登录请求，字段规则与 Java `AuthLoginDTO` 对齐。"""

    account: str = Field(...)
    password: str = Field(...)
    remember: bool | None = True

    @field_validator("account", mode="before")
    @classmethod
    def validate_account(cls, value: object) -> str:
        """拒绝空账号，并保留 Java 端的中文提示。"""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("账号不能为空")
        return value

    @field_validator("password", mode="before")
    @classmethod
    def validate_password(cls, value: object) -> str:
        """拒绝空密码，并保留 Java 端的中文提示。"""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("密码不能为空")
        return value


class AuthUserResponse(BaseModel):
    """已登录用户的公开字段。"""

    id: int
    account: str
    displayName: str
    email: str | None = None
    role: str
    loginAt: datetime | None = None


class AuthLoginResponse(BaseModel):
    """登录成功后的会话令牌及用户信息。"""

    token: str
    expiresAt: datetime
    user: AuthUserResponse
