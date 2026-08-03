"""纯 Python 后端的非破坏性数据库增量迁移。"""

from __future__ import annotations

import os
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_DIRECTORY = REPOSITORY_ROOT / "infra" / "sql" / "alter-database"
PYTHON_MIGRATIONS = (
    "20260721_0100_add_python_rag_durable_tasks.sql",
    "20260801_0100_create_learning_review_tables.sql",
    "20260802_0100_add_remote_video_import_guard.sql",
    "20260802_0200_add_learning_review_exclusions.sql",
    "20260802_0300_add_review_material_summary.sql",
    "20260802_0400_add_review_material_display_order.sql",
    "20260802_0500_guard_review_extractor_downgrade.sql",
    "20260803_0100_add_review_folders.sql",
    "20260803_0200_add_review_generation_repair_state.sql",
    "20260803_0300_add_review_generation_progress.sql",
)
PYTHON_MIGRATION_LOCK_KEY = 6842476948943452609


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
                # 同一数据库的多个 API 实例必须串行检查和执行迁移。
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(%s)",
                    (PYTHON_MIGRATION_LOCK_KEY,),
                )
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
