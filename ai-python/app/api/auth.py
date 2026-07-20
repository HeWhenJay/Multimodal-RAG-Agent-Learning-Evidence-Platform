"""对外账号密码认证路由。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, Request

from app.auth.service import AuthBusinessError, AuthService
from app.core.result import BusinessError, Result
from app.schemas.auth import AuthLoginRequest, AuthLoginResponse, AuthUserResponse


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["登录认证"])


def get_auth_service() -> AuthService:
    """提供默认认证服务，测试可通过 FastAPI 依赖替换注入内存实现。"""
    return AuthService()


@router.post("/login", response_model=Result[AuthLoginResponse])
def login(
    payload: AuthLoginRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> Result[AuthLoginResponse]:
    """使用账号密码登录并创建数据库会话。"""
    try:
        return Result.success(service.login(payload, client_ip(request), request.headers.get("User-Agent")))
    except AuthBusinessError:
        raise
    except Exception:
        logger.exception("认证登录服务调用失败")
        raise BusinessError("认证服务暂不可用") from None


@router.get("/me", response_model=Result[AuthUserResponse])
def me(
    authorization: str | None = Header(default=None, alias="Authorization"),
    service: AuthService = Depends(get_auth_service),
) -> Result[AuthUserResponse]:
    """根据 Bearer Token 查询当前用户。"""
    try:
        return Result.success(service.current_user(bearer_token(authorization)))
    except AuthBusinessError:
        raise
    except Exception:
        logger.exception("查询当前认证用户失败")
        raise BusinessError("认证服务暂不可用") from None


@router.post("/logout", response_model=Result[None])
def logout(
    authorization: str | None = Header(default=None, alias="Authorization"),
    service: AuthService = Depends(get_auth_service),
) -> Result[None]:
    """撤销当前会话；空令牌和未知令牌均幂等成功。"""
    try:
        service.logout(bearer_token(authorization))
        return Result.success()
    except Exception:
        logger.exception("撤销认证会话失败")
        raise BusinessError("认证服务暂不可用") from None


def bearer_token(authorization: str | None) -> str | None:
    """按 Java `AuthController` 的规则提取 Bearer Token。"""
    if authorization is None or not authorization.strip():
        return None
    prefix = "Bearer "
    return authorization[len(prefix) :].strip() if authorization.startswith(prefix) else authorization.strip()


def client_ip(request: Request) -> str | None:
    """优先采用代理透传的第一个客户端地址。"""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for and forwarded_for.strip():
        return forwarded_for.split(",", maxsplit=1)[0].strip()
    return request.client.host if request.client else None
