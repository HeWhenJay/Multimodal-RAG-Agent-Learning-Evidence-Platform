"""学习复习能力使用的 PostgreSQL 事务仓储。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
import json
import os
import re
from typing import Any, Protocol

from app.schemas.rag import Evidence
from prompts.review import REVIEW_CARD_PROMPT_VERSION
from rag.core.source_references import public_http_source
from rag.retrievers.evidence_diversity import build_evidence_metadata_view
from rag.retrievers.retrieval import as_optional_int, as_optional_str, build_playback_url


DEFAULT_SCHEMA = "learning_evidence"
SCHEMA_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CURRENT_REVIEW_EXTRACTORS = (
    f"local:{REVIEW_CARD_PROMPT_VERSION}",
    f"model:{REVIEW_CARD_PROMPT_VERSION}",
    f"none:{REVIEW_CARD_PROMPT_VERSION}",
)


@dataclass(frozen=True)
class MaterialSourceRecord:
    """待分类学习资料及其当前索引版本。"""

    id: int
    title: str
    user_id: str
    document_type: str
    material_status: str
    document_summary: str | None
    index_request_version: int
    updated_at: datetime | None


@dataclass(frozen=True)
class ReviewMaterialRecord:
    """资料与复习分类状态的联合快照。"""

    material_id: int
    title: str
    document_type: str
    material_status: str
    is_learning_content: bool | None
    category: str | None
    status: str
    reason: str | None
    extractor: str | None
    card_count: int
    index_request_version: int
    synced_index_request_version: int | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ReviewCardDraft:
    """新提炼卡片的持久化输入。"""

    source_key: str
    question: str
    answer: str
    hint: str | None
    evidence_refs_json: str
    fsrs_card_json: str
    due_at: datetime


@dataclass(frozen=True)
class ReviewCardRecord:
    """数据库中的一张复习卡片。"""

    id: int
    material_id: int
    user_id: str
    material_title: str
    document_type: str
    question: str
    answer: str
    hint: str | None
    evidence_refs_json: str
    fsrs_card_json: str
    due_at: datetime
    retrievability: float
    review_count: int
    lapse_count: int
    active: bool
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class ReviewSettingsRecord:
    """数据库中的用户复习设置。"""

    user_id: str
    enabled: bool
    desired_retention: float
    daily_limit: int
    reminder_time: str
    timezone: str


@dataclass(frozen=True)
class ReviewOverviewStats:
    """复习中心所需的聚合统计。"""

    due_count: int
    today_reviewed_count: int
    total_card_count: int
    active_material_count: int
    next_due_at: datetime | None


class ReviewTransaction(Protocol):
    """复习服务在单个事务内需要的最小操作集合。"""

    def list_sync_candidates(self, user_id: str, limit: int) -> list[MaterialSourceRecord]: ...

    def find_material(self, material_id: int, user_id: str) -> MaterialSourceRecord | None: ...

    def list_evidences(self, material: MaterialSourceRecord, limit: int = 24) -> list[Evidence]: ...

    def save_generation(
        self,
        material: MaterialSourceRecord,
        *,
        is_learning_content: bool,
        category: str | None,
        status: str,
        reason: str,
        extractor: str,
        cards: list[ReviewCardDraft],
    ) -> ReviewMaterialRecord: ...

    def list_review_materials(self, user_id: str, limit: int = 100) -> list[ReviewMaterialRecord]: ...

    def find_review_material(self, material_id: int, user_id: str) -> ReviewMaterialRecord | None: ...

    def get_or_create_settings(
        self,
        user_id: str,
        *,
        for_update: bool = False,
    ) -> ReviewSettingsRecord: ...

    def update_settings(
        self,
        user_id: str,
        *,
        enabled: bool,
        desired_retention: float,
        daily_limit: int,
        reminder_time: str,
        timezone: str,
    ) -> ReviewSettingsRecord: ...

    def overview_stats(
        self,
        user_id: str,
        *,
        now: datetime,
        today_start: datetime,
        tomorrow_start: datetime,
    ) -> ReviewOverviewStats: ...

    def list_due_cards(self, user_id: str, *, now: datetime, limit: int) -> list[ReviewCardRecord]: ...

    def find_card(self, card_id: int, user_id: str) -> ReviewCardRecord | None: ...

    def find_card_for_update(self, card_id: int, user_id: str) -> ReviewCardRecord | None: ...

    def save_grade(
        self,
        card: ReviewCardRecord,
        *,
        rating: int,
        duration_ms: int | None,
        reviewed_at: datetime,
        previous_due_at: datetime,
        next_due_at: datetime,
        interval_days: float,
        retrievability: float,
        fsrs_card_json: str,
        fsrs_review_log_json: str,
        state_rebuilt: bool,
    ) -> ReviewCardRecord: ...


class ReviewRepositoryProtocol(Protocol):
    """支持在测试中注入内存事务仓储。"""

    def transaction(self) -> Iterator[ReviewTransaction]: ...


class DatabaseReviewTransaction:
    """在同一 psycopg 事务中执行复习数据读写。"""

    def __init__(self, cursor: Any, schema: str) -> None:
        self._cursor = cursor
        self._schema = schema

    def list_sync_candidates(self, user_id: str, limit: int) -> list[MaterialSourceRecord]:
        """扫描当前用户已完成索引且当前版本尚未分类的资料。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT lm.*
                FROM {schema}.learning_material lm
                LEFT JOIN {schema}.learning_review_material rm ON rm.material_id = lm.id
                WHERE lm.user_id = %s
                  AND lm.status IN ('READY', 'PARTIAL')
                  AND (
                      rm.material_id IS NULL
                      OR rm.index_request_version < lm.index_request_version
                      OR rm.extractor IS NULL
                      OR rm.extractor NOT IN (%s, %s, %s)
                  )
                ORDER BY lm.updated_at ASC, lm.id ASC
                LIMIT %s
                """
            ),
            (user_id, *CURRENT_REVIEW_EXTRACTORS, limit),
        )
        return [self._to_material(row) for row in self._cursor.fetchall()]

    def find_material(self, material_id: int, user_id: str) -> MaterialSourceRecord | None:
        """按资料 ID 和当前用户联合查询，避免生成接口越权。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT *
                FROM {schema}.learning_material
                WHERE id = %s AND user_id = %s
                """
            ),
            (material_id, user_id),
        )
        row = self._cursor.fetchone()
        return self._to_material(row) if row else None

    def list_evidences(self, material: MaterialSourceRecord, limit: int = 24) -> list[Evidence]:
        """读取 canonical RAG 切块，并保留视频时间段和文本预览链接。"""
        document_id = f"material-{material.id}"
        self._cursor.execute(
            self._statement(
                """
                SELECT
                    c.chunk_id, c.document_id, c.chunk_position, c.section_name,
                    c.text, c.metadata, d.title, d.source, d.document_type
                FROM {schema}.rag_chunk c
                JOIN {schema}.rag_document d ON d.document_id = c.document_id
                WHERE c.document_id = %s
                  AND d.user_id = %s
                  AND d.visibility_scope = 'private'
                ORDER BY
                    CASE WHEN c.metadata ->> 'childKind' = 'summary' THEN 0 ELSE 1 END,
                    c.chunk_position ASC
                LIMIT %s
                """
            ),
            (document_id, material.user_id, limit),
        )
        return [self._to_evidence(row, material.id) for row in self._cursor.fetchall()]

    def save_generation(
        self,
        material: MaterialSourceRecord,
        *,
        is_learning_content: bool,
        category: str | None,
        status: str,
        reason: str,
        extractor: str,
        cards: list[ReviewCardDraft],
    ) -> ReviewMaterialRecord:
        """更新分类并按稳定来源键刷新正文，已有卡片不重置 FSRS 状态。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT index_request_version
                FROM {schema}.learning_material
                WHERE id = %s AND user_id = %s
                FOR UPDATE
                """
            ),
            (material.id, material.user_id),
        )
        current_material = self._cursor.fetchone()
        if current_material is None:
            raise RuntimeError("保存复习卡片时资料已不存在")
        if int(current_material.get("index_request_version") or 0) != material.index_request_version:
            # 提炼期间资料已进入新索引版本时，丢弃旧结果并等待下一轮同步。
            current = self._find_review_material(material.id, material.user_id)
            if current is not None:
                return current
            raise RuntimeError("保存复习卡片时资料索引版本已变化")
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.learning_review_material (
                    material_id, user_id, index_request_version, is_learning_content,
                    category, status, reason, extractor, card_count, generated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, CURRENT_TIMESTAMP)
                ON CONFLICT (material_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    index_request_version = EXCLUDED.index_request_version,
                    is_learning_content = EXCLUDED.is_learning_content,
                    category = EXCLUDED.category,
                    status = EXCLUDED.status,
                    reason = EXCLUDED.reason,
                    extractor = EXCLUDED.extractor,
                    generated_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE {schema}.learning_review_material.index_request_version <= EXCLUDED.index_request_version
                RETURNING id
                """
            ),
            (
                material.id,
                material.user_id,
                material.index_request_version,
                is_learning_content,
                category,
                status,
                reason,
                extractor,
            ),
        )
        inserted_row = self._cursor.fetchone()
        if inserted_row is None:
            # 旧索引版本的模型结果晚到时，保留数据库中更新的复习状态。
            current = self._find_review_material(material.id, material.user_id)
            if current is not None:
                return current
            raise RuntimeError("保存复习资料分类时检测到索引版本冲突")
        review_material_id = int(inserted_row["id"])
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_card
                SET active = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE material_id = %s AND user_id = %s
                """
            ),
            (material.id, material.user_id),
        )
        for card in cards:
            self._cursor.execute(
                self._statement(
                    """
                    INSERT INTO {schema}.learning_review_card (
                        review_material_id, material_id, user_id, source_key,
                        question, answer, hint, evidence_refs, fsrs_card_json,
                        due_at, retrievability, review_count, lapse_count, active
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, 0, 0, 0, TRUE)
                    ON CONFLICT (material_id, source_key) DO UPDATE SET
                        review_material_id = EXCLUDED.review_material_id,
                        user_id = EXCLUDED.user_id,
                        question = EXCLUDED.question,
                        answer = EXCLUDED.answer,
                        hint = EXCLUDED.hint,
                        evidence_refs = EXCLUDED.evidence_refs,
                        active = TRUE,
                        updated_at = CURRENT_TIMESTAMP
                    """
                ),
                (
                    review_material_id,
                    material.id,
                    material.user_id,
                    card.source_key,
                    card.question,
                    card.answer,
                    card.hint,
                    card.evidence_refs_json,
                    card.fsrs_card_json,
                    card.due_at,
                ),
            )
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_material
                SET card_count = (
                    SELECT COUNT(1)
                    FROM {schema}.learning_review_card c
                    WHERE c.material_id = %s AND c.user_id = %s AND c.active = TRUE
                ), updated_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """
            ),
            (material.id, material.user_id, review_material_id),
        )
        record = self._find_review_material(material.id, material.user_id)
        if record is None:
            raise RuntimeError("保存复习资料分类后无法读取结果")
        return record

    def list_review_materials(self, user_id: str, limit: int = 100) -> list[ReviewMaterialRecord]:
        """展示资料当前索引版本及其复习同步状态。"""
        self._cursor.execute(
            self._review_material_select(
                "WHERE lm.user_id = %s ORDER BY lm.updated_at DESC, lm.id DESC LIMIT %s"
            ),
            (user_id, limit),
        )
        return [self._to_review_material(row) for row in self._cursor.fetchall()]

    def find_review_material(self, material_id: int, user_id: str) -> ReviewMaterialRecord | None:
        """按资料和用户读取当前复习生成状态，用于生成前幂等复核。"""
        return self._find_review_material(material_id, user_id)

    def get_or_create_settings(
        self,
        user_id: str,
        *,
        for_update: bool = False,
    ) -> ReviewSettingsRecord:
        """首次访问时创建默认设置，可在评分事务中锁定用户行。"""
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.learning_review_setting (user_id)
                VALUES (%s)
                ON CONFLICT (user_id) DO NOTHING
                """
            ),
            (user_id,),
        )
        select_statement = "SELECT * FROM {schema}.learning_review_setting WHERE user_id = %s"
        if for_update:
            select_statement += " FOR UPDATE"
        self._cursor.execute(self._statement(select_statement), (user_id,))
        return self._to_settings(self._cursor.fetchone())

    def update_settings(
        self,
        user_id: str,
        *,
        enabled: bool,
        desired_retention: float,
        daily_limit: int,
        reminder_time: str,
        timezone: str,
    ) -> ReviewSettingsRecord:
        """幂等写入用户设置，当前版本不批量改写既有卡片到期时间。"""
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.learning_review_setting (
                    user_id, enabled, desired_retention, daily_limit, reminder_time, timezone
                )
                VALUES (%s, %s, %s, %s, %s::time, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    desired_retention = EXCLUDED.desired_retention,
                    daily_limit = EXCLUDED.daily_limit,
                    reminder_time = EXCLUDED.reminder_time,
                    timezone = EXCLUDED.timezone,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING *
                """
            ),
            (user_id, enabled, desired_retention, daily_limit, reminder_time, timezone),
        )
        return self._to_settings(self._cursor.fetchone())

    def overview_stats(
        self,
        user_id: str,
        *,
        now: datetime,
        today_start: datetime,
        tomorrow_start: datetime,
    ) -> ReviewOverviewStats:
        """用持久化 due_at 和评分日志实时计算复习统计。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT
                    COUNT(1) FILTER (WHERE active = TRUE) AS total_card_count,
                    COUNT(DISTINCT material_id) FILTER (WHERE active = TRUE) AS active_material_count,
                    COUNT(1) FILTER (WHERE active = TRUE AND due_at <= %s) AS due_count,
                    MIN(due_at) FILTER (WHERE active = TRUE AND due_at > %s) AS next_due_at
                FROM {schema}.learning_review_card
                WHERE user_id = %s
                """
            ),
            (now, now, user_id),
        )
        card_stats = self._cursor.fetchone() or {}
        self._cursor.execute(
            self._statement(
                """
                SELECT COUNT(1) AS reviewed_count
                FROM {schema}.learning_review_log
                WHERE user_id = %s AND reviewed_at >= %s AND reviewed_at < %s
                """
            ),
            (user_id, today_start, tomorrow_start),
        )
        log_stats = self._cursor.fetchone() or {}
        return ReviewOverviewStats(
            due_count=int(card_stats.get("due_count") or 0),
            today_reviewed_count=int(log_stats.get("reviewed_count") or 0),
            total_card_count=int(card_stats.get("total_card_count") or 0),
            active_material_count=int(card_stats.get("active_material_count") or 0),
            next_due_at=card_stats.get("next_due_at"),
        )

    def list_due_cards(self, user_id: str, *, now: datetime, limit: int) -> list[ReviewCardRecord]:
        """按到期时间轮询资料读取当前用户卡片，避免单一资料独占每日队列。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT *
                FROM (
                    SELECT c.*, lm.title AS material_title, lm.document_type,
                           ROW_NUMBER() OVER (
                               PARTITION BY c.material_id
                               ORDER BY c.due_at ASC, c.id ASC
                           ) AS material_rank,
                           MIN(c.due_at) OVER (PARTITION BY c.material_id) AS group_due_at
                    FROM {schema}.learning_review_card c
                    JOIN {schema}.learning_material lm ON lm.id = c.material_id
                    WHERE c.user_id = %s
                      AND lm.user_id = %s
                      AND c.active = TRUE
                      AND c.due_at <= %s
                ) due_cards
                WHERE material_rank <= 4
                ORDER BY group_due_at ASC, material_id ASC, material_rank ASC
                LIMIT %s
                """
            ),
            (user_id, user_id, now, limit),
        )
        return [self._to_card(row) for row in self._cursor.fetchall()]

    def find_card(self, card_id: int, user_id: str) -> ReviewCardRecord | None:
        """读取当前用户的活动卡片，供用户主动揭示答案。"""
        self._cursor.execute(
            self._card_select("WHERE c.id = %s AND c.user_id = %s AND lm.user_id = %s AND c.active = TRUE"),
            (card_id, user_id, user_id),
        )
        row = self._cursor.fetchone()
        return self._to_card(row) if row else None

    def find_card_for_update(self, card_id: int, user_id: str) -> ReviewCardRecord | None:
        """锁定当前用户的一张活动卡片，供评分更新与日志追加共用事务。"""
        self._cursor.execute(
            self._card_select(
                "WHERE c.id = %s AND c.user_id = %s AND lm.user_id = %s AND c.active = TRUE FOR UPDATE OF c"
            ),
            (card_id, user_id, user_id),
        )
        row = self._cursor.fetchone()
        return self._to_card(row) if row else None

    def save_grade(
        self,
        card: ReviewCardRecord,
        *,
        rating: int,
        duration_ms: int | None,
        reviewed_at: datetime,
        previous_due_at: datetime,
        next_due_at: datetime,
        interval_days: float,
        retrievability: float,
        fsrs_card_json: str,
        fsrs_review_log_json: str,
        state_rebuilt: bool,
    ) -> ReviewCardRecord:
        """同一事务更新卡片状态并追加不可覆盖的评分日志。"""
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_card
                SET fsrs_card_json = %s,
                    due_at = %s,
                    retrievability = %s,
                    review_count = review_count + 1,
                    lapse_count = lapse_count + CASE WHEN %s = 1 THEN 1 ELSE 0 END,
                    last_reviewed_at = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND user_id = %s AND active = TRUE
                """
            ),
            (
                fsrs_card_json,
                next_due_at,
                retrievability,
                rating,
                reviewed_at,
                card.id,
                card.user_id,
            ),
        )
        if self._cursor.rowcount != 1:
            raise RuntimeError("评分更新复习卡片失败")
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.learning_review_log (
                    card_id, material_id, user_id, rating, duration_ms, reviewed_at,
                    previous_due_at, next_due_at, interval_days, retrievability,
                    fsrs_review_log_json, state_rebuilt
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """
            ),
            (
                card.id,
                card.material_id,
                card.user_id,
                rating,
                duration_ms,
                reviewed_at,
                previous_due_at,
                next_due_at,
                interval_days,
                retrievability,
                fsrs_review_log_json,
                state_rebuilt,
            ),
        )
        self._cursor.execute(
            self._card_select("WHERE c.id = %s AND c.user_id = %s AND lm.user_id = %s"),
            (card.id, card.user_id, card.user_id),
        )
        updated = self._cursor.fetchone()
        if updated is None:
            raise RuntimeError("评分后无法读取复习卡片")
        return self._to_card(updated)

    def _find_review_material(self, material_id: int, user_id: str) -> ReviewMaterialRecord | None:
        """读取一条资料的联合复习状态。"""
        self._cursor.execute(
            self._review_material_select("WHERE lm.id = %s AND lm.user_id = %s"),
            (material_id, user_id),
        )
        row = self._cursor.fetchone()
        return self._to_review_material(row) if row else None

    def _review_material_select(self, suffix: str) -> Any:
        """构造资料列表的统一 SELECT。"""
        return self._statement(
            f"""
            SELECT
                lm.id AS material_id, lm.title, lm.document_type,
                lm.status AS material_status, lm.index_request_version,
                rm.index_request_version AS synced_index_request_version,
                rm.is_learning_content, rm.category,
                COALESCE(rm.status, 'PENDING') AS review_status,
                rm.reason, COALESCE(rm.card_count, 0) AS card_count,
                rm.extractor,
                COALESCE(rm.updated_at, lm.updated_at) AS review_updated_at
            FROM {{schema}}.learning_material lm
            LEFT JOIN {{schema}}.learning_review_material rm ON rm.material_id = lm.id
            {suffix}
            """
        )

    def _card_select(self, suffix: str) -> Any:
        """构造卡片与资料标题的统一 SELECT。"""
        return self._statement(
            f"""
            SELECT c.*, lm.title AS material_title, lm.document_type
            FROM {{schema}}.learning_review_card c
            JOIN {{schema}}.learning_material lm ON lm.id = c.material_id
            {suffix}
            """
        )

    def _statement(self, query: str) -> Any:
        """使用 psycopg 标识符拼接 schema，拒绝配置值注入。"""
        from psycopg import sql

        return sql.SQL(query).format(schema=sql.Identifier(self._schema))

    @staticmethod
    def _to_material(row: dict[str, Any]) -> MaterialSourceRecord:
        """把 psycopg 行转换为资料记录。"""
        return MaterialSourceRecord(
            id=int(row["id"]),
            title=str(row["title"]),
            user_id=str(row["user_id"]),
            document_type=str(row["document_type"]),
            material_status=str(row["status"]),
            document_summary=row.get("document_summary"),
            index_request_version=int(row.get("index_request_version") or 0),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _to_review_material(row: dict[str, Any]) -> ReviewMaterialRecord:
        """把联合资料行转换为公开状态记录。"""
        return ReviewMaterialRecord(
            material_id=int(row["material_id"]),
            title=str(row["title"]),
            document_type=str(row["document_type"]),
            material_status=str(row["material_status"]),
            is_learning_content=row.get("is_learning_content"),
            category=row.get("category"),
            status=str(row.get("review_status") or "PENDING"),
            reason=row.get("reason"),
            extractor=row.get("extractor"),
            card_count=int(row.get("card_count") or 0),
            index_request_version=int(row.get("index_request_version") or 0),
            synced_index_request_version=(
                int(row["synced_index_request_version"])
                if row.get("synced_index_request_version") is not None
                else None
            ),
            updated_at=row.get("review_updated_at"),
        )

    @staticmethod
    def _to_card(row: dict[str, Any]) -> ReviewCardRecord:
        """把卡片行转换为业务记录。"""
        return ReviewCardRecord(
            id=int(row["id"]),
            material_id=int(row["material_id"]),
            user_id=str(row["user_id"]),
            material_title=str(row["material_title"]),
            document_type=str(row["document_type"]),
            question=str(row["question"]),
            answer=str(row["answer"]),
            hint=row.get("hint"),
            evidence_refs_json=json_text(row.get("evidence_refs"), "[]"),
            fsrs_card_json=str(row.get("fsrs_card_json") or "{}"),
            due_at=row["due_at"],
            retrievability=float(row.get("retrievability") or 0.0),
            review_count=int(row.get("review_count") or 0),
            lapse_count=int(row.get("lapse_count") or 0),
            active=bool(row.get("active")),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _to_settings(row: dict[str, Any]) -> ReviewSettingsRecord:
        """把 time 列转换为稳定 HH:MM 字符串。"""
        reminder = row.get("reminder_time")
        reminder_text = reminder.strftime("%H:%M") if hasattr(reminder, "strftime") else str(reminder or "09:00")[:5]
        return ReviewSettingsRecord(
            user_id=str(row["user_id"]),
            enabled=bool(row.get("enabled", True)),
            desired_retention=float(row.get("desired_retention") or 0.90),
            daily_limit=int(row.get("daily_limit") or 20),
            reminder_time=reminder_text,
            timezone=str(row.get("timezone") or "Asia/Shanghai"),
        )

    @staticmethod
    def _to_evidence(row: dict[str, Any], material_id: int) -> Evidence:
        """复用 RAG 时间跳转规则构造公开 evidence。"""
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        title = str(row.get("title") or metadata.get("title") or "未命名资料")
        section = str(metadata.get("sectionTitle") or row.get("section_name") or "全文")
        snippet = " ".join(str(row.get("text") or "").split())
        if len(snippet) > 600:
            snippet = snippet[:600].rstrip() + "..."
        metadata_view = build_evidence_metadata_view(metadata)
        metadata_view["previewUrl"] = f"/preview/material/{material_id}"
        return Evidence(
            evidenceId=str(row["chunk_id"]),
            documentId=str(row["document_id"]),
            documentTitle=title,
            blockId=as_optional_str(metadata.get("blockId")),
            blockType=as_optional_str(metadata.get("blockType")),
            pageIndex=as_optional_int(metadata.get("pageIndex")),
            slideIndex=as_optional_int(metadata.get("slideIndex")),
            startTime=as_optional_str(metadata.get("startTime")),
            endTime=as_optional_str(metadata.get("endTime")),
            sheetName=as_optional_str(metadata.get("sheetName")),
            cellRange=as_optional_str(metadata.get("cellRange")),
            sectionTitle=section,
            title=title,
            snippet=snippet,
            source=str(row.get("source") or "upload"),
            sourcePath=public_http_source(metadata.get("sourcePath")),
            assetPath=public_http_source(metadata.get("assetPath")),
            playbackUrl=build_playback_url(
                document_id=str(row["document_id"]),
                title=title,
                metadata=metadata,
            ),
            sectionName=section,
            documentType=str(row.get("document_type") or "document"),
            score=1.0,
            retrievalSource="summary",
            parseEngine=as_optional_str(metadata.get("parseEngine") or metadata.get("parser")),
            metadata=metadata_view,
        )


class ReviewRepository:
    """通过 psycopg 管理学习复习事务。"""

    def __init__(self, database_url: str | None = None, schema: str | None = None) -> None:
        self._database_url = database_url or resolve_database_url()
        self._schema = validate_schema(schema or os.getenv("RAG_DATABASE_SCHEMA", DEFAULT_SCHEMA))

    @contextmanager
    def transaction(self) -> Iterator[ReviewTransaction]:
        """打开一个自动提交或回滚的 PostgreSQL 事务。"""
        connection = self._connect()
        try:
            with connection:
                with connection.cursor() as cursor:
                    yield DatabaseReviewTransaction(cursor, self._schema)
        finally:
            connection.close()

    def _connect(self) -> Any:
        """延迟导入数据库驱动，测试依赖替换无需真实连接。"""
        if not self._database_url:
            raise RuntimeError("未配置 REVIEW_DATABASE_URL、RAG_DATABASE_URL 或 DATABASE_URL")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("复习仓储需要安装 psycopg[binary]") from exc
        return psycopg.connect(self._database_url, row_factory=dict_row)


def resolve_database_url() -> str:
    """按复习专用、RAG 复用、通用顺序读取连接串。"""
    return (
        os.getenv("REVIEW_DATABASE_URL", "").strip()
        or os.getenv("RAG_DATABASE_URL", "").strip()
        or os.getenv("DATABASE_URL", "").strip()
    )


def validate_schema(value: str) -> str:
    """只允许合法简单 PostgreSQL schema 名称。"""
    if not SCHEMA_PATTERN.fullmatch(value):
        raise RuntimeError("RAG_DATABASE_SCHEMA 必须是合法的 PostgreSQL schema 标识符")
    return value


def json_text(value: object, default: str) -> str:
    """把 JSONB 行值转换为 UTF-8 JSON 字符串。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
