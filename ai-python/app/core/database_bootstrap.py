"""PostgreSQL/pgvector 空库的非破坏性初始化入口。

`infra/sql/init.sql` 是可审计的破坏性重建快照，不能直接在已有数据库上执行。
本模块在运行时读取该快照，移除 DROP 语句并将建表、建索引转换为幂等形式，
避免为 Python 后端复制一份长期失真的 SQL。
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INIT_SQL_PATH = REPOSITORY_ROOT / "infra" / "sql" / "init.sql"


@dataclass(frozen=True)
class BootstrapPlan:
    """描述由权威初始化快照转换出的安全执行计划。"""

    statements: tuple[str, ...]
    skipped_statements: tuple[str, ...]
    source_path: Path

    @property
    def sql(self) -> str:
        """返回适合日志、dry-run 或人工审计的 SQL 文本。"""
        return "\n\n".join(f"{statement.rstrip(';')};" for statement in self.statements)


@dataclass(frozen=True)
class BootstrapResult:
    """记录一次数据库 bootstrap 的执行结果。"""

    executed_statements: int
    skipped_statements: int
    applied_migrations: tuple[str, ...] = ()


def split_sql_statements(script: str) -> list[str]:
    """按 PostgreSQL 语法安全拆分语句，忽略引号、注释和 dollar quote 中的分号。"""
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    state = "normal"
    dollar_tag: str | None = None

    while index < len(script):
        char = script[index]
        next_char = script[index + 1] if index + 1 < len(script) else ""

        if state == "line_comment":
            buffer.append(char)
            if char == "\n":
                state = "normal"
            index += 1
            continue

        if state == "block_comment":
            buffer.append(char)
            if char == "*" and next_char == "/":
                buffer.append(next_char)
                index += 2
                state = "normal"
            else:
                index += 1
            continue

        if state == "single_quote":
            buffer.append(char)
            if char == "'":
                if next_char == "'":
                    buffer.append(next_char)
                    index += 2
                    continue
                state = "normal"
            elif char == "\\" and next_char:
                # 兼容 PostgreSQL standard_conforming_strings 关闭时的转义。
                buffer.append(next_char)
                index += 2
                continue
            index += 1
            continue

        if state == "double_quote":
            buffer.append(char)
            if char == '"':
                if next_char == '"':
                    buffer.append(next_char)
                    index += 2
                    continue
                state = "normal"
            index += 1
            continue

        if state == "dollar_quote":
            assert dollar_tag is not None
            if script.startswith(dollar_tag, index):
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                state = "normal"
                dollar_tag = None
            else:
                buffer.append(char)
                index += 1
            continue

        # normal 状态。
        if char == "-" and next_char == "-":
            buffer.extend((char, next_char))
            index += 2
            state = "line_comment"
            continue
        if char == "/" and next_char == "*":
            buffer.extend((char, next_char))
            index += 2
            state = "block_comment"
            continue
        if char == "'":
            buffer.append(char)
            index += 1
            state = "single_quote"
            continue
        if char == '"':
            buffer.append(char)
            index += 1
            state = "double_quote"
            continue
        if char == "$":
            match = re.match(r"\$[A-Za-z_][A-Za-z0-9_]*\$|\$\$", script[index:])
            if match:
                dollar_tag = match.group(0)
                buffer.append(dollar_tag)
                index += len(dollar_tag)
                state = "dollar_quote"
                continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer.clear()
            index += 1
            continue

        buffer.append(char)
        index += 1

    trailing = "".join(buffer).strip()
    if trailing:
        statements.append(trailing)
    return statements


def _without_leading_comments(statement: str) -> str:
    """去掉语句前的注释，便于安全识别 SQL 类型。"""
    value = statement.strip()
    while value.startswith("--"):
        newline = value.find("\n")
        if newline < 0:
            return ""
        value = value[newline + 1 :].lstrip()
    while value.startswith("/*"):
        end = value.find("*/", 2)
        if end < 0:
            return ""
        value = value[end + 2 :].lstrip()
    return value


def _make_create_idempotent(statement: str) -> str:
    """给 CREATE TABLE/INDEX 添加 IF NOT EXISTS，保持原始 SQL 定义不变。"""
    value = statement.strip()
    value = re.sub(
        r"^CREATE\s+TABLE\s+(?!IF\s+NOT\s+EXISTS\b)",
        "CREATE TABLE IF NOT EXISTS ",
        value,
        count=1,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"^CREATE\s+UNIQUE\s+INDEX\s+(?!IF\s+NOT\s+EXISTS\b)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ",
        value,
        count=1,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"^CREATE\s+INDEX\s+(?!IF\s+NOT\s+EXISTS\b)",
        "CREATE INDEX IF NOT EXISTS ",
        value,
        count=1,
        flags=re.IGNORECASE,
    )
    return value


def _preserve_existing_admin_seed(statement: str) -> str:
    """避免重复 bootstrap 时覆盖已有管理员密码或人工修改的资料。"""
    if not re.match(r"^INSERT\s+INTO\s+learning_evidence\.app_user\b", statement, re.IGNORECASE):
        return statement
    return re.sub(
        r"\bON\s+CONFLICT\s*\(\s*account\s*\)\s+DO\s+UPDATE\s+SET\b.*$",
        "ON CONFLICT (account) DO NOTHING",
        statement,
        count=1,
        flags=re.IGNORECASE | re.DOTALL,
    )


def build_bootstrap_plan(
    source_sql: str,
    *,
    source_path: Path = DEFAULT_INIT_SQL_PATH,
    preserve_existing_seed: bool = True,
) -> BootstrapPlan:
    """将破坏性初始化快照转换为不会删除既有数据的执行计划。"""
    statements: list[str] = []
    skipped: list[str] = []
    for raw_statement in split_sql_statements(source_sql):
        statement = _without_leading_comments(raw_statement)
        if not statement:
            continue
        first_keyword = statement.split(None, 1)[0].upper()
        if first_keyword == "DROP":
            # init.sql 的 DROP TABLE 只用于重建快照；bootstrap 必须跳过全部 DROP。
            skipped.append(statement)
            continue
        if first_keyword in {"TRUNCATE", "DELETE"} or re.search(
            r"\bALTER\s+TABLE\b[\s\S]*\bDROP\b",
            statement,
            flags=re.IGNORECASE,
        ):
            raise ValueError(f"初始化快照包含不允许执行的破坏性语句：{first_keyword}")
        transformed = _make_create_idempotent(statement)
        if preserve_existing_seed:
            transformed = _preserve_existing_admin_seed(transformed)
        statements.append(transformed)
    return BootstrapPlan(tuple(statements), tuple(skipped), source_path)


def load_bootstrap_plan(
    init_sql_path: str | os.PathLike[str] | None = None,
    *,
    preserve_existing_seed: bool = True,
) -> BootstrapPlan:
    """读取 `infra/sql/init.sql` 并构造安全执行计划。"""
    path = Path(init_sql_path) if init_sql_path else DEFAULT_INIT_SQL_PATH
    if not path.is_file():
        raise FileNotFoundError(f"找不到数据库初始化脚本：{path}")
    return build_bootstrap_plan(
        path.read_text(encoding="utf-8"),
        source_path=path,
        preserve_existing_seed=preserve_existing_seed,
    )


def _resolve_database_url(database_url: str | None) -> str:
    """按显式参数和项目约定解析 PostgreSQL 连接串。"""
    value = (database_url or os.getenv("RAG_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not value:
        raise ValueError("未配置数据库连接串，请传入 --database-url 或设置 RAG_DATABASE_URL")
    return value


def bootstrap_database(
    database_url: str | None = None,
    *,
    init_sql_path: str | os.PathLike[str] | None = None,
    preserve_existing_seed: bool = True,
    apply_incremental_migrations: bool = True,
) -> BootstrapResult:
    """执行安全初始化并按需补充 Python 增量迁移。"""
    plan = load_bootstrap_plan(init_sql_path, preserve_existing_seed=preserve_existing_seed)
    url = _resolve_database_url(database_url)
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("数据库初始化需要安装 psycopg[binary]") from exc

    try:
        with psycopg.connect(url) as connection:
            with connection.cursor() as cursor:
                for statement in plan.statements:
                    cursor.execute(statement)
    except Exception as exc:
        raise RuntimeError("PostgreSQL/pgvector 非破坏性初始化失败，事务已回滚") from exc

    applied_migrations: tuple[str, ...] = ()
    if apply_incremental_migrations:
        from app.core.database_migrations import apply_python_schema_migrations

        applied_migrations = tuple(apply_python_schema_migrations(url))
    return BootstrapResult(len(plan.statements), len(plan.skipped_statements), applied_migrations)


def _build_parser() -> argparse.ArgumentParser:
    """构造命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="安全初始化 PostgreSQL/pgvector 数据库")
    parser.add_argument("--database-url", help="PostgreSQL 连接串，默认读取 RAG_DATABASE_URL")
    parser.add_argument("--init-sql", help="自定义 init.sql 路径，默认读取仓库 infra/sql/init.sql")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只转换并校验 SQL，不连接数据库",
    )
    parser.add_argument(
        "--print-sql",
        action="store_true",
        help="dry-run 时输出转换后的 SQL",
    )
    parser.add_argument(
        "--overwrite-admin-seed",
        action="store_true",
        help="允许用 init.sql 中的默认管理员种子更新已有 admin 账号",
    )
    parser.add_argument(
        "--skip-incremental-migrations",
        action="store_true",
        help="初始化后不调用 Python 非破坏性增量迁移",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    """执行命令行 bootstrap，返回适合脚本使用的退出码。"""
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    plan = load_bootstrap_plan(
        args.init_sql,
        preserve_existing_seed=not args.overwrite_admin_seed,
    )
    if args.dry_run:
        print(f"安全初始化 dry-run：将执行 {len(plan.statements)} 条语句，跳过 {len(plan.skipped_statements)} 条 DROP 语句。")
        if args.print_sql:
            print(plan.sql)
        return 0

    result = bootstrap_database(
        args.database_url,
        init_sql_path=args.init_sql,
        preserve_existing_seed=not args.overwrite_admin_seed,
        apply_incremental_migrations=not args.skip_incremental_migrations,
    )
    migration_text = ", ".join(result.applied_migrations) if result.applied_migrations else "无"
    print(
        f"数据库初始化完成：执行 {result.executed_statements} 条语句，"
        f"跳过 {result.skipped_statements} 条 DROP 语句，增量迁移：{migration_text}。"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - 通过命令行入口执行
    raise SystemExit(main())
