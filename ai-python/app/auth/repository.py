"""认证表的 psycopg 仓储实现。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import os
import re
from typing import Any, ContextManager, Protocol


DEFAULT_SCHEMA = "learning_evidence"
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class AuthUserRecord:
    """认证所需的 `app_user` 字段。"""

    id: int
    account: str
    email: str | None
    display_name: str
    role: str
    password_hash: str
    password_salt: str
    password_algorithm: str | None
    password_iterations: int | None
    status: str
    last_login_at: datetime | None


@dataclass(frozen=True)
class AuthSessionRecord:
    """校验当前登录态所需的会话字段。"""

    user_id: int


class AuthTransaction(Protocol):
    """认证服务对事务的最小依赖，便于测试使用内存替身。"""

    def find_user_by_account(self, account: str) -> AuthUserRecord | None: ...

    def find_user_by_id(self, user_id: int) -> AuthUserRecord | None: ...

    def update_last_login_at(self, user_id: int, login_at: datetime) -> None: ...

    def insert_session(self, user_id: int, token_hash: str, remember_me: bool, expires_at: datetime) -> None: ...

    def find_active_session_by_token_hash(self, token_hash: str, now: datetime) -> AuthSessionRecord | None: ...

    def revoke_by_token_hash(self, token_hash: str) -> None: ...

    def insert_login_record(
        self,
        user_id: int | None,
        account: str,
        success: bool,
        failure_reason: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None: ...


class AuthRepositoryProtocol(Protocol):
    """允许测试替换实际 PostgreSQL 仓储。"""

    def transaction(self) -> ContextManager[AuthTransaction]: ...


class DatabaseAuthTransaction:
    """单个 PostgreSQL 事务内的认证 SQL 操作。"""

    def __init__(self, cursor: Any, schema: str) -> None:
        self._cursor = cursor
        self._schema = schema

    def find_user_by_account(self, account: str) -> AuthUserRecord | None:
        """按标准化账号读取认证用户。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT id, account, email, display_name, role, password_hash, password_salt,
                       password_algorithm, password_iterations, status, last_login_at
                FROM {schema}.app_user
                WHERE account = %s
                """
            ),
            (account,),
        )
        return self._to_user(self._cursor.fetchone())

    def find_user_by_id(self, user_id: int) -> AuthUserRecord | None:
        """按 ID 读取有效会话对应的用户。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT id, account, email, display_name, role, password_hash, password_salt,
                       password_algorithm, password_iterations, status, last_login_at
                FROM {schema}.app_user
                WHERE id = %s
                """
            ),
            (user_id,),
        )
        return self._to_user(self._cursor.fetchone())

    def update_last_login_at(self, user_id: int, login_at: datetime) -> None:
        """回写用户本次登录时间。"""
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.app_user
                SET last_login_at = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """
            ),
            (login_at, user_id),
        )

    def insert_session(self, user_id: int, token_hash: str, remember_me: bool, expires_at: datetime) -> None:
        """只保存令牌哈希，避免数据库泄露原始会话令牌。"""
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.auth_session (user_id, token_hash, remember_me, expires_at, revoked)
                VALUES (%s, %s, %s, %s, FALSE)
                """
            ),
            (user_id, token_hash, remember_me, expires_at),
        )

    def find_active_session_by_token_hash(self, token_hash: str, now: datetime) -> AuthSessionRecord | None:
        """查询未撤销且未过期的会话。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT user_id
                FROM {schema}.auth_session
                WHERE token_hash = %s
                  AND revoked = FALSE
                  AND expires_at > %s
                """
            ),
            (token_hash, now),
        )
        row = self._cursor.fetchone()
        return None if row is None else AuthSessionRecord(user_id=int(row["user_id"]))

    def revoke_by_token_hash(self, token_hash: str) -> None:
        """幂等撤销指定会话。"""
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.auth_session
                SET revoked = TRUE, updated_at = CURRENT_TIMESTAMP
                WHERE token_hash = %s
                """
            ),
            (token_hash,),
        )

    def insert_login_record(
        self,
        user_id: int | None,
        account: str,
        success: bool,
        failure_reason: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        """记录成功或失败的登录尝试。"""
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.auth_login_record
                    (user_id, account, success, failure_reason, ip_address, user_agent)
                VALUES (%s, %s, %s, %s, %s, %s)
                """
            ),
            (user_id, account, success, failure_reason, ip_address, user_agent),
        )

    def _statement(self, query: str) -> Any:
        """以标识符 API 拼接 schema，避免把配置值拼入普通 SQL 文本。"""
        from psycopg import sql

        return sql.SQL(query).format(schema=sql.Identifier(self._schema))

    @staticmethod
    def _to_user(row: dict[str, Any] | None) -> AuthUserRecord | None:
        """将字典行转换为领域记录。"""
        if row is None:
            return None
        return AuthUserRecord(
            id=int(row["id"]),
            account=str(row["account"]),
            email=row["email"],
            display_name=str(row["display_name"]),
            role=str(row["role"]),
            password_hash=str(row["password_hash"]),
            password_salt=str(row["password_salt"]),
            password_algorithm=row["password_algorithm"],
            password_iterations=row["password_iterations"],
            status=str(row["status"]),
            last_login_at=row["last_login_at"],
        )


class AuthRepository:
    """通过 psycopg 管理认证数据库事务。"""

    def __init__(self, database_url: str | None = None, schema: str | None = None) -> None:
        self._database_url = database_url or resolve_database_url()
        self._schema = validate_schema(schema or os.getenv("RAG_DATABASE_SCHEMA", DEFAULT_SCHEMA))

    @contextmanager
    def transaction(self) -> Iterator[AuthTransaction]:
        """打开一个提交或回滚一致的数据库事务。"""
        connection = self._connect()
        try:
            with connection:
                with connection.cursor() as cursor:
                    yield DatabaseAuthTransaction(cursor, self._schema)
        finally:
            connection.close()

    def _connect(self) -> Any:
        """延迟导入 psycopg，使依赖替换测试不必建立数据库连接。"""
        if not self._database_url:
            raise RuntimeError("未配置 AUTH_DATABASE_URL、RAG_DATABASE_URL 或 DATABASE_URL")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("认证数据库仓储需要安装 psycopg[binary]") from exc
        return psycopg.connect(self._database_url, row_factory=dict_row)


def resolve_database_url() -> str:
    """按认证优先、RAG 复用、通用数据库的顺序读取连接串。"""
    return (
        os.getenv("AUTH_DATABASE_URL", "").strip()
        or os.getenv("RAG_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
    )


def validate_schema(value: str) -> str:
    """只接受 PostgreSQL 合法简单标识符作为 schema 名称。"""
    if not SCHEMA_PATTERN.fullmatch(value):
        raise RuntimeError("RAG_DATABASE_SCHEMA 必须是合法的 PostgreSQL schema 标识符")
    return value
