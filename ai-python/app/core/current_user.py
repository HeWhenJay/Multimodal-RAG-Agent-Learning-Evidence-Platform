"""公开接口的当前登录用户依赖。"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header

from app.api.auth import bearer_token, get_auth_service
from app.auth.service import AuthBusinessError, AuthService
from app.schemas.auth import AuthUserResponse


def get_current_user(
    authorization: str | None = Header(default=None, alias="Authorization"),
    service: AuthService = Depends(get_auth_service),
) -> AuthUserResponse:
    """从 Bearer 会话读取权威用户，拒绝客户端伪造的 userId。"""
    try:
        return service.current_user(bearer_token(authorization))
    except AuthBusinessError:
        raise
    except Exception:
        raise AuthBusinessError("认证服务暂不可用") from None


CurrentUser = Annotated[AuthUserResponse, Depends(get_current_user)]
