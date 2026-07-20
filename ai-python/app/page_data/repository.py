"""工作台与设置页面数据的 psycopg 仓储。"""

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
class PageMaterialRecord:
    """工作台资料卡片需要的 `learning_material` 字段。"""

    id: int
    title: str
    user_id: str
    document_type: str
    source: str | None
    status: str
    parser: str | None
    document_summary: str | None
    chunk_count: int
    original_filename: str | None
    original_file_path: str | None
    storage_type: str | None
    object_key: str | None
    public_url: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class PageProgressRecord:
    """资料卡片转换 RAG 进度所需的日志字段。"""

    id: int
    stage: str | None
    message: str | None
    success: bool | None
    context_json: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class SystemSettingRecord:
    """设置页展示项。"""

    key: str
    group: str
    label: str
    value: str
    sort_order: int


class PageDataTransaction(Protocol):
    """页面服务可替换的最小数据库操作集。"""

    def material_count(self, user_id: str) -> int: ...

    def material_count_since(self, user_id: str, start_at: datetime) -> int: ...

    def chunk_count(self, user_id: str) -> int: ...

    def count_errors_since(self, start_at: datetime) -> int: ...

    def count_open_errors_since(self, start_at: datetime) -> int: ...

    def list_materials_between(self, user_id: str, start_at: datetime, end_at: datetime, limit: int) -> list[PageMaterialRecord]: ...

    def list_progress(self, material_id: int, limit: int, video_only: bool = False) -> list[PageProgressRecord]: ...

    def list_settings(self) -> list[SystemSettingRecord]: ...


class PageDataRepositoryProtocol(Protocol):
    """便于测试替换实际 PostgreSQL 仓储。"""

    def transaction(self) -> Iterator[PageDataTransaction]: ...


