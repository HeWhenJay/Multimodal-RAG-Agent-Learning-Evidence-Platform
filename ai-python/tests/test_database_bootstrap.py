from app.core.database_bootstrap import (
    DEFAULT_INIT_SQL_PATH,
    build_bootstrap_plan,
    load_bootstrap_plan,
    main,
    split_sql_statements,
)


def test_split_sql_statements_keeps_semicolons_inside_literals_and_comments():
    """SQL 拆分器不能把字符串、注释或 dollar quote 内的分号误当成边界。"""
    statements = split_sql_statements(
        """
        -- 注释；不能拆分
        CREATE TABLE demo (value TEXT DEFAULT 'a;b');
        DO $$ BEGIN RAISE NOTICE 'x;y'; END $$;
        """
    )

    assert len(statements) == 2
    assert "'a;b'" in statements[0]
    assert "NOTICE 'x;y'" in statements[1]


def test_init_snapshot_is_converted_to_non_destructive_plan():
    """权威 init.sql 转换后不再含 DROP，并且表和索引可重复执行。"""
    plan = load_bootstrap_plan()
    sql = plan.sql.upper()

    assert plan.source_path == DEFAULT_INIT_SQL_PATH
    assert len(plan.skipped_statements) >= 20
    assert "DROP TABLE" not in sql
    assert "CREATE TABLE IF NOT EXISTS LEARNING_EVIDENCE.APP_USER" in sql
    assert "CREATE UNIQUE INDEX IF NOT EXISTS UK_RAG_QUERY_HISTORY_TASK_ID" in sql
    assert "ON CONFLICT (ACCOUNT) DO NOTHING" in sql
    assert all("CREATE INDEX " not in statement.upper() or "IF NOT EXISTS" in statement.upper() for statement in plan.statements)


def test_bootstrap_rejects_other_destructive_sql():
    """未来误把 TRUNCATE/DELETE 写入快照时，bootstrap 应 fail closed。"""
    for statement in (
        "TRUNCATE TABLE learning_evidence.app_user;",
        "ALTER TABLE learning_evidence.app_user DROP COLUMN email;",
    ):
        try:
            build_bootstrap_plan(statement)
        except ValueError as exc:
            assert "破坏性" in str(exc)
        else:  # pragma: no cover - 仅用于让失败信息明确
            raise AssertionError("bootstrap 未拒绝破坏性语句")


def test_dry_run_does_not_require_database_connection(capsys):
    """dry-run 可在没有 PostgreSQL/psycopg 连接时验证转换结果。"""
    assert main(["--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "安全初始化 dry-run" in output
    assert "DROP" in output
