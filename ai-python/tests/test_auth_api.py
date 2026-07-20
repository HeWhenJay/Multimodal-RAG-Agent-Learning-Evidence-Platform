"""认证 FastAPI 路由及 Java PBKDF2/数据库会话契约测试。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
import hashlib

from fastapi.testclient import TestClient

from app.api.auth import get_auth_service
from app.auth.repository import AuthSessionRecord, AuthUserRecord
from app.auth.service import AuthService, token_hash
from app.main import app


class InMemoryAuthRepository:
    """替代 PostgreSQL 的最小认证事务实现。"""

    def __init__(self, users: list[AuthUserRecord]) -> None:
        self.users_by_account = {user.account: user for user in users}
        self.users_by_id = {user.id: user for user in users}
        self.sessions: dict[str, tuple[int, datetime, bool]] = {}
        self.login_records: list[dict[str, object]] = []

    @contextmanager
    def transaction(self) -> Iterator["InMemoryAuthRepository"]:
        """内存替身不需要真实数据库事务。"""
        yield self

    def find_user_by_account(self, account: str) -> AuthUserRecord | None:
        """按账号查找内存用户。"""
        return self.users_by_account.get(account)

    def find_user_by_id(self, user_id: int) -> AuthUserRecord | None:
        """按 ID 查找内存用户。"""
        return self.users_by_id.get(user_id)

    def update_last_login_at(self, user_id: int, login_at: datetime) -> None:
        """回写内存用户登录时间。"""
        user = self.users_by_id[user_id]
        updated = AuthUserRecord(
            id=user.id,
            account=user.account,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            password_hash=user.password_hash,
            password_salt=user.password_salt,
            password_algorithm=user.password_algorithm,
            password_iterations=user.password_iterations,
            status=user.status,
            last_login_at=login_at,
        )
        self.users_by_id[user_id] = updated
        self.users_by_account[updated.account] = updated

    def insert_session(self, user_id: int, token_hash_value: str, remember_me: bool, expires_at: datetime) -> None:
        """保存令牌哈希而不保存原始令牌。"""
        self.sessions[token_hash_value] = (user_id, expires_at, False)

    def find_active_session_by_token_hash(self, token_hash_value: str, now: datetime) -> AuthSessionRecord | None:
        """查询未过期且未撤销的内存会话。"""
        session = self.sessions.get(token_hash_value)
        if session is None:
            return None
        user_id, expires_at, revoked = session
        return None if revoked or expires_at <= now else AuthSessionRecord(user_id=user_id)

    def revoke_by_token_hash(self, token_hash_value: str) -> None:
        """幂等撤销内存会话。"""
        session = self.sessions.get(token_hash_value)
        if session is None:
            return
        self.sessions[token_hash_value] = (session[0], session[1], True)

    def insert_login_record(
        self,
        user_id: int | None,
        account: str,
        success: bool,
        failure_reason: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        """记录登录审计参数，供断言验证。"""
        self.login_records.append(
            {
                "user_id": user_id,
                "account": account,
                "success": success,
                "failure_reason": failure_reason,
                "ip_address": ip_address,
                "user_agent": user_agent,
            }
        )


def build_user(account: str = "admin", status: str = "ACTIVE") -> AuthUserRecord:
    """构造与 Java 默认 PBKDF2 算法兼容的测试用户。"""
    salt = "learning-evidence-admin-salt-v1"
    password_hash = hashlib.pbkdf2_hmac("sha256", b"123456", salt.encode("utf-8"), 120000, dklen=32).hex()
    return AuthUserRecord(
        id=7,
        account=account,
        email="admin@example.com",
        display_name="系统管理员",
        role="ADMIN",
        password_hash=password_hash,
        password_salt=salt,
        password_algorithm="PBKDF2WithHmacSHA256",
        password_iterations=120000,
        status=status,
        last_login_at=None,
    )


def test_login_me_logout_uses_patched_service_without_database() -> None:
    """路由可通过依赖替换完成完整会话流程，不连接 PostgreSQL。"""
    now = datetime(2026, 7, 20, 10, 30, 0)
    repository = InMemoryAuthRepository([build_user()])
    service = AuthService(repository=repository, clock=lambda: now)
    app.dependency_overrides[get_auth_service] = lambda: service
    client = TestClient(app)
    try:
        login_response = client.post(
            "/api/auth/login",
            json={"account": " ADMIN ", "password": "123456", "remember": True},
            headers={"X-Forwarded-For": "203.0.113.5, 10.0.0.1", "User-Agent": "pytest-auth"},
        )
        assert login_response.status_code == 200
        login_body = login_response.json()
        assert login_body["code"] == 1
        assert login_body["data"]["user"]["account"] == "admin"
        assert login_body["data"]["expiresAt"].startswith("2026-08-19T10:30:00")
        token = login_body["data"]["token"]
        assert token not in repository.sessions
        assert token_hash(token) in repository.sessions
        assert repository.login_records[-1]["ip_address"] == "203.0.113.5"

        me_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_response.status_code == 200
        assert me_response.json()["code"] == 1
        assert me_response.json()["data"]["id"] == 7

        logout_response = client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})
        assert logout_response.status_code == 200
        assert logout_response.json()["code"] == 1

        expired_response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert expired_response.status_code == 200
        assert expired_response.json() == {"code": 0, "msg": "登录状态已失效", "data": None}
    finally:
        app.dependency_overrides.clear()


def test_failed_login_and_validation_keep_java_result_errors() -> None:
    """错误密码和空参数均返回 Java 风格的业务错误信封。"""
    repository = InMemoryAuthRepository([build_user()])
    service = AuthService(repository=repository)
    app.dependency_overrides[get_auth_service] = lambda: service
    client = TestClient(app)
    try:
        invalid_password = client.post("/api/auth/login", json={"account": "admin", "password": "bad-password"})
        assert invalid_password.status_code == 200
        assert invalid_password.json()["code"] == 0
        assert invalid_password.json()["msg"] == "账号或密码错误"
        assert repository.login_records[-1]["success"] is False

        missing_account = client.post("/api/auth/login", json={"password": "123456"})
        assert missing_account.status_code == 200
        assert missing_account.json() == {"code": 0, "msg": "账号不能为空", "data": None}
    finally:
        app.dependency_overrides.clear()


def test_non_remembered_session_uses_twelve_hours() -> None:
    """`remember=false` 保持 Java 的 12 小时有效期。"""
    now = datetime(2026, 7, 20, 10, 30, 0)
    repository = InMemoryAuthRepository([build_user()])
    service = AuthService(repository=repository, clock=lambda: now)
    app.dependency_overrides[get_auth_service] = lambda: service
    client = TestClient(app)
    try:
        response = client.post("/api/auth/login", json={"account": "admin", "password": "123456", "remember": False})
        assert response.status_code == 200
        assert response.json()["data"]["expiresAt"].startswith((now + timedelta(hours=12)).isoformat())
    finally:
        app.dependency_overrides.clear()
