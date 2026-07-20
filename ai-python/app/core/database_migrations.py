"""纯 Python 后端的非破坏性数据库增量迁移。"""

from __future__ import annotations

import os
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_DIRECTORY = REPOSITORY_ROOT / "infra" / "sql" / "alter-database"
PYTHON_MIGRATIONS = ("20260721_0100_add_python_rag_durable_tasks.sql",)


def apply_python_schema_migrations(database_url: str | None = None) -> list[str]:
    """在 API 启动前补齐 Python 新增表和列，不执行会清空数据的初始化脚本。"""
    if not read_bool_env("AI_DATABASE_MIGRATIONS_ENABLED", True):
        return []
    url = (database_url or os.getenv("RAG_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        return []
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("数据库迁移需要安装 psycopg[binary]") from exc

    applied: list[str] = []
    try:
        with psycopg.connect(url) as connection:
            with connection.cursor() as cursor:
                cursor.execute("CREATE SCHEMA IF NOT EXISTS learning_evidence")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS learning_evidence.python_schema_migration (
                        version VARCHAR(120) PRIMARY KEY,
                        applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                for filename in PYTHON_MIGRATIONS:
                    cursor.execute(
                        "SELECT 1 FROM learning_evidence.python_schema_migration WHERE version = %s",
                        (filename,),
                    )
                    if cursor.fetchone() is not None:
                        continue
                    path = MIGRATION_DIRECTORY / filename
                    if not path.is_file():
                        raise RuntimeError(f"缺少数据库增量迁移文件：{path}")
                    cursor.execute(path.read_text(encoding="utf-8"))
                    cursor.execute(
                        "INSERT INTO learning_evidence.python_schema_migration (version) VALUES (%s)",
                        (filename,),
                    )
                    applied.append(filename)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            "Python 数据库增量迁移失败；新环境请先按 infra/sql/init.sql 初始化 PostgreSQL，再重新启动服务"
        ) from exc
    return applied


def read_bool_env(name: str, default: bool) -> bool:
    """读取迁移开关，非法或空值回退到安全默认值。"""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
