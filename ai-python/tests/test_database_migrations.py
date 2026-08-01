"""Python 增量迁移的多实例安全性测试。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from app.core.database_migrations import (
    PYTHON_MIGRATION_LOCK_KEY,
    apply_python_schema_migrations,
)


class FakeCursor:
    """记录迁移启动阶段执行的 SQL。"""

    def __init__(self) -> None:
        self.executed: list[tuple[str, object | None]] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def execute(self, statement: str, params: object | None = None) -> None:
        self.executed.append((" ".join(statement.split()), params))

    def fetchone(self):
        # 两个迁移均视为已执行，本测试只核对启动锁顺序。
        return (1,)


class FakeConnection:
    """提供 psycopg 连接上下文所需的最小行为。"""

    def __init__(self, cursor: FakeCursor) -> None:
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def cursor(self) -> FakeCursor:
        return self._cursor


def test_migrations_take_transaction_lock_before_schema_checks(monkeypatch) -> None:
    """多实例启动时必须先获取数据库事务锁，再检查迁移版本。"""
    cursor = FakeCursor()
    fake_psycopg = SimpleNamespace(connect=lambda _url: FakeConnection(cursor))
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    monkeypatch.setenv("AI_DATABASE_MIGRATIONS_ENABLED", "true")

    assert apply_python_schema_migrations("postgresql://unused") == []
    assert "pg_advisory_xact_lock" in cursor.executed[0][0]
    assert cursor.executed[0][1] == (PYTHON_MIGRATION_LOCK_KEY,)
    assert "CREATE SCHEMA" in cursor.executed[1][0]