class DatabasePageDataTransaction:
    """单个 PostgreSQL 事务内的页面数据读取。"""

    VIDEO_PROGRESS_STAGES = (
        "parse.video.asr",
        "parse.video.frame.extract",
        "parse.video.frame.candidates",
        "parse.video.slide_detect",
        "parse.video.ocr",
    )

    def __init__(self, cursor: Any, schema: str) -> None:
        self._cursor = cursor
        self._schema = schema

    def material_count(self, user_id: str) -> int:
        """统计当前用户资料数。"""
        return self._count("SELECT COUNT(1) AS count FROM {schema}.learning_material WHERE user_id = %s", (user_id,))

    def material_count_since(self, user_id: str, start_at: datetime) -> int:
        """统计当前用户近期新增资料数。"""
        return self._count(
            "SELECT COUNT(1) AS count FROM {schema}.learning_material WHERE user_id = %s AND created_at >= %s",
            (user_id, start_at),
        )

    def chunk_count(self, user_id: str) -> int:
        """汇总当前用户已记录的递归切块数量。"""
        return self._count(
            "SELECT COALESCE(SUM(chunk_count), 0) AS count FROM {schema}.learning_material WHERE user_id = %s",
            (user_id,),
        )

    def count_errors_since(self, start_at: datetime) -> int:
        """按 Java 工作台口径统计全局近期错误数。"""
        return self._count(
            "SELECT COUNT(1) AS count FROM {schema}.log_error WHERE created_at >= %s",
            (start_at,),
        )

    def count_open_errors_since(self, start_at: datetime) -> int:
        """按 Java 工作台口径统计全局未关闭错误数。"""
        return self._count(
            "SELECT COUNT(1) AS count FROM {schema}.log_error WHERE created_at >= %s AND status = 'OPEN'",
            (start_at,),
        )

    def list_materials_between(self, user_id: str, start_at: datetime, end_at: datetime, limit: int) -> list[PageMaterialRecord]:
        """按更新时间倒序读取指定日期范围内的资料。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT id, title, user_id, document_type, source, status, parser, document_summary,
                       chunk_count, original_filename, original_file_path, storage_type, object_key,
                       public_url, created_at, updated_at
                FROM {schema}.learning_material
                WHERE user_id = %s
                  AND updated_at >= %s
                  AND updated_at < %s
                ORDER BY updated_at DESC, id DESC
                LIMIT %s
                """
            ),
            (user_id, start_at, end_at, limit),
        )
        return [self._to_material(row) for row in self._cursor.fetchall()]

    def list_progress(self, material_id: int, limit: int, video_only: bool = False) -> list[PageProgressRecord]:
        """读取资料的最近 RAG 进度，视频模式额外筛选关键阶段。"""
        stage_clause = ""
        params: tuple[object, ...] = (material_id, limit)
        if video_only:
            stage_clause = " AND stage IN ('parse.video.asr', 'parse.video.frame.extract', 'parse.video.frame.candidates', 'parse.video.slide_detect', 'parse.video.ocr')"
        self._cursor.execute(
            self._statement(
                """
                SELECT id, stage, message, success, context_json, created_at
                FROM {schema}.log_event
                WHERE domain = 'rag'
                  AND event_type = 'rag_progress'
                  AND material_id = %s
                """
                + stage_clause
                + " ORDER BY created_at DESC, id DESC LIMIT %s"
            ),
            params,
        )
        return [
            PageProgressRecord(
                id=int(row["id"]),
                stage=row.get("stage"),
                message=row.get("message"),
                success=row.get("success"),
                context_json=row.get("context_json"),
                created_at=row.get("created_at"),
            )
            for row in self._cursor.fetchall()
        ]

    def list_settings(self) -> list[SystemSettingRecord]:
        """按原 Java 排序规则读取系统设置。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT setting_key, setting_group, label, setting_value, sort_order
                FROM {schema}.system_setting
                ORDER BY setting_group ASC, sort_order ASC, setting_key ASC
                """
            )
        )
        return [
            SystemSettingRecord(
                key=str(row["setting_key"]),
                group=str(row["setting_group"]),
                label=str(row["label"]),
                value=str(row["setting_value"]),
                sort_order=int(row.get("sort_order") or 0),
            )
            for row in self._cursor.fetchall()
        ]

    def _count(self, query: str, params: tuple[object, ...]) -> int:
        self._cursor.execute(self._statement(query), params)
        row = self._cursor.fetchone() or {}
        return int(row.get("count") or 0)

    def _statement(self, query: str) -> Any:
        """使用 psycopg 标识符 API 安全引用 schema。"""
        from psycopg import sql

        return sql.SQL(query).format(schema=sql.Identifier(self._schema))

    @staticmethod
    def _to_material(row: dict[str, Any]) -> PageMaterialRecord:
        return PageMaterialRecord(
            id=int(row["id"]),
            title=str(row["title"]),
            user_id=str(row["user_id"]),
            document_type=str(row["document_type"]),
            source=row.get("source"),
            status=str(row["status"]),
            parser=row.get("parser"),
            document_summary=row.get("document_summary"),
            chunk_count=int(row.get("chunk_count") or 0),
            original_filename=row.get("original_filename"),
            original_file_path=row.get("original_file_path"),
            storage_type=row.get("storage_type"),
            object_key=row.get("object_key"),
            public_url=row.get("public_url"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


class PageDataRepository:
    """通过 psycopg 管理工作台和设置查询事务。"""

    def __init__(self, database_url: str | None = None, schema: str | None = None) -> None:
        self._database_url = database_url or resolve_database_url()
        self._schema = validate_schema(schema or os.getenv("RAG_DATABASE_SCHEMA", DEFAULT_SCHEMA))

    @contextmanager
    def transaction(self) -> Iterator[PageDataTransaction]:
        """开启只读用途的提交或回滚一致事务。"""
        connection = self._connect()
        try:
            with connection:
                with connection.cursor() as cursor:
                    yield DatabasePageDataTransaction(cursor, self._schema)
        finally:
            connection.close()

    def _connect(self) -> Any:
        """延迟导入驱动，避免依赖替换测试访问数据库。"""
        if not self._database_url:
            raise RuntimeError("未配置 PAGE_DATA_DATABASE_URL、RAG_DATABASE_URL 或 DATABASE_URL")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("页面数据仓储需要安装 psycopg[binary]") from exc
        return psycopg.connect(self._database_url, row_factory=dict_row)


def resolve_database_url() -> str:
    """按页面专用、RAG 复用、通用数据库顺序读取连接串。"""
    return (
        os.getenv("PAGE_DATA_DATABASE_URL", "").strip()
        or os.getenv("RAG_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
    )


def validate_schema(value: str) -> str:
    """仅接受合法简单 PostgreSQL schema 标识符。"""
    if not SCHEMA_PATTERN.fullmatch(value):
        raise RuntimeError("RAG_DATABASE_SCHEMA 必须是合法的 PostgreSQL schema 标识符")
    return value
