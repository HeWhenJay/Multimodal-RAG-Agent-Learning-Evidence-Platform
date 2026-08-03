"""Python 增量迁移的多实例安全性测试。"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from app.core.database_migrations import (
    MIGRATION_DIRECTORY,
    PYTHON_MIGRATION_LOCK_KEY,
    PYTHON_MIGRATIONS,
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
        # 所有迁移均视为已执行，本测试只核对启动锁顺序。
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


def test_review_material_summary_migration_is_registered_and_idempotent() -> None:
    """复习摘要列必须进入启动迁移，并允许多实例或重复启动安全执行。"""
    filename = "20260802_0300_add_review_material_summary.sql"

    assert filename in PYTHON_MIGRATIONS
    sql = (MIGRATION_DIRECTORY / filename).read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS summary TEXT" in " ".join(sql.split())


def test_review_material_order_migration_is_registered_and_idempotent() -> None:
    """资料拖拽顺序列和查询索引必须进入启动迁移。"""
    filename = "20260802_0400_add_review_material_display_order.sql"

    assert filename in PYTHON_MIGRATIONS
    sql = " ".join((MIGRATION_DIRECTORY / filename).read_text(encoding="utf-8").split())
    assert "ADD COLUMN IF NOT EXISTS display_order INTEGER" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_learning_review_material_user_order" in sql


def test_review_folder_migration_is_registered_and_idempotent() -> None:
    """复习文件夹和文档归属表必须随 Python 服务启动安全创建。"""
    filename = "20260803_0100_add_review_folders.sql"

    assert filename in PYTHON_MIGRATIONS
    sql = " ".join((MIGRATION_DIRECTORY / filename).read_text(encoding="utf-8").split())
    assert "CREATE TABLE IF NOT EXISTS learning_evidence.learning_review_folder" in sql
    assert "CREATE TABLE IF NOT EXISTS learning_evidence.learning_review_folder_material" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_learning_review_folder_material_folder" in sql


def test_review_generation_repair_state_migration_is_registered() -> None:
    """多轮生成尝试、质量反馈和人工终态必须可安全迁移。"""
    filename = "20260803_0200_add_review_generation_repair_state.sql"

    assert filename in PYTHON_MIGRATIONS
    sql = " ".join((MIGRATION_DIRECTORY / filename).read_text(encoding="utf-8").split())
    assert "ADD COLUMN IF NOT EXISTS generation_attempts INTEGER" in sql
    assert "ADD COLUMN IF NOT EXISTS quality_feedback JSONB" in sql
    assert "NEEDS_REVIEW" in sql


def test_review_generation_progress_migration_is_registered() -> None:
    """复习生成阶段快照必须进入启动迁移并提供 JSONB 默认值。"""
    filename = "20260803_0300_add_review_generation_progress.sql"

    assert filename in PYTHON_MIGRATIONS
    sql = " ".join((MIGRATION_DIRECTORY / filename).read_text(encoding="utf-8").split())
    assert "ADD COLUMN IF NOT EXISTS generation_progress JSONB" in sql
    assert "DEFAULT '{}'::jsonb" in sql
