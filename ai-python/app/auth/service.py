"""与 Java 密码和数据库会话契约兼容的认证业务服务。"""

from __future__ import annotations

import base64
from collections.abc import Callable
from datetime import datetime, timedelta
import hashlib
import hmac
import secrets
from typing import Final

from app.auth.repository import AuthRepository, AuthRepositoryProtocol, AuthTransaction, AuthUserRecord
from app.core.result import BusinessError
from app.schemas.auth import AuthLoginRequest, AuthLoginResponse, AuthUserResponse


ACTIVE_STATUS: Final[str] = "ACTIVE"
INVALID_LOGIN_MESSAGE: Final[str] = "账号或密码错误"
SESSION_HOURS: Final[int] = 12
REMEMBER_DAYS: Final[int] = 30
TOKEN_BYTES: Final[int] = 32
HASH_BYTES: Final[int] = 32
PBKDF2_ALGORITHMS: Final[dict[str, str]] = {
    "pbkdf2withhmacsha1": "sha1",
    "pbkdf2withhmacsha224": "sha224",
    "pbkdf2withhmacsha256": "sha256",
    "pbkdf2withhmacsha384": "sha384",
    "pbkdf2withhmacsha512": "sha512",
}


class AuthBusinessError(BusinessError):
    """认证领域可安全暴露给前端的业务错误。"""


class AuthService:
    """处理账号密码登录、会话查询和退出登录。"""

    def __init__(
        self,
        repository: AuthRepositoryProtocol | None = None,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self._repository = repository or AuthRepository()
        self._clock = clock

    def login(self, request: AuthLoginRequest, ip_address: str | None, user_agent: str | None) -> AuthLoginResponse:
        """校验密码、记录登录结果，并在同一事务内创建会话。"""
        account = normalize_account(request.account)
        failure_message: str | None = None
        response: AuthLoginResponse | None = None
        with self._repository.transaction() as transaction:
            user = transaction.find_user_by_account(account)
            if user is None:
                self._record_login(transaction, None, account, False, INVALID_LOGIN_MESSAGE, ip_address, user_agent)
                failure_message = INVALID_LOGIN_MESSAGE
            elif user.status.upper() != ACTIVE_STATUS:
                self._record_login(transaction, user, account, False, "账号已停用", ip_address, user_agent)
                failure_message = "账号已停用"
            elif not password_matches(request.password, user):
                self._record_login(transaction, user, account, False, INVALID_LOGIN_MESSAGE, ip_address, user_agent)
                failure_message = INVALID_LOGIN_MESSAGE
            else:
                login_at = self._clock()
                remember = bool(request.remember)
                expires_at = login_at + (timedelta(days=REMEMBER_DAYS) if remember else timedelta(hours=SESSION_HOURS))
                token = new_token()
                transaction.insert_session(user.id, token_hash(token), remember, expires_at)
                transaction.update_last_login_at(user.id, login_at)
                self._record_login(transaction, user, account, True, None, ip_address, user_agent)
                response = AuthLoginResponse(
                    token=token,
                    expiresAt=expires_at,
                    user=to_user_response(user, login_at),
                )

        # Java 使用 noRollbackFor，失败登录审计记录必须先提交再返回业务错误。
        if failure_message is not None:
            raise AuthBusinessError(failure_message)
        if response is None:
            raise RuntimeError("认证登录未生成会话响应")
        return response

    def current_user(self, token: str | None) -> AuthUserResponse:
        """读取当前未过期且未撤销的数据库会话。"""
        if not token or not token.strip():
            raise AuthBusinessError("登录状态已失效")
        with self._repository.transaction() as transaction:
            session = transaction.find_active_session_by_token_hash(token_hash(token), self._clock())
            if session is None:
                raise AuthBusinessError("登录状态已失效")
            user = transaction.find_user_by_id(session.user_id)
            if user is None or user.status.upper() != ACTIVE_STATUS:
                raise AuthBusinessError("登录状态已失效")
            return to_user_response(user, user.last_login_at)

    def logout(self, token: str | None) -> None:
        """幂等撤销当前令牌；缺少令牌时保持 Java 接口的成功语义。"""
        if not token or not token.strip():
            return
        with self._repository.transaction() as transaction:
            transaction.revoke_by_token_hash(token_hash(token))

    @staticmethod
    def _record_login(
        transaction: AuthTransaction,
        user: AuthUserRecord | None,
        account: str,
        success: bool,
        failure_reason: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        """写入与 Java 字段长度一致的登录审计记录。"""
        transaction.insert_login_record(
            user.id if user else None,
            account,
            success,
            truncate(failure_reason, 255),
            truncate(ip_address, 80),
            truncate(user_agent, 500),
        )


def password_matches(password: str, user: AuthUserRecord) -> bool:
    """以 Java PBKDF2 参数计算哈希并使用常量时间比较。"""
    algorithm = (user.password_algorithm or "PBKDF2WithHmacSHA256").strip().lower()
    digest_name = PBKDF2_ALGORITHMS.get(algorithm)
    if not digest_name:
        return False
    iterations = user.password_iterations or 120000
    if iterations <= 0:
        return False
    try:
        actual = hashlib.pbkdf2_hmac(
            digest_name,
            password.encode("utf-8"),
            (user.password_salt or "").encode("utf-8"),
            iterations,
            dklen=HASH_BYTES,
        ).hex()
        return hmac.compare_digest(user.password_hash.encode("utf-8"), actual.encode("utf-8"))
    except (TypeError, ValueError):
        return False


def token_hash(token: str) -> str:
    """计算存入 `auth_session` 的 SHA-256 十六进制 Token 哈希。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """生成与 Java 32 字节 URL-safe Base64 无填充格式一致的会话令牌。"""
    raw = secrets.token_bytes(TOKEN_BYTES)
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def normalize_account(account: str) -> str:
    """标准化账号输入，保持大小写不影响登录。"""
    return (account or "").strip().lower()


def to_user_response(user: AuthUserRecord, login_at: datetime | None) -> AuthUserResponse:
    """转换不包含密码字段的用户响应。"""
    return AuthUserResponse(
        id=user.id,
        account=user.account,
        displayName=user.display_name,
        email=user.email,
        role=user.role,
        loginAt=login_at,
    )


def truncate(value: str | None, max_length: int) -> str | None:
    """按 Java 规则截断审计字段。"""
    if value is None or len(value) <= max_length:
        return value
    return value[:max_length]
