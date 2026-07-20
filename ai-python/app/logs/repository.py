"""系统日志与资料进度同步的 psycopg 仓储。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import os
import re
from typing import Any, Protocol


DEFAULT_SCHEMA = "learning_evidence"
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class LogEventRecord:
    """`log_event` 写入和展示所需的字段。"""

    id: int | None
    trace_id: str
    session_id: str | None
    user_id: str
    source: str
    domain: str
    level: str
    module: str
    stage: str | None
    event_type: str
    action: str
    message: str | None
    route: str | None
    http_method: str | None
    request_path: str | None
    status_code: int | None
    success: bool
    duration_ms: int | None
    material_id: int | None
    document_id: str | None
    parser: str | None
    client_time: datetime | None
    server_time: datetime
    context_json: str
    created_at: datetime | None = None


@dataclass(frozen=True)
class LogErrorRecord:
    """`log_error` 写入和展示所需的字段。"""

    id: int | None
    trace_id: str
    session_id: str | None
    user_id: str
    source: str
    domain: str
    severity: str
    module: str
    stage: str | None
    action: str | None
    error_type: str
    error_code: str | None
    message: str
    stack_trace: str | None
    fingerprint: str
    route: str | None
    http_method: str | None
    request_path: str | None
    status_code: int | None
    duration_ms: int | None
    material_id: int | None
    document_id: str | None
    parser: str | None
    client_time: datetime | None
    server_time: datetime
    context_json: str
    status: str = "OPEN"
    occurrence_count: int = 1
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    created_at: datetime | None = None


class LogTransaction(Protocol):
    """日志服务依赖的最小事务协议，允许单元测试替换数据库。"""

    def insert_event(self, record: LogEventRecord) -> int: ...

    def find_error_id_by_fingerprint(self, fingerprint: str) -> int | None: ...

    def insert_error(self, record: LogErrorRecord) -> int: ...

    def increase_error_occurrence(self, fingerprint: str, last_seen_at: datetime) -> None: ...

    def update_material_progress(self, material_id: int, status: str, parser: str | None, chunk_count: int | None) -> None: ...

    def list_recent_events(self, limit: int) -> list[LogEventRecord]: ...

    def list_recent_errors(self, limit: int) -> list[LogErrorRecord]: ...

    def count_events_since(self, start_at: datetime) -> int: ...

    def count_errors_since(self, start_at: datetime) -> int: ...

    def count_open_errors_since(self, start_at: datetime) -> int: ...

    def count_errors_by_source_since(self, source: str, start_at: datetime) -> int: ...


class LogRepositoryProtocol(Protocol):
    """日志服务可替换的仓储边界。"""

    def transaction(self) -> Iterator[LogTransaction]: ...


class DatabaseLogTransaction:
    """单个 PostgreSQL 事务内的日志 SQL 操作。"""

    def __init__(self, cursor: Any, schema: str) -> None:
        self._cursor = cursor
        self._schema = schema

    def insert_event(self, record: LogEventRecord) -> int:
        """写入事件并返回数据库生成的主键。"""
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.log_event (
                    trace_id, session_id, user_id, source, domain, level, module, stage,
                    event_type, action, message, route, http_method, request_path,
                    status_code, success, duration_ms, material_id, document_id, parser,
                    client_time, server_time, context_json
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """
            ),
            (
                record.trace_id,
                record.session_id,
                record.user_id,
                record.source,
                record.domain,
                record.level,
                record.module,
                record.stage,
                record.event_type,
                record.action,
                record.message,
                record.route,
                record.http_method,
                record.request_path,
                record.status_code,
                record.success,
                record.duration_ms,
                record.material_id,
                record.document_id,
                record.parser,
                record.client_time,
                record.server_time,
                record.context_json,
            ),
        )
        row = self._cursor.fetchone()
        if row is None:
            raise RuntimeError("写入事件日志失败")
        return int(row["id"])

    def find_error_id_by_fingerprint(self, fingerprint: str) -> int | None:
        """读取同类错误的现有主键。"""
        self._cursor.execute(
            self._statement("SELECT id FROM {schema}.log_error WHERE fingerprint = %s"),
            (fingerprint,),
        )
        row = self._cursor.fetchone()
        return int(row["id"]) if row is not None else None

    def insert_error(self, record: LogErrorRecord) -> int:
        """写入新的错误聚合记录。"""
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.log_error (
                    trace_id, session_id, user_id, source, domain, severity, module, stage,
                    action, error_type, error_code, message, stack_trace, fingerprint,
                    route, http_method, request_path, status_code, duration_ms, material_id,
                    document_id, parser, client_time, server_time, context_json, status
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """
            ),
            (
                record.trace_id,
                record.session_id,
                record.user_id,
                record.source,
                record.domain,
                record.severity,
                record.module,
                record.stage,
                record.action,
                record.error_type,
                record.error_code,
                record.message,
                record.stack_trace,
                record.fingerprint,
                record.route,
                record.http_method,
                record.request_path,
                record.status_code,
                record.duration_ms,
                record.material_id,
                record.document_id,
                record.parser,
                record.client_time,
                record.server_time,
                record.context_json,
                record.status,
            ),
        )
        row = self._cursor.fetchone()
        if row is None:
            raise RuntimeError("写入错误日志失败")
        return int(row["id"])

    def increase_error_occurrence(self, fingerprint: str, last_seen_at: datetime) -> None:
        """同指纹错误仅增加出现次数并刷新最后出现时间。"""
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.log_error
                SET occurrence_count = occurrence_count + 1,
                    last_seen_at = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE fingerprint = %s
                """
            ),
            (last_seen_at, fingerprint),
        )

    def update_material_progress(self, material_id: int, status: str, parser: str | None, chunk_count: int | None) -> None:
        """根据用户可见 RAG 进度回写资料主状态。"""
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_material
                SET status = %s,
                    parser = COALESCE(%s, parser),
                    chunk_count = COALESCE(%s, chunk_count),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """
            ),
            (status, parser, chunk_count, material_id),
        )

    def list_recent_events(self, limit: int) -> list[LogEventRecord]:
        """按创建时间倒序读取事件日志。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT id, trace_id, session_id, user_id, source, domain, level, module, stage,
                       event_type, action, message, route, http_method, request_path,
                       status_code, success, duration_ms, material_id, document_id, parser,
                       client_time, server_time, context_json, created_at
                FROM {schema}.log_event
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """
            ),
            (limit,),
        )
        return [self._to_event(row) for row in self._cursor.fetchall()]

    def list_recent_errors(self, limit: int) -> list[LogErrorRecord]:
        """按最后出现时间倒序读取错误聚合。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT id, trace_id, session_id, user_id, source, domain, severity, module, stage,
                       action, error_type, error_code, message, stack_trace, fingerprint, route,
                       http_method, request_path, status_code, duration_ms, material_id, document_id,
                       parser, client_time, server_time, context_json, first_seen_at, last_seen_at,
                       occurrence_count, status, created_at
                FROM {schema}.log_error
                ORDER BY last_seen_at DESC, id DESC
                LIMIT %s
                """
            ),
            (limit,),
        )
        return [self._to_error(row) for row in self._cursor.fetchall()]

    def count_events_since(self, start_at: datetime) -> int:
        """统计指定时间后的事件总数。"""
        return self._count("SELECT COUNT(1) AS count FROM {schema}.log_event WHERE created_at >= %s", (start_at,))

    def count_errors_since(self, start_at: datetime) -> int:
        """统计指定时间后的错误总数。"""
        return self._count("SELECT COUNT(1) AS count FROM {schema}.log_error WHERE created_at >= %s", (start_at,))

    def count_open_errors_since(self, start_at: datetime) -> int:
        """统计指定时间后仍未关闭的错误数。"""
        return self._count(
            "SELECT COUNT(1) AS count FROM {schema}.log_error WHERE created_at >= %s AND status = 'OPEN'",
            (start_at,),
        )

    def count_errors_by_source_since(self, source: str, start_at: datetime) -> int:
        """按来源统计指定时间后的错误数。"""
        return self._count(
            "SELECT COUNT(1) AS count FROM {schema}.log_error WHERE created_at >= %s AND source = %s",
            (start_at, source),
        )

    def _count(self, query: str, params: tuple[object, ...]) -> int:
        self._cursor.execute(self._statement(query), params)
        row = self._cursor.fetchone() or {}
        return int(row.get("count") or 0)

    def _statement(self, query: str) -> Any:
        """通过 psycopg 标识符 API 安全引用 schema。"""
        from psycopg import sql

        return sql.SQL(query).format(schema=sql.Identifier(self._schema))

    @staticmethod
    def _to_event(row: dict[str, Any]) -> LogEventRecord:
        return LogEventRecord(
            id=int(row["id"]),
            trace_id=str(row["trace_id"]),
            session_id=row.get("session_id"),
            user_id=str(row["user_id"]),
            source=str(row["source"]),
            domain=str(row["domain"]),
            level=str(row["level"]),
            module=str(row["module"]),
            stage=row.get("stage"),
            event_type=str(row["event_type"]),
            action=str(row["action"]),
            message=row.get("message"),
            route=row.get("route"),
            http_method=row.get("http_method"),
            request_path=row.get("request_path"),
            status_code=row.get("status_code"),
            success=bool(row.get("success")),
            duration_ms=row.get("duration_ms"),
            material_id=row.get("material_id"),
            document_id=row.get("document_id"),
            parser=row.get("parser"),
            client_time=row.get("client_time"),
            server_time=row.get("server_time"),
            context_json=str(row.get("context_json") or "{}"),
            created_at=row.get("created_at"),
        )

    @staticmethod
    def _to_error(row: dict[str, Any]) -> LogErrorRecord:
        return LogErrorRecord(
            id=int(row["id"]),
            trace_id=str(row["trace_id"]),
            session_id=row.get("session_id"),
            user_id=str(row["user_id"]),
            source=str(row["source"]),
            domain=str(row["domain"]),
            severity=str(row["severity"]),
            module=str(row["module"]),
            stage=row.get("stage"),
            action=row.get("action"),
            error_type=str(row["error_type"]),
            error_code=row.get("error_code"),
            message=str(row["message"]),
            stack_trace=row.get("stack_trace"),
            fingerprint=str(row["fingerprint"]),
            route=row.get("route"),
            http_method=row.get("http_method"),
            request_path=row.get("request_path"),
            status_code=row.get("status_code"),
            duration_ms=row.get("duration_ms"),
            material_id=row.get("material_id"),
            document_id=row.get("document_id"),
            parser=row.get("parser"),
            client_time=row.get("client_time"),
            server_time=row.get("server_time"),
            context_json=str(row.get("context_json") or "{}"),
            status=str(row.get("status") or "OPEN"),
            occurrence_count=int(row.get("occurrence_count") or 1),
            first_seen_at=row.get("first_seen_at"),
            last_seen_at=row.get("last_seen_at"),
            created_at=row.get("created_at"),
        )


class LogRepository:
    """通过 psycopg 管理日志写入、查询和资料进度同步事务。"""

    def __init__(self, database_url: str | None = None, schema: str | None = None) -> None:
        self._database_url = database_url or resolve_database_url()
        self._schema = validate_schema(schema or os.getenv("RAG_DATABASE_SCHEMA", DEFAULT_SCHEMA))

    @contextmanager
    def transaction(self) -> Iterator[LogTransaction]:
        """开启提交或回滚一致的 PostgreSQL 事务。"""
        connection = self._connect()
        try:
            with connection:
                with connection.cursor() as cursor:
                    yield DatabaseLogTransaction(cursor, self._schema)
        finally:
            connection.close()

    def _connect(self) -> Any:
        """延迟导入驱动，支持无数据库依赖替换测试。"""
        if not self._database_url:
            raise RuntimeError("未配置 LOG_DATABASE_URL、RAG_DATABASE_URL 或 DATABASE_URL")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("日志仓储需要安装 psycopg[binary]") from exc
        return psycopg.connect(self._database_url, row_factory=dict_row)


def resolve_database_url() -> str:
    """按日志专用、RAG 复用、通用数据库顺序读取连接串。"""
    return (
        os.getenv("LOG_DATABASE_URL", "").strip()
        or os.getenv("RAG_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
    )


def validate_schema(value: str) -> str:
    """仅接受合法简单 PostgreSQL schema 标识符。"""
    if not SCHEMA_PATTERN.fullmatch(value):
        raise RuntimeError("RAG_DATABASE_SCHEMA 必须是合法的 PostgreSQL schema 标识符")
    return value
