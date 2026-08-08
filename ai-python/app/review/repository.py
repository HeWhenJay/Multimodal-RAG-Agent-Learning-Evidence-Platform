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
    f"model:{REVIEW_CARD_PROMPT_VERSION}",
    f"filter:{REVIEW_CARD_PROMPT_VERSION}",
)
# 保留公开测试和历史扩展使用的模型提取器标识；它不再参与版本失效判定。
CURRENT_REVIEW_MODEL_EXTRACTOR = CURRENT_REVIEW_EXTRACTORS[0]


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
    summary: str | None = None
    folder_id: int | None = None
    folder_name: str | None = None
    generation_attempts: int = 0
    quality_feedback: tuple[str, ...] = ()
    generation_progress: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReviewFolderRecord:
    """用户复习文件夹及其实时聚合统计。"""

    id: int
    user_id: str
    name: str
    material_count: int
    card_count: int
    due_card_count: int
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
    source_key: str = ""
    material_summary: str | None = None
    folder_id: int | None = None
    folder_name: str | None = None


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
    due_material_count: int = 0
    started_due_material_count: int = 0


class ReviewTransaction(Protocol):
    """复习服务在单个事务内需要的最小操作集合。"""

    def list_sync_candidates(self, user_id: str, limit: int) -> list[MaterialSourceRecord]: ...

    def find_material(self, material_id: int, user_id: str) -> MaterialSourceRecord | None: ...

    def find_material_by_id(self, material_id: int) -> MaterialSourceRecord | None: ...

    def list_evidences(self, material: MaterialSourceRecord, limit: int = 320) -> list[Evidence]: ...

    def list_active_cards_for_material(self, material_id: int, user_id: str) -> list[ReviewCardRecord]: ...

    def append_review_cards(
        self,
        material: MaterialSourceRecord,
        cards: list[ReviewCardDraft],
    ) -> list[ReviewCardRecord]: ...

    def replace_active_cards_for_material(
        self,
        material: MaterialSourceRecord,
        original_card_ids: list[int],
        cards: list[ReviewCardDraft],
        *,
        summary: str | None = None,
    ) -> list[ReviewCardRecord] | None: ...

    def save_generation(
        self,
        material: MaterialSourceRecord,
        *,
        is_learning_content: bool | None,
        category: str | None,
        summary: str | None,
        status: str,
        reason: str,
        extractor: str,
        cards: list[ReviewCardDraft],
        generation_attempts: int = 0,
        quality_feedback: list[str] | tuple[str, ...] = (),
        generation_progress_event: dict[str, Any] | None = None,
    ) -> ReviewMaterialRecord | None: ...

    def save_generation_progress(
        self,
        material: MaterialSourceRecord,
        event: dict[str, Any],
    ) -> ReviewMaterialRecord | None: ...

    def list_review_materials(self, user_id: str, limit: int = 100) -> list[ReviewMaterialRecord]: ...

    def list_review_folders(self, user_id: str, *, now: datetime) -> list[ReviewFolderRecord]: ...

    def find_review_folder(self, folder_id: int, user_id: str, *, now: datetime) -> ReviewFolderRecord | None: ...

    def review_folder_name_exists(self, user_id: str, name: str, *, exclude_folder_id: int | None = None) -> bool: ...

    def create_review_folder(self, user_id: str, name: str, *, now: datetime) -> ReviewFolderRecord: ...

    def rename_review_folder(
        self,
        folder_id: int,
        user_id: str,
        name: str,
        *,
        now: datetime,
    ) -> ReviewFolderRecord | None: ...

    def delete_review_folder(self, folder_id: int, user_id: str) -> int | None: ...

    def assign_review_materials_to_folder(
        self,
        user_id: str,
        material_ids: list[int],
        folder_id: int | None,
    ) -> list[int] | None: ...

    def reorder_review_folder_materials(
        self,
        folder_id: int,
        user_id: str,
        material_ids: list[int],
    ) -> list[int] | None: ...

    def list_review_materials_in_folder(
        self,
        folder_id: int,
        user_id: str,
        limit: int = 100,
    ) -> list[ReviewMaterialRecord]: ...

    def list_review_cards_in_folder(self, folder_id: int, user_id: str) -> list[ReviewCardRecord]: ...

    def list_all_active_cards(self, user_id: str) -> list[ReviewCardRecord]: ...

    def find_review_material(self, material_id: int, user_id: str) -> ReviewMaterialRecord | None: ...

    def is_material_excluded(self, material_id: int, user_id: str) -> bool: ...

    def exclude_material(self, material_id: int, user_id: str) -> bool: ...

    def exclude_card(self, card_id: int, user_id: str) -> int | None: ...

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

    def list_due_group_cards(
        self,
        user_id: str,
        *,
        now: datetime,
        today_start: datetime,
        tomorrow_start: datetime,
        limit: int,
    ) -> list[ReviewCardRecord]: ...

    def has_material_reviewed_today(
        self,
        material_id: int,
        user_id: str,
        *,
        today_start: datetime,
        tomorrow_start: datetime,
    ) -> bool: ...

    def reorder_review_materials(self, user_id: str, material_ids: list[int]) -> list[int] | None: ...

    def find_card(self, card_id: int, user_id: str) -> ReviewCardRecord | None: ...

    def find_card_for_update(self, card_id: int, user_id: str) -> ReviewCardRecord | None: ...

    def update_card_content(
        self,
        card_id: int,
        user_id: str,
        *,
        question: str,
        answer: str,
        hint: str | None,
        evidence_refs_json: str | None = None,
    ) -> ReviewCardRecord | None: ...

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
        """只扫描首次生成、资料新索引版本或中断超时的资料。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT lm.*
                FROM {schema}.learning_material lm
                LEFT JOIN {schema}.learning_review_material rm ON rm.material_id = lm.id
                WHERE lm.user_id = %s
                  AND lm.status IN ('READY', 'PARTIAL')
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {schema}.learning_review_material_exclusion excluded_material
                      WHERE excluded_material.material_id = lm.id
                        AND excluded_material.user_id = lm.user_id
                  )
                  AND (
                      rm.material_id IS NULL
                      OR rm.index_request_version < lm.index_request_version
                      OR (
                          rm.status = 'GENERATING'
                          AND rm.updated_at <= CURRENT_TIMESTAMP - INTERVAL '20 minutes'
                      )
                  )
                ORDER BY lm.updated_at ASC, lm.id ASC
                LIMIT %s
                """
            ),
            (user_id, limit),
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

    def find_material_by_id(self, material_id: int) -> MaterialSourceRecord | None:
        """按资料 ID 读取索引终态，仅供内部 worker 衔接复习生成。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT *
                FROM {schema}.learning_material
                WHERE id = %s
                """
            ),
            (material_id,),
        )
        row = self._cursor.fetchone()
        return self._to_material(row) if row else None

    def list_evidences(self, material: MaterialSourceRecord, limit: int = 320) -> list[Evidence]:
        """读取整份资料的原始 RAG 切块，由提炼器再做全局均匀抽样。"""
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
                    CASE
                        WHEN COALESCE(c.metadata ->> 'childKind', '') NOT IN ('summary', 'video_segment_summary', 'ocr_occurrence')
                             AND COALESCE(c.metadata ->> 'evidenceChannel', '') <> 'frame_ocr' THEN 0
                        WHEN COALESCE(c.metadata ->> 'evidenceChannel', '') = 'frame_ocr'
                             OR c.metadata ->> 'childKind' = 'ocr_occurrence' THEN 1
                        ELSE 2
                    END,
                    c.chunk_position ASC
                LIMIT %s
                """
            ),
            (document_id, material.user_id, limit),
        )
        return [self._to_evidence(row, material.id) for row in self._cursor.fetchall()]

    def list_active_cards_for_material(self, material_id: int, user_id: str) -> list[ReviewCardRecord]:
        """读取单份资料的全部活动卡片，作为补漏模型和服务端去重基线。"""
        self._cursor.execute(
            self._card_select(
                "WHERE c.material_id = %s AND c.user_id = %s AND lm.user_id = %s AND c.active = TRUE ORDER BY c.id ASC"
            ),
            (material_id, user_id, user_id),
        )
        return [self._to_card(row) for row in self._cursor.fetchall()]

    def replace_active_cards_for_material(
        self,
        material: MaterialSourceRecord,
        original_card_ids: list[int],
        cards: list[ReviewCardDraft],
        *,
        summary: str | None = None,
    ) -> list[ReviewCardRecord] | None:
        """在资料锁内停用旧卡片并原子插入用户确认的新卡片。"""
        if not cards or not original_card_ids:
            return None
        self._cursor.execute(
            self._statement(
                """
                SELECT lm.index_request_version,
                       rm.id AS review_material_id,
                       rm.status AS review_status
                FROM {schema}.learning_material lm
                JOIN {schema}.learning_review_material rm
                  ON rm.material_id = lm.id AND rm.user_id = lm.user_id
                WHERE lm.id = %s AND lm.user_id = %s
                FOR UPDATE OF lm, rm
                """
            ),
            (material.id, material.user_id),
        )
        current = self._cursor.fetchone()
        if current is None or int(current.get("index_request_version") or 0) != material.index_request_version:
            return None
        if str(current.get("review_status") or "") != "GENERATED":
            return None
        review_material_id = int(current["review_material_id"])
        self._cursor.execute(
            self._statement(
                """
                SELECT id, source_key
                FROM {schema}.learning_review_card
                WHERE material_id = %s AND user_id = %s AND active = TRUE
                ORDER BY id ASC
                FOR UPDATE
                """
            ),
            (material.id, material.user_id),
        )
        old_rows = self._cursor.fetchall()
        old_ids = [int(row["id"]) for row in old_rows]
        if old_ids != sorted(set(original_card_ids)):
            return None
        for row in old_rows:
            self._cursor.execute(
                self._statement(
                    """
                    INSERT INTO {schema}.learning_review_card_exclusion (
                        original_card_id, material_id, user_id, source_key
                    )
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                    """
                ),
                (int(row["id"]), material.id, material.user_id, str(row["source_key"])),
            )
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_card
                SET active = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE material_id = %s AND user_id = %s AND active = TRUE
                """
            ),
            (material.id, material.user_id),
        )
        inserted_ids: list[int] = []
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
                        question = EXCLUDED.question,
                        answer = EXCLUDED.answer,
                        hint = EXCLUDED.hint,
                        evidence_refs = EXCLUDED.evidence_refs,
                        active = TRUE,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id
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
            inserted = self._cursor.fetchone()
            if inserted is not None:
                inserted_ids.append(int(inserted["id"]))
        summary_clause = "" if summary is None else ", summary = %s"
        update_params: list[Any] = [material.id, material.user_id]
        if summary is not None:
            update_params.append(summary)
        update_params.extend([review_material_id, material.user_id])
        self._cursor.execute(
            self._statement(
                f"""
                UPDATE {{schema}}.learning_review_material
                SET card_count = (
                    SELECT COUNT(1)
                    FROM {{schema}}.learning_review_card c
                    WHERE c.material_id = %s AND c.user_id = %s AND c.active = TRUE
                ), updated_at = CURRENT_TIMESTAMP{summary_clause}
                WHERE id = %s AND user_id = %s
                """
            ),
            tuple(update_params),
        )
        if not inserted_ids:
            return []
        self._cursor.execute(
            self._card_select(
                "WHERE c.id = ANY(%s::BIGINT[]) AND c.user_id = %s AND lm.user_id = %s ORDER BY c.id ASC"
            ),
            (inserted_ids, material.user_id, material.user_id),
        )
        return [self._to_card(row) for row in self._cursor.fetchall()]

    def append_review_cards(
        self,
        material: MaterialSourceRecord,
        cards: list[ReviewCardDraft],
    ) -> list[ReviewCardRecord]:
        """只插入补漏卡片，绝不更新、停用或替换任何既有卡片状态。"""
        if not cards:
            return []
        self._cursor.execute(
            self._statement(
                """
                SELECT lm.index_request_version,
                       rm.id AS review_material_id,
                       rm.status AS review_status,
                       rm.extractor,
                       EXISTS (
                           SELECT 1
                           FROM {schema}.learning_review_material_exclusion excluded_material
                           WHERE excluded_material.material_id = lm.id
                             AND excluded_material.user_id = lm.user_id
                       ) AS review_excluded
                FROM {schema}.learning_material lm
                LEFT JOIN {schema}.learning_review_material rm
                  ON rm.material_id = lm.id AND rm.user_id = lm.user_id
                WHERE lm.id = %s AND lm.user_id = %s
                FOR UPDATE OF lm
                """
            ),
            (material.id, material.user_id),
        )
        current = self._cursor.fetchone()
        if current is None:
            raise RuntimeError("追加复习卡片时资料已不存在")
        if bool(current.get("review_excluded")):
            return []
        if int(current.get("index_request_version") or 0) != material.index_request_version:
            raise RuntimeError("追加复习卡片时资料索引版本已变化，请重新查找遗漏知识点")
        if current.get("review_status") != "GENERATED":
            raise RuntimeError("只有已成功生成的复习资料才能追加遗漏知识点")
        review_material_id = int(current["review_material_id"])
        self._cursor.execute(
            self._statement(
                """
                SELECT source_key
                FROM {schema}.learning_review_card_exclusion
                WHERE material_id = %s AND user_id = %s
                """
            ),
            (material.id, material.user_id),
        )
        excluded_source_keys = {str(row["source_key"]) for row in self._cursor.fetchall()}
        self._cursor.execute(
            self._statement(
                """
                SELECT source_key, question
                FROM {schema}.learning_review_card
                WHERE material_id = %s AND user_id = %s AND active = TRUE
                FOR UPDATE
                """
            ),
            (material.id, material.user_id),
        )
        existing_rows = self._cursor.fetchall()
        existing_source_keys = {str(row["source_key"]) for row in existing_rows}
        existing_question_keys = {normalized_question_key(str(row["question"])) for row in existing_rows}
        inserted_ids: list[int] = []
        for card in cards:
            question_key = normalized_question_key(card.question)
            if (
                card.source_key in excluded_source_keys
                or card.source_key in existing_source_keys
                or question_key in existing_question_keys
            ):
                continue
            self._cursor.execute(
                self._statement(
                    """
                    INSERT INTO {schema}.learning_review_card (
                        review_material_id, material_id, user_id, source_key,
                        question, answer, hint, evidence_refs, fsrs_card_json,
                        due_at, retrievability, review_count, lapse_count, active
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, 0, 0, 0, TRUE)
                    ON CONFLICT (material_id, source_key) DO NOTHING
                    RETURNING id
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
            inserted = self._cursor.fetchone()
            if inserted is not None:
                inserted_ids.append(int(inserted["id"]))
                existing_source_keys.add(card.source_key)
                existing_question_keys.add(question_key)
        if not inserted_ids:
            return []
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_material
                SET card_count = (
                    SELECT COUNT(1)
                    FROM {schema}.learning_review_card active_card
                    WHERE active_card.material_id = %s
                      AND active_card.user_id = %s
                      AND active_card.active = TRUE
                )
                WHERE id = %s AND user_id = %s
                """
            ),
            (material.id, material.user_id, review_material_id, material.user_id),
        )
        self._cursor.execute(
            self._card_select(
                "WHERE c.id = ANY(%s::BIGINT[]) AND c.user_id = %s AND lm.user_id = %s ORDER BY c.id ASC"
            ),
            (inserted_ids, material.user_id, material.user_id),
        )
        return [self._to_card(row) for row in self._cursor.fetchall()]

    def save_generation_progress(
        self,
        material: MaterialSourceRecord,
        event: dict[str, Any],
    ) -> ReviewMaterialRecord | None:
        """按资料行锁追加一条生成事件，并把当前状态标记为真实运行中。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT lm.index_request_version,
                       rm.generation_progress,
                       EXISTS (
                           SELECT 1
                           FROM {schema}.learning_review_material_exclusion excluded_material
                           WHERE excluded_material.material_id = lm.id
                             AND excluded_material.user_id = lm.user_id
                       ) AS review_excluded
                FROM {schema}.learning_material lm
                LEFT JOIN {schema}.learning_review_material rm ON rm.material_id = lm.id
                WHERE lm.id = %s AND lm.user_id = %s
                FOR UPDATE OF lm
                """
            ),
            (material.id, material.user_id),
        )
        current_material = self._cursor.fetchone()
        if current_material is None or bool(current_material.get("review_excluded")):
            return None
        if int(current_material.get("index_request_version") or 0) != material.index_request_version:
            return self._find_review_material(material.id, material.user_id)
        progress = merge_generation_progress(current_material.get("generation_progress"), event)
        attempt = max(0, int(event.get("attempt") or 0))
        message = " ".join(str(event.get("message") or "复习内容生成中").split()).strip()[:500]
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.learning_review_material (
                    material_id, user_id, index_request_version, status, reason,
                    card_count, generation_attempts, quality_feedback, generation_progress
                )
                VALUES (%s, %s, %s, 'GENERATING', %s, 0, %s, '[]'::jsonb, %s::jsonb)
                ON CONFLICT (material_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    index_request_version = EXCLUDED.index_request_version,
                    status = 'GENERATING',
                    reason = EXCLUDED.reason,
                    generation_attempts = EXCLUDED.generation_attempts,
                    generation_progress = EXCLUDED.generation_progress,
                    updated_at = CURRENT_TIMESTAMP
                WHERE {schema}.learning_review_material.index_request_version <= EXCLUDED.index_request_version
                RETURNING id
                """
            ),
            (
                material.id,
                material.user_id,
                material.index_request_version,
                message,
                attempt,
                json.dumps(progress, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        if self._cursor.fetchone() is None:
            return self._find_review_material(material.id, material.user_id)
        return self._find_review_material(material.id, material.user_id)

    def save_generation(
        self,
        material: MaterialSourceRecord,
        *,
        is_learning_content: bool | None,
        category: str | None,
        summary: str | None,
        status: str,
        reason: str,
        extractor: str,
        cards: list[ReviewCardDraft],
        generation_attempts: int = 0,
        quality_feedback: list[str] | tuple[str, ...] = (),
        generation_progress_event: dict[str, Any] | None = None,
    ) -> ReviewMaterialRecord | None:
        """更新分类并按稳定来源键刷新正文，已有卡片不重置 FSRS 状态。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT lm.index_request_version,
                       rm.generation_progress,
                       EXISTS (
                           SELECT 1
                           FROM {schema}.learning_review_material_exclusion excluded_material
                           WHERE excluded_material.material_id = lm.id
                             AND excluded_material.user_id = lm.user_id
                       ) AS review_excluded
                FROM {schema}.learning_material lm
                LEFT JOIN {schema}.learning_review_material rm ON rm.material_id = lm.id
                WHERE lm.id = %s AND lm.user_id = %s
                FOR UPDATE OF lm
                """
            ),
            (material.id, material.user_id),
        )
        current_material = self._cursor.fetchone()
        if current_material is None:
            raise RuntimeError("保存复习卡片时资料已不存在")
        if bool(current_material.get("review_excluded")):
            return None
        if int(current_material.get("index_request_version") or 0) != material.index_request_version:
            # 提炼期间资料已进入新索引版本时，丢弃旧结果并等待下一轮同步。
            current = self._find_review_material(material.id, material.user_id)
            if current is not None:
                return current
            raise RuntimeError("保存复习卡片时资料索引版本已变化")
        generation_progress = merge_generation_progress(
            current_material.get("generation_progress"),
            generation_progress_event,
        )
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.learning_review_material (
                    material_id, user_id, index_request_version, is_learning_content,
                    category, summary, status, reason, extractor, card_count, generated_at
                    , generation_attempts, quality_feedback, generation_progress
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 0, CURRENT_TIMESTAMP, %s, %s::jsonb, %s::jsonb)
                ON CONFLICT (material_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    index_request_version = EXCLUDED.index_request_version,
                    is_learning_content = EXCLUDED.is_learning_content,
                    category = EXCLUDED.category,
                    summary = EXCLUDED.summary,
                    status = EXCLUDED.status,
                    reason = EXCLUDED.reason,
                    extractor = EXCLUDED.extractor,
                    generation_attempts = EXCLUDED.generation_attempts,
                    quality_feedback = EXCLUDED.quality_feedback,
                    generation_progress = EXCLUDED.generation_progress,
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
                summary,
                status,
                reason,
                extractor,
                max(0, int(generation_attempts)),
                json.dumps(list(quality_feedback), ensure_ascii=False, separators=(",", ":")),
                json.dumps(generation_progress, ensure_ascii=False, separators=(",", ":")),
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
                SELECT source_key
                FROM {schema}.learning_review_card_exclusion
                WHERE material_id = %s AND user_id = %s
                """
            ),
            (material.id, material.user_id),
        )
        excluded_source_keys = {str(row["source_key"]) for row in self._cursor.fetchall()}
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_card
                SET active = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE material_id = %s AND user_id = %s
                  AND source_key NOT LIKE 'manual:%%'
                  AND source_key NOT LIKE 'custom:%%'
                """
            ),
            (material.id, material.user_id),
        )
        for card in cards:
            if card.source_key in excluded_source_keys:
                continue
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
        """先读取未归档资料，再按资料 ID 批量读取复习状态，避免资料列表多表联查。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT lm.id AS material_id,
                       lm.title,
                       lm.document_type,
                       lm.status AS material_status,
                       lm.index_request_version,
                       lm.updated_at AS material_updated_at
                FROM {schema}.learning_material lm
                WHERE lm.user_id = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {schema}.learning_review_material_exclusion excluded_material
                      WHERE excluded_material.material_id = lm.id
                        AND excluded_material.user_id = lm.user_id
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {schema}.learning_review_folder_material folder_material
                      WHERE folder_material.material_id = lm.id
                        AND folder_material.user_id = lm.user_id
                  )
                ORDER BY lm.updated_at DESC, lm.id DESC
                LIMIT %s
                """
            ),
            (user_id, limit),
        )
        base_rows = self._cursor.fetchall()
        if not base_rows:
            return []
        material_ids = [int(row["material_id"]) for row in base_rows]
        review_rows = self._load_review_material_states(user_id, material_ids)
        return self._merge_review_material_rows(base_rows, review_rows)

    def list_review_folders(self, user_id: str, *, now: datetime) -> list[ReviewFolderRecord]:
        """按最近更新顺序读取当前用户文件夹及实时卡片统计。"""
        self._cursor.execute(
            self._folder_select(
                "WHERE folder.user_id = %s",
                "ORDER BY folder.updated_at DESC, folder.id DESC",
            ),
            (now, user_id),
        )
        return [self._to_folder(row) for row in self._cursor.fetchall()]

    def find_review_folder(self, folder_id: int, user_id: str, *, now: datetime) -> ReviewFolderRecord | None:
        """按文件夹和认证用户读取统计，隐藏其他用户同 ID 文件夹。"""
        self._cursor.execute(
            self._folder_select("WHERE folder.id = %s AND folder.user_id = %s"),
            (now, folder_id, user_id),
        )
        row = self._cursor.fetchone()
        return self._to_folder(row) if row else None

    def review_folder_name_exists(
        self,
        user_id: str,
        name: str,
        *,
        exclude_folder_id: int | None = None,
    ) -> bool:
        """以不区分大小写的方式拒绝同一用户的同名文件夹。"""
        query = """
            SELECT 1
            FROM {schema}.learning_review_folder
            WHERE user_id = %s AND LOWER(name) = LOWER(%s)
        """
        params: list[Any] = [user_id, name]
        if exclude_folder_id is not None:
            query += " AND id <> %s"
            params.append(exclude_folder_id)
        self._cursor.execute(self._statement(query), tuple(params))
        return self._cursor.fetchone() is not None

    def create_review_folder(self, user_id: str, name: str, *, now: datetime) -> ReviewFolderRecord:
        """创建一个空文件夹并返回零统计快照。"""
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.learning_review_folder (user_id, name)
                VALUES (%s, %s)
                RETURNING id
                """
            ),
            (user_id, name),
        )
        folder_id = int(self._cursor.fetchone()["id"])
        record = self.find_review_folder(folder_id, user_id, now=now)
        if record is None:
            raise RuntimeError("创建复习文件夹后无法读取结果")
        return record

    def rename_review_folder(
        self,
        folder_id: int,
        user_id: str,
        name: str,
        *,
        now: datetime,
    ) -> ReviewFolderRecord | None:
        """只允许当前用户重命名文件夹。"""
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_folder
                SET name = %s, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND user_id = %s
                """
            ),
            (name, folder_id, user_id),
        )
        if self._cursor.rowcount != 1:
            return None
        return self.find_review_folder(folder_id, user_id, now=now)

    def delete_review_folder(self, folder_id: int, user_id: str) -> int | None:
        """删除文件夹并依靠级联解除归档，返回受影响文档数。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT id
                FROM {schema}.learning_review_folder
                WHERE id = %s AND user_id = %s
                FOR UPDATE
                """
            ),
            (folder_id, user_id),
        )
        if self._cursor.fetchone() is None:
            return None
        self._cursor.execute(
            self._statement(
                """
                SELECT COUNT(material_id) AS material_count
                FROM {schema}.learning_review_folder_material
                WHERE folder_id = %s AND user_id = %s
                """
            ),
            (folder_id, user_id),
        )
        material_count = int((self._cursor.fetchone() or {}).get("material_count") or 0)
        self._cursor.execute(
            self._statement("DELETE FROM {schema}.learning_review_folder WHERE id = %s AND user_id = %s"),
            (folder_id, user_id),
        )
        return material_count

    def assign_review_materials_to_folder(
        self,
        user_id: str,
        material_ids: list[int],
        folder_id: int | None,
    ) -> list[int] | None:
        """锁定全部资料后原子更新文件夹归属，任一越权或排除资料都会失败。"""
        if folder_id is not None:
            self._cursor.execute(
                self._statement(
                    """
                    SELECT id
                    FROM {schema}.learning_review_folder
                    WHERE id = %s AND user_id = %s
                    FOR UPDATE
                    """
                ),
                (folder_id, user_id),
            )
            if self._cursor.fetchone() is None:
                return None
        self._cursor.execute(
            self._statement(
                """
                SELECT material.id
                FROM {schema}.learning_material material
                WHERE material.user_id = %s
                  AND material.id = ANY(%s::BIGINT[])
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {schema}.learning_review_material_exclusion excluded_material
                      WHERE excluded_material.material_id = material.id
                        AND excluded_material.user_id = material.user_id
                  )
                ORDER BY material.id
                FOR UPDATE OF material
                """
            ),
            (user_id, material_ids),
        )
        owned_ids = [int(row["id"]) for row in self._cursor.fetchall()]
        if set(owned_ids) != set(material_ids):
            return None

        self._cursor.execute(
            self._statement(
                """
                DELETE FROM {schema}.learning_review_folder_material
                WHERE user_id = %s AND material_id = ANY(%s::BIGINT[])
                """
            ),
            (user_id, material_ids),
        )
        if folder_id is not None:
            self._cursor.execute(
                self._statement(
                    """
                    INSERT INTO {schema}.learning_review_folder_material (
                        material_id, folder_id, user_id, display_order
                    )
                    SELECT selected.material_id, %s, %s,
                           next_position.position + selected.ordinality - 1
                    FROM UNNEST(%s::BIGINT[]) WITH ORDINALITY AS selected(material_id, ordinality)
                    CROSS JOIN (
                        SELECT COALESCE(MAX(display_order), -1) AS position
                        FROM {schema}.learning_review_folder_material
                        WHERE folder_id = %s AND user_id = %s
                    ) next_position
                    """
                ),
                (folder_id, user_id, material_ids, folder_id, user_id),
            )
            self._cursor.execute(
                self._statement(
                    """
                    UPDATE {schema}.learning_review_folder
                    SET updated_at = CURRENT_TIMESTAMP
                    WHERE id = %s AND user_id = %s
                    """
                ),
                (folder_id, user_id),
            )
        return list(material_ids)

    def reorder_review_folder_materials(
        self,
        folder_id: int,
        user_id: str,
        material_ids: list[int],
    ) -> list[int] | None:
        """锁定文件夹内归属后原子保存文档顺序，任一越权资料都会失败。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT id
                FROM {schema}.learning_review_folder
                WHERE id = %s AND user_id = %s
                FOR UPDATE
                """
            ),
            (folder_id, user_id),
        )
        if self._cursor.fetchone() is None:
            return None
        self._cursor.execute(
            self._statement(
                """
                SELECT folder_material.material_id
                FROM {schema}.learning_review_folder_material folder_material
                WHERE folder_material.folder_id = %s
                  AND folder_material.user_id = %s
                ORDER BY folder_material.display_order ASC NULLS LAST,
                         folder_material.material_id ASC
                FOR UPDATE
                """
            ),
            (folder_id, user_id),
        )
        stable_ids = [int(row["material_id"]) for row in self._cursor.fetchall()]
        if not set(material_ids).issubset(set(stable_ids)):
            return None
        requested_set = set(material_ids)
        complete_order = [
            *material_ids,
            *(material_id for material_id in stable_ids if material_id not in requested_set),
        ]
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_folder_material target
                SET display_order = ordered.position,
                    updated_at = CURRENT_TIMESTAMP
                FROM (
                    SELECT material_id, CAST(ordinality - 1 AS INTEGER) AS position
                    FROM UNNEST(%s::BIGINT[]) WITH ORDINALITY AS values_with_order(material_id, ordinality)
                ) ordered
                WHERE target.material_id = ordered.material_id
                  AND target.folder_id = %s
                  AND target.user_id = %s
                  AND target.display_order IS DISTINCT FROM ordered.position
                """
            ),
            (complete_order, folder_id, user_id),
        )
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_folder
                SET updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND user_id = %s
                """
            ),
            (folder_id, user_id),
        )
        return list(complete_order)

    def list_review_materials_in_folder(
        self,
        folder_id: int,
        user_id: str,
        limit: int = 100,
    ) -> list[ReviewMaterialRecord]:
        """先读取文件夹关联顺序，再按资料 ID 批量读取资料与复习状态。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT material_id, display_order
                FROM {schema}.learning_review_folder_material folder_material
                WHERE folder_material.folder_id = %s AND folder_material.user_id = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {schema}.learning_review_material_exclusion excluded_material
                      WHERE excluded_material.material_id = folder_material.material_id
                        AND excluded_material.user_id = folder_material.user_id
                  )
                ORDER BY folder_material.display_order ASC NULLS LAST, folder_material.material_id ASC
                LIMIT %s
                """
            ),
            (folder_id, user_id, limit),
        )
        folder_rows = self._cursor.fetchall()
        if not folder_rows:
            return []
        material_ids = [int(row["material_id"]) for row in folder_rows]
        self._cursor.execute(
            self._statement(
                """
                SELECT lm.id AS material_id,
                       lm.title,
                       lm.document_type,
                       lm.status AS material_status,
                       lm.index_request_version,
                       lm.updated_at AS material_updated_at
                FROM {schema}.learning_material lm
                WHERE lm.user_id = %s
                  AND lm.id = ANY(%s::BIGINT[])
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {schema}.learning_review_material_exclusion excluded_material
                      WHERE excluded_material.material_id = lm.id
                        AND excluded_material.user_id = lm.user_id
                  )
                """
            ),
            (user_id, material_ids),
        )
        base_rows = self._cursor.fetchall()
        if not base_rows:
            return []
        review_rows = self._load_review_material_states(user_id, material_ids)
        folder_order = {int(row["material_id"]): index for index, row in enumerate(folder_rows)}
        merged = self._merge_review_material_rows(
            base_rows,
            review_rows,
            folder_id=folder_id,
        )
        return sorted(merged, key=lambda item: folder_order.get(item.material_id, len(folder_order)))[:limit]

    def list_review_cards_in_folder(self, folder_id: int, user_id: str) -> list[ReviewCardRecord]:
        """读取文件夹内全部活动卡片，不应用今日到期或每日额度过滤。"""
        self._cursor.execute(
            self._card_select(
                """
                JOIN {schema}.learning_review_folder_material review_folder_material
                  ON review_folder_material.material_id = c.material_id
                 AND review_folder_material.user_id = c.user_id
                WHERE review_folder_material.folder_id = %s
                  AND c.user_id = %s
                  AND lm.user_id = %s
                  AND c.active = TRUE
                ORDER BY review_folder_material.display_order ASC NULLS LAST, c.material_id ASC, c.id ASC
                """
            ),
            (folder_id, user_id, user_id),
        )
        return [self._to_card(row) for row in self._cursor.fetchall()]

    def list_all_active_cards(self, user_id: str) -> list[ReviewCardRecord]:
        """读取当前用户所有活动卡片，不应用到期、文件夹或已复习过滤。"""
        self._cursor.execute(
            self._card_select(
                """
                WHERE c.user_id = %s
                  AND lm.user_id = %s
                  AND c.active = TRUE
                ORDER BY folder_material.folder_id ASC NULLS FIRST,
                         c.material_id ASC,
                         c.id ASC
                """
            ),
            (user_id, user_id),
        )
        return [self._to_card(row) for row in self._cursor.fetchall()]

    def find_review_material(self, material_id: int, user_id: str) -> ReviewMaterialRecord | None:
        """按资料和用户读取当前复习生成状态，用于生成前幂等复核。"""
        return self._find_review_material(material_id, user_id)

    def is_material_excluded(self, material_id: int, user_id: str) -> bool:
        """读取资料级 tombstone，自动同步和显式生成都以此为准。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT 1
                FROM {schema}.learning_review_material_exclusion
                WHERE material_id = %s AND user_id = %s
                """
            ),
            (material_id, user_id),
        )
        return self._cursor.fetchone() is not None

    def exclude_material(self, material_id: int, user_id: str) -> bool:
        """锁定资料后写入排除记录，并停用该资料全部复习卡片。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT id
                FROM {schema}.learning_material
                WHERE id = %s AND user_id = %s
                FOR UPDATE
                """
            ),
            (material_id, user_id),
        )
        if self._cursor.fetchone() is None:
            return False
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.learning_review_material_exclusion (material_id, user_id)
                VALUES (%s, %s)
                ON CONFLICT (material_id) DO NOTHING
                """
            ),
            (material_id, user_id),
        )
        self._cursor.execute(
            self._statement(
                """
                DELETE FROM {schema}.learning_review_folder_material
                WHERE material_id = %s AND user_id = %s
                """
            ),
            (material_id, user_id),
        )
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_card
                SET active = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE material_id = %s AND user_id = %s AND active = TRUE
                """
            ),
            (material_id, user_id),
        )
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_material
                SET card_count = 0, updated_at = CURRENT_TIMESTAMP
                WHERE material_id = %s AND user_id = %s
                """
            ),
            (material_id, user_id),
        )
        return True

    def exclude_card(self, card_id: int, user_id: str) -> int | None:
        """幂等停用一张卡片，并保存来源键避免重新生成后复活。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT material_id
                FROM {schema}.learning_review_card_exclusion
                WHERE original_card_id = %s AND user_id = %s
                """
            ),
            (card_id, user_id),
        )
        excluded = self._cursor.fetchone()
        if excluded is not None:
            return int(excluded["material_id"])
        self._cursor.execute(
            self._statement(
                """
                SELECT c.material_id, c.source_key, c.review_material_id
                FROM {schema}.learning_review_card c
                JOIN {schema}.learning_material lm ON lm.id = c.material_id
                WHERE c.id = %s AND c.user_id = %s AND lm.user_id = %s
                FOR UPDATE OF c
                """
            ),
            (card_id, user_id, user_id),
        )
        card = self._cursor.fetchone()
        if card is None:
            return None
        material_id = int(card["material_id"])
        self._cursor.execute(
            self._statement(
                """
                INSERT INTO {schema}.learning_review_card_exclusion (
                    original_card_id, material_id, user_id, source_key
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """
            ),
            (card_id, material_id, user_id, str(card["source_key"])),
        )
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_card
                SET active = FALSE, updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND user_id = %s
                """
            ),
            (card_id, user_id),
        )
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_material
                SET card_count = (
                    SELECT COUNT(1)
                    FROM {schema}.learning_review_card active_card
                    WHERE active_card.material_id = %s
                      AND active_card.user_id = %s
                      AND active_card.active = TRUE
                ), updated_at = CURRENT_TIMESTAMP
                WHERE id = %s AND user_id = %s
                """
            ),
            (material_id, user_id, int(card["review_material_id"]), user_id),
        )
        return material_id

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
        """只统计主页面未归档资料，文件夹内容由文件夹详情独立展示。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT
                    COUNT(1) FILTER (WHERE c.active = TRUE) AS total_card_count,
                    COUNT(DISTINCT c.material_id) FILTER (WHERE c.active = TRUE) AS active_material_count,
                    COUNT(1) FILTER (WHERE c.active = TRUE AND c.due_at <= %s) AS due_count,
                    COUNT(DISTINCT c.material_id) FILTER (WHERE c.active = TRUE AND c.due_at <= %s) AS due_material_count,
                    MIN(c.due_at) FILTER (WHERE c.active = TRUE AND c.due_at > %s) AS next_due_at
                FROM {schema}.learning_review_card c
                JOIN {schema}.learning_material lm
                  ON lm.id = c.material_id
                 AND lm.user_id = c.user_id
                JOIN {schema}.learning_review_material rm
                  ON rm.material_id = c.material_id
                 AND rm.user_id = c.user_id
                 AND rm.status = 'GENERATED'
                 AND rm.index_request_version = lm.index_request_version
                WHERE c.user_id = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {schema}.learning_review_folder_material folder_material
                      WHERE folder_material.material_id = c.material_id
                        AND folder_material.user_id = c.user_id
                  )
                """
            ),
            (now, now, now, user_id),
        )
        card_stats = self._cursor.fetchone() or {}
        self._cursor.execute(
            self._statement(
                """
                SELECT
                    COUNT(DISTINCT log.material_id) AS reviewed_count,
                    COUNT(DISTINCT log.material_id) FILTER (
                        WHERE EXISTS (
                            SELECT 1
                            FROM {schema}.learning_review_card due_card
                            JOIN {schema}.learning_material due_source
                              ON due_source.id = due_card.material_id
                             AND due_source.user_id = due_card.user_id
                            JOIN {schema}.learning_review_material due_material
                              ON due_material.material_id = due_card.material_id
                             AND due_material.user_id = due_card.user_id
                             AND due_material.status = 'GENERATED'
                             AND due_material.index_request_version = due_source.index_request_version
                            WHERE due_card.material_id = log.material_id
                              AND due_card.user_id = log.user_id
                              AND due_card.active = TRUE
                              AND due_card.due_at <= %s
                              AND NOT EXISTS (
                                  SELECT 1
                                  FROM {schema}.learning_review_folder_material folder_material
                                  WHERE folder_material.material_id = due_card.material_id
                                    AND folder_material.user_id = due_card.user_id
                              )
                        )
                    ) AS started_due_material_count
                FROM {schema}.learning_review_log log
                WHERE log.user_id = %s AND log.reviewed_at >= %s AND log.reviewed_at < %s
                """
            ),
            (now, user_id, today_start, tomorrow_start),
        )
        log_stats = self._cursor.fetchone() or {}
        return ReviewOverviewStats(
            due_count=int(card_stats.get("due_count") or 0),
            today_reviewed_count=int(log_stats.get("reviewed_count") or 0),
            total_card_count=int(card_stats.get("total_card_count") or 0),
            active_material_count=int(card_stats.get("active_material_count") or 0),
            next_due_at=card_stats.get("next_due_at"),
            due_material_count=int(card_stats.get("due_material_count") or 0),
            started_due_material_count=int(log_stats.get("started_due_material_count") or 0),
        )

    def list_due_cards(self, user_id: str, *, now: datetime, limit: int) -> list[ReviewCardRecord]:
        """保留未归档资料的卡片级查询；公开队列使用文档级分组查询。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT c.*, lm.title AS material_title, lm.document_type,
                       rm.summary AS material_summary,
                       NULL::BIGINT AS folder_id,
                       NULL::VARCHAR AS folder_name,
                       MIN(c.due_at) OVER (PARTITION BY c.material_id) AS group_due_at
                FROM {schema}.learning_review_card c
                JOIN {schema}.learning_material lm ON lm.id = c.material_id
                JOIN {schema}.learning_review_material rm
                  ON rm.material_id = c.material_id
                 AND rm.user_id = c.user_id
                 AND rm.status = 'GENERATED'
                 AND rm.index_request_version = lm.index_request_version
                WHERE c.user_id = %s
                  AND lm.user_id = %s
                  AND c.active = TRUE
                  AND c.due_at <= %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {schema}.learning_review_folder_material folder_material
                      WHERE folder_material.material_id = c.material_id
                        AND folder_material.user_id = c.user_id
                  )
                ORDER BY group_due_at ASC, c.material_id ASC, c.due_at ASC, c.id ASC
                LIMIT %s
                """
            ),
            (user_id, user_id, now, limit),
        )
        return [self._to_card(row) for row in self._cursor.fetchall()]

    def list_due_group_cards(
        self,
        user_id: str,
        *,
        now: datetime,
        today_start: datetime,
        tomorrow_start: datetime,
        limit: int,
    ) -> list[ReviewCardRecord]:
        """先从未归档资料中选择文档组，再返回每组全部到期卡片。"""
        self._cursor.execute(
            self._statement(
                """
                WITH reviewed_materials AS (
                    SELECT DISTINCT material_id
                    FROM {schema}.learning_review_log
                    WHERE user_id = %s
                      AND reviewed_at >= %s
                      AND reviewed_at < %s
                ),
                due_cards AS (
                    SELECT c.*, lm.title AS material_title, lm.document_type,
                           rm.summary AS material_summary,
                           rm.display_order AS material_display_order,
                           NULL::BIGINT AS folder_id,
                           NULL::VARCHAR AS folder_name,
                           EXISTS (
                               SELECT 1
                               FROM reviewed_materials
                               WHERE reviewed_materials.material_id = c.material_id
                           ) AS started_today,
                           MIN(c.due_at) OVER (PARTITION BY c.material_id) AS group_due_at
                    FROM {schema}.learning_review_card c
                    JOIN {schema}.learning_material lm ON lm.id = c.material_id
                    JOIN {schema}.learning_review_material rm
                      ON rm.material_id = c.material_id
                     AND rm.user_id = c.user_id
                     AND rm.status = 'GENERATED'
                     AND rm.index_request_version = lm.index_request_version
                    WHERE c.user_id = %s
                      AND lm.user_id = %s
                      AND c.active = TRUE
                      AND c.due_at <= %s
                      AND NOT EXISTS (
                          SELECT 1
                          FROM {schema}.learning_review_folder_material folder_material
                          WHERE folder_material.material_id = c.material_id
                            AND folder_material.user_id = c.user_id
                      )
                ),
                due_materials AS (
                    SELECT material_id,
                           BOOL_OR(started_today) AS started_today,
                           MIN(material_display_order) AS material_display_order,
                           MIN(group_due_at) AS group_due_at
                    FROM due_cards
                    GROUP BY material_id
                ),
                started_count AS (
                    SELECT COUNT(*) AS value
                    FROM due_materials
                    WHERE started_today = TRUE
                ),
                new_candidates AS (
                    SELECT material_id,
                           ROW_NUMBER() OVER (
                               ORDER BY material_display_order ASC NULLS LAST,
                                        group_due_at ASC,
                                        material_id ASC
                           ) AS new_rank
                    FROM due_materials
                    WHERE started_today = FALSE
                ),
                selected_materials AS (
                    SELECT material_id
                    FROM due_materials
                    WHERE started_today = TRUE
                    UNION ALL
                    SELECT new_candidates.material_id
                    FROM new_candidates
                    CROSS JOIN started_count
                    WHERE new_candidates.new_rank <= GREATEST(0, %s - started_count.value)
                )
                SELECT due_cards.*
                FROM due_cards
                JOIN selected_materials
                    ON selected_materials.material_id = due_cards.material_id
                ORDER BY due_cards.material_display_order ASC NULLS LAST,
                         due_cards.group_due_at ASC,
                         due_cards.material_id ASC,
                         due_cards.due_at ASC,
                         due_cards.id ASC
                """
            ),
            (user_id, today_start, tomorrow_start, user_id, user_id, now, limit),
        )
        return [self._to_card(row) for row in self._cursor.fetchall()]

    def has_material_reviewed_today(
        self,
        material_id: int,
        user_id: str,
        *,
        today_start: datetime,
        tomorrow_start: datetime,
    ) -> bool:
        """判断当前资料是否已在今天占用过一个文档额度。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM {schema}.learning_review_log
                    WHERE material_id = %s
                      AND user_id = %s
                      AND reviewed_at >= %s
                      AND reviewed_at < %s
                ) AS reviewed_today
                """
            ),
            (material_id, user_id, today_start, tomorrow_start),
        )
        row = self._cursor.fetchone() or {}
        return bool(row.get("reviewed_today"))

    def reorder_review_materials(self, user_id: str, material_ids: list[int]) -> list[int] | None:
        """锁定主页面未归档资料并以单次集合更新保存稳定的拖拽顺序。"""
        # 用户设置行作为轻量级用户锁，使同一用户的并发排序按请求完成顺序串行化。
        self.get_or_create_settings(user_id, for_update=True)
        self._cursor.execute(
            self._statement(
                """
                SELECT rm.material_id
                FROM {schema}.learning_review_material rm
                JOIN {schema}.learning_material lm ON lm.id = rm.material_id
                WHERE rm.user_id = %s
                  AND lm.user_id = %s
                  AND rm.is_learning_content IS TRUE
                  AND rm.status = 'GENERATED'
                  AND EXISTS (
                      SELECT 1
                      FROM {schema}.learning_review_card active_card
                      WHERE active_card.material_id = rm.material_id
                        AND active_card.user_id = %s
                        AND active_card.active = TRUE
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {schema}.learning_review_material_exclusion excluded_material
                      WHERE excluded_material.material_id = rm.material_id
                        AND excluded_material.user_id = %s
                  )
                  AND NOT EXISTS (
                      SELECT 1
                      FROM {schema}.learning_review_folder_material folder_material
                      WHERE folder_material.material_id = rm.material_id
                        AND folder_material.user_id = rm.user_id
                  )
                ORDER BY rm.display_order ASC NULLS LAST, rm.material_id ASC
                FOR UPDATE OF rm
                """
            ),
            (user_id, user_id, user_id, user_id),
        )
        stable_ids = [int(row["material_id"]) for row in self._cursor.fetchall()]
        requested_set = set(material_ids)
        if not requested_set.issubset(stable_ids):
            return None

        # 本次可见资料前置，未参与拖拽的资料沿用事务开始时的相对顺序。
        complete_order = [*material_ids, *(item for item in stable_ids if item not in requested_set)]
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_material rm
                SET display_order = ordered.position,
                    updated_at = CURRENT_TIMESTAMP
                FROM (
                    SELECT material_id, CAST(ordinality - 1 AS INTEGER) AS position
                    FROM UNNEST(%s::BIGINT[]) WITH ORDINALITY AS values_with_order(material_id, ordinality)
                ) ordered
                WHERE rm.material_id = ordered.material_id
                  AND rm.user_id = %s
                  AND rm.display_order IS DISTINCT FROM ordered.position
                """
            ),
            (complete_order, user_id),
        )
        return list(material_ids)

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

    def update_card_content(
        self,
        card_id: int,
        user_id: str,
        *,
        question: str,
        answer: str,
        hint: str | None,
        evidence_refs_json: str | None = None,
    ) -> ReviewCardRecord | None:
        """更新用户卡片正文并保留 FSRS 状态；模型卡转为用户自定义来源键。"""
        self._cursor.execute(
            self._statement(
                """
                UPDATE {schema}.learning_review_card c
                SET question = %s,
                    answer = %s,
                    hint = %s,
                    evidence_refs = COALESCE(%s::jsonb, c.evidence_refs),
                    source_key = CASE
                        WHEN c.source_key LIKE 'manual:%%' OR c.source_key LIKE 'custom:%%' THEN c.source_key
                        ELSE 'custom:' || c.id::TEXT
                    END,
                    updated_at = CURRENT_TIMESTAMP
                FROM {schema}.learning_material lm
                WHERE c.id = %s
                  AND c.user_id = %s
                  AND c.active = TRUE
                  AND lm.id = c.material_id
                  AND lm.user_id = %s
                """
            ),
            (question, answer, hint, evidence_refs_json, card_id, user_id, user_id),
        )
        if self._cursor.rowcount != 1:
            return None
        return self.find_card(card_id, user_id)

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

    def _load_review_material_states(self, user_id: str, material_ids: list[int]) -> list[dict[str, Any]]:
        """按资料 ID 批量读取复习状态，避免把资料基础表和状态表联表扫描。"""
        self._cursor.execute(
            self._statement(
                """
                SELECT material_id,
                       index_request_version AS synced_index_request_version,
                       is_learning_content,
                       category,
                       status AS review_status,
                       reason,
                       card_count,
                       extractor,
                       summary AS material_summary,
                       generation_attempts,
                       quality_feedback,
                       generation_progress,
                       updated_at AS review_updated_at
                FROM {schema}.learning_review_material
                WHERE user_id = %s
                  AND material_id = ANY(%s::BIGINT[])
                """
            ),
            (user_id, material_ids),
        )
        return list(self._cursor.fetchall())

    def _merge_review_material_rows(
        self,
        base_rows: list[dict[str, Any]],
        review_rows: list[dict[str, Any]],
        *,
        folder_id: int | None = None,
    ) -> list[ReviewMaterialRecord]:
        """在后端合并资料基础信息与复习状态，保持旧接口的版本语义。"""
        review_by_material = {int(row["material_id"]): row for row in review_rows}
        merged_rows: list[dict[str, Any]] = []
        for base in base_rows:
            material_id = int(base["material_id"])
            review = review_by_material.get(material_id)
            current_version = int(base.get("index_request_version") or 0)
            synced_version = int(review["synced_index_request_version"]) if review and review.get("synced_index_request_version") is not None else None
            is_current = review is not None and synced_version == current_version
            merged_rows.append(
                {
                    "material_id": material_id,
                    "title": base["title"],
                    "document_type": base["document_type"],
                    "material_status": base["material_status"],
                    "index_request_version": current_version,
                    "synced_index_request_version": synced_version,
                    "is_learning_content": review.get("is_learning_content") if is_current and review else None,
                    "category": review.get("category") if is_current and review else None,
                    "review_status": str(review.get("review_status") or "PENDING") if is_current and review else "PENDING",
                    "reason": (
                        review.get("reason") if is_current and review else
                        "资料内容已更新，等待生成当前索引版本的复习卡片" if review else None
                    ),
                    "card_count": int(review.get("card_count") or 0) if is_current and review and review.get("review_status") == "GENERATED" else 0,
                    "extractor": review.get("extractor") if review else None,
                    "material_summary": review.get("material_summary") if is_current and review else None,
                    "folder_id": folder_id,
                    "folder_name": None,
                    "generation_attempts": int(review.get("generation_attempts") or 0) if is_current and review else 0,
                    "quality_feedback": review.get("quality_feedback") if is_current and review else [],
                    "generation_progress": review.get("generation_progress") if is_current and review else {},
                    "review_updated_at": review.get("review_updated_at") if review else base.get("material_updated_at"),
                }
            )
        return [self._to_review_material(row) for row in merged_rows]

    def _review_material_select(self, suffix: str) -> Any:
        """构造资料列表的统一 SELECT。"""
        return self._statement(
            f"""
            SELECT
                lm.id AS material_id, lm.title, lm.document_type,
                lm.status AS material_status, lm.index_request_version,
                rm.index_request_version AS synced_index_request_version,
                CASE
                    WHEN rm.index_request_version = lm.index_request_version
                        THEN rm.is_learning_content
                    ELSE NULL
                END AS is_learning_content,
                CASE
                    WHEN rm.index_request_version = lm.index_request_version
                        THEN rm.category
                    ELSE NULL
                END AS category,
                CASE
                    WHEN rm.index_request_version = lm.index_request_version
                        THEN COALESCE(rm.status, 'PENDING')
                    ELSE 'PENDING'
                END AS review_status,
                CASE
                    WHEN rm.index_request_version = lm.index_request_version
                        THEN rm.reason
                    WHEN rm.material_id IS NOT NULL
                        THEN '资料内容已更新，等待生成当前索引版本的复习卡片'
                    ELSE NULL
                END AS reason,
                CASE
                    WHEN rm.index_request_version = lm.index_request_version
                         AND rm.status = 'GENERATED'
                        THEN COALESCE(rm.card_count, 0)
                    ELSE 0
                END AS card_count,
                rm.extractor,
                review_folder_material.folder_id,
                review_folder.name AS folder_name,
                CASE
                    WHEN rm.index_request_version = lm.index_request_version
                        THEN rm.summary
                    ELSE NULL
                END AS material_summary,
                CASE
                    WHEN rm.index_request_version = lm.index_request_version
                        THEN COALESCE(rm.generation_attempts, 0)
                    ELSE 0
                END AS generation_attempts,
                CASE
                    WHEN rm.index_request_version = lm.index_request_version
                        THEN COALESCE(rm.quality_feedback, '[]'::jsonb)
                    ELSE '[]'::jsonb
                END AS quality_feedback,
                CASE
                    WHEN rm.index_request_version = lm.index_request_version
                        THEN COALESCE(rm.generation_progress, '{{{{}}}}'::jsonb)
                    ELSE '{{{{}}}}'::jsonb
                END AS generation_progress,
                COALESCE(rm.updated_at, lm.updated_at) AS review_updated_at
            FROM {{schema}}.learning_material lm
            LEFT JOIN {{schema}}.learning_review_material rm ON rm.material_id = lm.id
            LEFT JOIN {{schema}}.learning_review_folder_material review_folder_material
              ON review_folder_material.material_id = lm.id
             AND review_folder_material.user_id = lm.user_id
            LEFT JOIN {{schema}}.learning_review_folder review_folder
              ON review_folder.id = review_folder_material.folder_id
             AND review_folder.user_id = lm.user_id
            {suffix}
            """
        )

    def _folder_select(self, where_clause: str, tail_clause: str = "") -> Any:
        """构造文件夹与活动卡片统计的统一 SELECT。"""
        return self._statement(
            f"""
            SELECT
                folder.id,
                folder.user_id,
                folder.name,
                folder.updated_at,
                COUNT(DISTINCT folder_material.material_id) FILTER (
                    WHERE material.id IS NOT NULL AND excluded_material.material_id IS NULL
                ) AS material_count,
                COUNT(DISTINCT card.id) FILTER (
                    WHERE excluded_material.material_id IS NULL AND card.active = TRUE
                ) AS card_count,
                COUNT(DISTINCT card.id) FILTER (
                    WHERE excluded_material.material_id IS NULL
                      AND card.active = TRUE
                      AND card.due_at <= %s
                ) AS due_card_count
            FROM {{schema}}.learning_review_folder folder
            LEFT JOIN {{schema}}.learning_review_folder_material folder_material
              ON folder_material.folder_id = folder.id
             AND folder_material.user_id = folder.user_id
            LEFT JOIN {{schema}}.learning_material material
              ON material.id = folder_material.material_id
             AND material.user_id = folder.user_id
            LEFT JOIN {{schema}}.learning_review_material review_material
              ON review_material.material_id = material.id
             AND review_material.user_id = folder.user_id
             AND review_material.status = 'GENERATED'
             AND review_material.index_request_version = material.index_request_version
            LEFT JOIN {{schema}}.learning_review_card card
              ON card.material_id = material.id
             AND card.user_id = folder.user_id
             AND card.active = TRUE
             AND review_material.material_id IS NOT NULL
            LEFT JOIN {{schema}}.learning_review_material_exclusion excluded_material
              ON excluded_material.material_id = material.id
             AND excluded_material.user_id = folder.user_id
            {where_clause}
            GROUP BY folder.id, folder.user_id, folder.name, folder.updated_at
            {tail_clause}
            """
        )

    def _card_select(self, suffix: str) -> Any:
        """构造卡片与资料标题的统一 SELECT。"""
        return self._statement(
            f"""
            SELECT c.*, lm.title AS material_title, lm.document_type,
                   rm.summary AS material_summary,
                   folder_material.folder_id,
                   folder.name AS folder_name
            FROM {{schema}}.learning_review_card c
            JOIN {{schema}}.learning_material lm ON lm.id = c.material_id
            JOIN {{schema}}.learning_review_material rm
              ON rm.material_id = c.material_id
             AND rm.user_id = c.user_id
             AND rm.status = 'GENERATED'
             AND rm.index_request_version = lm.index_request_version
            LEFT JOIN {{schema}}.learning_review_folder_material folder_material
              ON folder_material.material_id = c.material_id
             AND folder_material.user_id = c.user_id
            LEFT JOIN {{schema}}.learning_review_folder folder
              ON folder.id = folder_material.folder_id
             AND folder.user_id = c.user_id
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
        raw_quality_feedback = row.get("quality_feedback") or []
        if isinstance(raw_quality_feedback, str):
            try:
                parsed_quality_feedback = json.loads(raw_quality_feedback)
            except (TypeError, ValueError):
                parsed_quality_feedback = []
        else:
            parsed_quality_feedback = raw_quality_feedback
        raw_generation_progress = row.get("generation_progress") or {}
        if isinstance(raw_generation_progress, str):
            try:
                parsed_generation_progress = json.loads(raw_generation_progress)
            except (TypeError, ValueError):
                parsed_generation_progress = {}
        else:
            parsed_generation_progress = raw_generation_progress
        if not isinstance(parsed_generation_progress, dict):
            parsed_generation_progress = {}
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
            summary=row.get("material_summary"),
            folder_id=(int(row["folder_id"]) if row.get("folder_id") is not None else None),
            folder_name=row.get("folder_name"),
            generation_attempts=int(row.get("generation_attempts") or 0),
            quality_feedback=tuple(
                str(item)
                for item in parsed_quality_feedback
                if str(item).strip()
            ),
            generation_progress=(dict(parsed_generation_progress) if parsed_generation_progress else None),
        )

    @staticmethod
    def _to_folder(row: dict[str, Any]) -> ReviewFolderRecord:
        """把文件夹统计行转换为业务记录。"""
        return ReviewFolderRecord(
            id=int(row["id"]),
            user_id=str(row["user_id"]),
            name=str(row["name"]),
            material_count=int(row.get("material_count") or 0),
            card_count=int(row.get("card_count") or 0),
            due_card_count=int(row.get("due_card_count") or 0),
            updated_at=row.get("updated_at"),
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
            source_key=str(row.get("source_key") or ""),
            material_summary=row.get("material_summary"),
            folder_id=(int(row["folder_id"]) if row.get("folder_id") is not None else None),
            folder_name=row.get("folder_name"),
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


def normalized_question_key(value: str) -> str:
    """生成数据库事务内使用的确定性问题去重键。"""
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def json_text(value: object, default: str) -> str:
    """把 JSONB 行值转换为 UTF-8 JSON 字符串。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def merge_generation_progress(
    current: object,
    event: dict[str, Any] | None,
) -> dict[str, Any]:
    """把当前阶段与最近十二条事件合并为有界 JSONB 快照。"""
    if isinstance(current, str):
        try:
            parsed = json.loads(current)
        except (TypeError, ValueError):
            parsed = {}
    else:
        parsed = current
    snapshot = dict(parsed) if isinstance(parsed, dict) else {}
    if not event:
        return snapshot
    normalized = {key: value for key, value in event.items() if value is not None and key != "events"}
    for key in ("stageCode", "stageLabel", "message", "status", "detail"):
        if key in normalized:
            normalized[key] = " ".join(str(normalized[key]).split()).strip()[:500]
    previous_events = snapshot.get("events")
    events = [dict(item) for item in previous_events if isinstance(item, dict)] if isinstance(previous_events, list) else []
    events.append(dict(normalized))
    return {**normalized, "events": events[-12:]}
