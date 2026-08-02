"""学习资料分类、知识点生成与 FSRS 复习编排服务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, time, timedelta, timezone
import json
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.result import BusinessError
from app.review.fsrs_scheduler import FsrsReviewScheduler, as_utc
from app.review.generation_guard import ReviewGenerationGuard
from app.review.knowledge_extractor import (
    KnowledgePointExtractor,
    LearningMaterialContext,
    ReviewExtractionError,
)
from app.review.repository import (
    CURRENT_REVIEW_EXTRACTORS,
    MaterialSourceRecord,
    ReviewCardDraft,
    ReviewCardRecord,
    ReviewMaterialRecord,
    ReviewOverviewStats,
    ReviewRepository,
    ReviewRepositoryProtocol,
    ReviewSettingsRecord,
)
from app.schemas.rag import Evidence
from app.schemas.review import (
    ReviewCard,
    ReviewBatchDeletionResult,
    ReviewCardGroup,
    ReviewDeletionResult,
    ReviewDueGroups,
    ReviewGroupOrderResult,
    ReviewGradeRequest,
    ReviewGradeResult,
    ReviewMaterial,
    ReviewOverview,
    ReviewSettings,
    ReviewSyncResult,
)
from prompts.review import REVIEW_CARD_PROMPT_VERSION


logger = logging.getLogger(__name__)


class ReviewService:
    """协调 evidence 提炼、持久化和 FSRS 状态更新。"""

    def __init__(
        self,
        repository: ReviewRepositoryProtocol | None = None,
        extractor: KnowledgePointExtractor | None = None,
        *,
        now_provider: Callable[[], datetime] | None = None,
        generation_guard: ReviewGenerationGuard | None = None,
    ) -> None:
        self.repository = repository or ReviewRepository()
        self.extractor = extractor or KnowledgePointExtractor()
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.generation_guard = generation_guard or ReviewGenerationGuard()

    def sync(self, user_id: str, limit: int = 1) -> ReviewSyncResult:
        """按索引版本扫描尚未同步的资料，每条资料独立完成分类。"""
        # 同步是含 LLM 的前台请求；单次只处理一份，避免串行模型调用造成网关超时。
        bounded_limit = 1
        with self.repository.transaction() as transaction:
            candidates = transaction.list_sync_candidates(user_id, bounded_limit)
        generated_cards = 0
        skipped = 0
        failed = 0
        for material in candidates:
            try:
                result = self._generate(material, user_id, force=False)
                if result is None:
                    # 另一实例正在处理同一资料，下一轮版本扫描会读取最终状态。
                    continue
                if result.status == "GENERATED":
                    generated_cards += result.cardCount
                elif result.status == "SKIPPED":
                    skipped += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
                logger.exception("自动同步学习资料复习卡片失败，materialId=%s", material.id)
        return ReviewSyncResult(
            processedMaterialCount=len(candidates),
            generatedCardCount=generated_cards,
            skippedMaterialCount=skipped,
            failedMaterialCount=failed,
        )

    def generate_material(self, material_id: int, user_id: str) -> ReviewMaterial:
        """显式重新分类并生成当前用户的一条资料。"""
        with self.repository.transaction() as transaction:
            material = transaction.find_material(material_id, user_id)
            excluded = transaction.is_material_excluded(material_id, user_id)
        if material is None:
            raise BusinessError("学习资料不存在")
        if excluded:
            raise BusinessError("该资料已从复习中心移除")
        result = self._generate(material, user_id, force=True)
        if result is None:
            with self.repository.transaction() as transaction:
                if transaction.is_material_excluded(material_id, user_id):
                    raise BusinessError("该资料已从复习中心移除")
            raise BusinessError("该资料的复习卡片正在生成，请稍后刷新")
        return result

    def generate_indexed_material(self, material_id: int) -> ReviewMaterial | None:
        """在 RAG worker 确认索引终态后，按资料真实归属幂等生成复习卡片。"""
        with self.repository.transaction() as transaction:
            material = transaction.find_material_by_id(material_id)
        if material is None or material.material_status not in {"READY", "PARTIAL"}:
            return None
        return self._generate(material, material.user_id, force=False)

    def list_materials(self, user_id: str) -> list[ReviewMaterial]:
        """读取当前用户所有资料的复习同步状态。"""
        with self.repository.transaction() as transaction:
            records = transaction.list_review_materials(user_id)
        return [material_response(record) for record in records]

    def delete_material(self, material_id: int, user_id: str) -> ReviewDeletionResult:
        """删除资料的全部复习内容并持久保存资料级排除意图。"""
        with self.repository.transaction() as transaction:
            deleted = transaction.exclude_material(material_id, user_id)
        if not deleted:
            raise BusinessError("学习资料不存在")
        return ReviewDeletionResult(scope="MATERIAL", materialId=material_id, deleted=True)

    def delete_materials(self, material_ids: list[int], user_id: str) -> ReviewBatchDeletionResult:
        """在一个事务中按稳定顺序批量移出资料，允许重复请求保持幂等。"""
        normalized_ids = sorted(set(material_ids))
        with self.repository.transaction() as transaction:
            deleted_ids = [
                material_id
                for material_id in normalized_ids
                if transaction.exclude_material(material_id, user_id)
            ]
        if not deleted_ids:
            raise BusinessError("学习资料不存在")
        return ReviewBatchDeletionResult(
            scope="MATERIAL",
            requestedCount=len(normalized_ids),
            deletedCount=len(deleted_ids),
            materialIds=deleted_ids,
        )

    def overview(self, user_id: str) -> ReviewOverview:
        """按用户时区计算今日评分数和实时到期统计。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            settings = transaction.get_or_create_settings(user_id)
            today_start, tomorrow_start = local_day_bounds(now, settings.timezone)
            stats = transaction.overview_stats(
                user_id,
                now=now,
                today_start=today_start,
                tomorrow_start=tomorrow_start,
            )
        return overview_response(stats, settings)

    def list_due(self, user_id: str, limit: int = 20) -> list[ReviewCard]:
        """按文档额度读取到期队列，并返回入选文档的全部卡片。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            settings = transaction.get_or_create_settings(user_id)
            today_start, tomorrow_start = local_day_bounds(now, settings.timezone)
            stats = transaction.overview_stats(
                user_id,
                now=now,
                today_start=today_start,
                tomorrow_start=tomorrow_start,
            )
            remaining_today = max(0, settings.daily_limit - stats.today_reviewed_count)
            material_limit = due_material_limit(limit, remaining_today, stats.started_due_material_count)
            if material_limit == 0:
                return []
            records = transaction.list_due_group_cards(
                user_id,
                now=now,
                today_start=today_start,
                tomorrow_start=tomorrow_start,
                limit=material_limit,
            )
        scheduler = FsrsReviewScheduler(settings.desired_retention)
        return [card_response(record, scheduler, now, include_answer=False) for record in records]

    def list_due_groups(self, user_id: str, limit: int = 20) -> ReviewDueGroups:
        """读取每日到期卡片并按上传资料聚合，答案由揭示接口单独返回。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            settings = transaction.get_or_create_settings(user_id)
            today_start, tomorrow_start = local_day_bounds(now, settings.timezone)
            stats = transaction.overview_stats(
                user_id,
                now=now,
                today_start=today_start,
                tomorrow_start=tomorrow_start,
            )
            remaining_today = max(0, settings.daily_limit - stats.today_reviewed_count)
            material_limit = due_material_limit(limit, remaining_today, stats.started_due_material_count)
            if material_limit == 0:
                return ReviewDueGroups(totalDueCount=stats.due_count, remainingToday=0, groups=[])
            records = transaction.list_due_group_cards(
                user_id,
                now=now,
                today_start=today_start,
                tomorrow_start=tomorrow_start,
                limit=material_limit,
            )
        scheduler = FsrsReviewScheduler(settings.desired_retention)
        grouped: dict[int, ReviewCardGroup] = {}
        for record in records:
            group = grouped.setdefault(
                record.material_id,
                ReviewCardGroup(
                    materialId=record.material_id,
                    materialTitle=record.material_title,
                    materialSummary=record.material_summary,
                    documentType=record.document_type,
                    dueCardCount=0,
                    cards=[],
                ),
            )
            group.cards.append(card_response(record, scheduler, now, include_answer=False))
            group.dueCardCount += 1
        return ReviewDueGroups(
            totalDueCount=stats.due_count,
            remainingToday=remaining_today,
            groups=list(grouped.values()),
        )

    def reorder_due_groups(self, material_ids: list[int], user_id: str) -> ReviewGroupOrderResult:
        """原子保存资料分组优先级，并拒绝任一不存在或越权资料。"""
        with self.repository.transaction() as transaction:
            ordered_ids = transaction.reorder_review_materials(user_id, material_ids)
        if ordered_ids is None:
            raise BusinessError("复习资料不存在")
        return ReviewGroupOrderResult(materialIds=ordered_ids, orderedCount=len(ordered_ids))

    def get_card(self, card_id: int, user_id: str) -> ReviewCard:
        """在用户主动揭示时读取答案和完整 evidence，并再次校验资料归属。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            card = transaction.find_card(card_id, user_id)
            if card is None:
                raise BusinessError("复习卡片不存在")
            settings = transaction.get_or_create_settings(user_id)
        return card_response(card, FsrsReviewScheduler(settings.desired_retention), now, include_answer=True)

    def delete_card(self, card_id: int, user_id: str) -> ReviewDeletionResult:
        """软停用卡片并保留评分日志，以来源键阻止后续重新生成。"""
        with self.repository.transaction() as transaction:
            material_id = transaction.exclude_card(card_id, user_id)
        if material_id is None:
            raise BusinessError("复习卡片不存在")
        return ReviewDeletionResult(scope="CARD", materialId=material_id, cardId=card_id, deleted=True)

    def delete_cards(self, card_ids: list[int], user_id: str) -> ReviewBatchDeletionResult:
        """在一个事务中按稳定顺序批量删除卡片，保留每张卡片的来源排除记录。"""
        normalized_ids = sorted(set(card_ids))
        with self.repository.transaction() as transaction:
            deleted_ids = [
                card_id
                for card_id in normalized_ids
                if transaction.exclude_card(card_id, user_id) is not None
            ]
        if not deleted_ids:
            raise BusinessError("复习卡片不存在")
        return ReviewBatchDeletionResult(
            scope="CARD",
            requestedCount=len(normalized_ids),
            deletedCount=len(deleted_ids),
            cardIds=deleted_ids,
        )

    def grade(self, card_id: int, payload: ReviewGradeRequest, user_id: str) -> ReviewGradeResult:
        """在单个事务内更新 FSRS 卡片并追加一次评分日志。"""
        reviewed_at = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            # 先锁定用户设置行，使同一用户的并发评分按顺序核对每日额度。
            settings = transaction.get_or_create_settings(user_id, for_update=True)
            card = transaction.find_card_for_update(card_id, user_id)
            if card is None:
                raise BusinessError("复习卡片不存在")
            if as_utc(card.due_at) > reviewed_at:
                raise BusinessError("复习卡片尚未到期")
            today_start, tomorrow_start = local_day_bounds(reviewed_at, settings.timezone)
            daily_stats = transaction.overview_stats(
                user_id,
                now=reviewed_at,
                today_start=today_start,
                tomorrow_start=tomorrow_start,
            )
            material_reviewed_today = transaction.has_material_reviewed_today(
                card.material_id,
                user_id,
                today_start=today_start,
                tomorrow_start=tomorrow_start,
            )
            if not material_reviewed_today and daily_stats.today_reviewed_count >= settings.daily_limit:
                raise BusinessError("今日复习文档上限已达到")
            scheduler = FsrsReviewScheduler(settings.desired_retention)
            scheduled = scheduler.review(
                card.fsrs_card_json,
                rating=payload.rating,
                reviewed_at=reviewed_at,
                duration_ms=payload.durationMs,
                fallback_created_at=card.created_at or reviewed_at,
            )
            updated = transaction.save_grade(
                card,
                rating=payload.rating,
                duration_ms=payload.durationMs,
                reviewed_at=reviewed_at,
                previous_due_at=card.due_at,
                next_due_at=scheduled.due_at,
                interval_days=scheduled.interval_days,
                retrievability=scheduled.retrievability,
                fsrs_card_json=scheduled.card_json,
                fsrs_review_log_json=scheduled.review_log_json,
                state_rebuilt=scheduled.state_rebuilt,
            )
        return ReviewGradeResult(
            card=card_response(updated, scheduler, reviewed_at, include_answer=True),
            previousDueAt=card.due_at,
            nextDueAt=scheduled.due_at,
            intervalDays=scheduled.interval_days,
            retrievability=scheduled.retrievability,
        )

    def update_settings(self, payload: ReviewSettings, user_id: str) -> ReviewSettings:
        """校验 IANA 时区后保存提醒偏好。"""
        validate_timezone(payload.timezone)
        with self.repository.transaction() as transaction:
            record = transaction.update_settings(
                user_id,
                enabled=payload.enabled,
                desired_retention=payload.desiredRetention,
                daily_limit=payload.dailyLimit,
                reminder_time=payload.reminderTime,
                timezone=payload.timezone,
            )
        return settings_response(record)

    def _generate(self, material: MaterialSourceRecord, user_id: str, *, force: bool) -> ReviewMaterial | None:
        """读取 evidence 后提炼卡片，再用资料级短锁和短事务更新分类。"""
        if material.user_id != user_id:
            raise BusinessError("学习资料不存在")
        if material.material_status not in {"READY", "PARTIAL"}:
            raise BusinessError("学习资料尚未完成索引")
        with self.repository.transaction() as transaction:
            if transaction.is_material_excluded(material.id, user_id):
                if force:
                    raise BusinessError("该资料已从复习中心移除")
                return None
        lock = self.generation_guard.acquire(
            f"{user_id}:{material.id}:{material.index_request_version}"
        )
        if lock is None:
            if force:
                raise BusinessError("该资料的复习卡片正在生成，请稍后刷新")
            return None
        try:
            with self.repository.transaction() as transaction:
                if transaction.is_material_excluded(material.id, user_id):
                    if force:
                        raise BusinessError("该资料已从复习中心移除")
                    return None
            if not force:
                with self.repository.transaction() as transaction:
                    current = transaction.find_review_material(material.id, user_id)
                if current and material_generation_is_current(current, material.index_request_version):
                    return material_response(current)
            with self.repository.transaction() as transaction:
                evidences = transaction.list_evidences(material)
            now = as_utc(self.now_provider())
            if not evidences:
                return self._save_generation(
                    material,
                    is_learning_content=None,
                    category=None,
                    summary=None,
                    status="FAILED",
                    reason="资料暂无可用 evidence，无法调用 DeepSeek 生成复习内容",
                    extractor=f"failed:{REVIEW_CARD_PROMPT_VERSION}",
                    cards=[],
                )
            try:
                extraction = self.extractor.extract(
                    LearningMaterialContext(
                        material_id=material.id,
                        title=material.title,
                        document_type=material.document_type,
                        summary=material.document_summary,
                    ),
                    evidences,
                )
            except ReviewExtractionError as exc:
                # 失败结果会停用旧卡片，避免继续展示本地降级或旧 Prompt 的坏内容。
                return self._save_generation(
                    material,
                    is_learning_content=None,
                    category=None,
                    summary=None,
                    status="FAILED",
                    reason=str(exc),
                    extractor=f"failed:{REVIEW_CARD_PROMPT_VERSION}",
                    cards=[],
                )
            summary = extraction.summary
            if not extraction.is_learning_content:
                return self._save_generation(
                    material,
                    is_learning_content=False,
                    category=extraction.category,
                    summary=summary,
                    status="SKIPPED",
                    reason=extraction.reason,
                    extractor=extraction.extractor,
                    cards=[],
                )
            if not extraction.knowledge_points:
                return self._save_generation(
                    material,
                    is_learning_content=True,
                    category=extraction.category,
                    summary=summary,
                    status="FAILED",
                    reason=extraction.reason,
                    extractor=extraction.extractor,
                    cards=[],
                )
            scheduler = FsrsReviewScheduler()
            cards = [
                ReviewCardDraft(
                    source_key=point.source_key,
                    question=point.question,
                    answer=point.answer,
                    hint=point.hint,
                    evidence_refs_json=json.dumps(
                        [reference.model_dump(mode="json") for reference in point.evidence_refs],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    fsrs_card_json=scheduler.new_card_json(now),
                    due_at=now,
                )
                for point in extraction.knowledge_points
                if point.evidence_refs
            ]
            status = "GENERATED" if cards else "FAILED"
            reason = extraction.reason if cards else "知识点未包含有效 evidence 引用"
            return self._save_generation(
                material,
                is_learning_content=True,
                category=extraction.category,
                summary=summary,
                status=status,
                reason=reason,
                extractor=extraction.extractor,
                cards=cards,
            )
        finally:
            lock.release()

    def _save_generation(
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
    ) -> ReviewMaterial | None:
        """保存一次完整生成结果并转换为公开响应。"""
        with self.repository.transaction() as transaction:
            record = transaction.save_generation(
                material,
                is_learning_content=is_learning_content,
                category=category,
                summary=summary,
                status=status,
                reason=reason,
                extractor=extractor,
                cards=cards,
            )
        return material_response(record) if record is not None else None


def card_response(
    record: ReviewCardRecord,
    scheduler: FsrsReviewScheduler,
    at: datetime,
    *,
    include_answer: bool,
) -> ReviewCard:
    """按是否主动揭示决定返回答案和 evidence，并动态计算可提取率。"""
    references: list[Evidence] = []
    if include_answer:
        try:
            raw_references = json.loads(record.evidence_refs_json)
            references = [Evidence.model_validate(item) for item in raw_references]
        except (TypeError, ValueError):
            references = []
    retrievability = scheduler.retrievability(record.fsrs_card_json, at)
    if record.review_count > 0 and retrievability <= 0:
        retrievability = max(0.0, min(1.0, record.retrievability))
    return ReviewCard(
        id=record.id,
        materialId=record.material_id,
        materialTitle=record.material_title,
        documentType=record.document_type,
        question=record.question,
        answer=record.answer if include_answer else None,
        # 提示不等于答案，允许用户在揭示前选择查看。
        hint=record.hint,
        evidenceRefs=references,
        dueAt=record.due_at,
        retrievability=retrievability,
        reviewCount=record.review_count,
        lapseCount=record.lapse_count,
    )


def material_generation_is_current(record: ReviewMaterialRecord, index_request_version: int) -> bool:
    """同时校验资料索引版本和提炼器版本，确保 Prompt 升级会重建旧卡片。"""
    return (
        record.synced_index_request_version is not None
        and record.synced_index_request_version >= index_request_version
        and record.extractor in CURRENT_REVIEW_EXTRACTORS
    )


def material_response(record: ReviewMaterialRecord) -> ReviewMaterial:
    """把资料联合记录转换为公开响应。"""
    return ReviewMaterial(
        materialId=record.material_id,
        title=record.title,
        summary=record.summary,
        documentType=record.document_type,
        materialStatus=record.material_status,
        isLearningContent=record.is_learning_content,
        category=record.category,
        status=record.status,  # type: ignore[arg-type]
        reason=record.reason,
        cardCount=record.card_count,
        indexRequestVersion=record.index_request_version,
        syncedIndexRequestVersion=record.synced_index_request_version,
        updatedAt=record.updated_at,
    )


def settings_response(record: ReviewSettingsRecord) -> ReviewSettings:
    """把设置记录转换为公开响应。"""
    return ReviewSettings(
        enabled=record.enabled,
        desiredRetention=record.desired_retention,
        dailyLimit=record.daily_limit,
        reminderTime=record.reminder_time,
        timezone=record.timezone,
    )


def overview_response(stats: ReviewOverviewStats, settings: ReviewSettingsRecord) -> ReviewOverview:
    """组合统计，并以可复习文档数驱动每日提醒。"""
    remaining_documents = max(0, settings.daily_limit - stats.today_reviewed_count)
    # 兼容仍使用旧内存统计对象的调用方；数据库统计始终返回 due_material_count。
    due_material_count = stats.due_material_count or stats.due_count
    actionable_due_count = min(
        due_material_count,
        stats.started_due_material_count + remaining_documents,
    )
    return ReviewOverview(
        dueCount=stats.due_count,
        actionableDueCount=actionable_due_count,
        todayReviewedCount=stats.today_reviewed_count,
        totalCardCount=stats.total_card_count,
        activeMaterialCount=stats.active_material_count,
        nextDueAt=stats.next_due_at,
        settings=settings_response(settings),
    )


def due_material_limit(requested_limit: int, remaining_today: int, started_due_material_count: int) -> int:
    """计算本次可选文档数，已开始的文档不重复消耗每日额度。"""
    requested = min(max(1, requested_limit), 100)
    started = max(0, started_due_material_count)
    available_new = min(max(0, remaining_today), max(0, requested - started))
    return started + available_new


def local_day_bounds(now: datetime, timezone_name: str) -> tuple[datetime, datetime]:
    """计算指定 IANA 时区当天的 UTC 起止时间。"""
    zone = validate_timezone(timezone_name)
    local_now = as_utc(now).astimezone(zone)
    today = datetime.combine(local_now.date(), time.min, tzinfo=zone)
    tomorrow = datetime.combine(local_now.date() + timedelta(days=1), time.min, tzinfo=zone)
    return today.astimezone(timezone.utc), tomorrow.astimezone(timezone.utc)


def validate_timezone(value: str) -> ZoneInfo:
    """校验用户设置使用的 IANA 时区。"""
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise BusinessError("提醒时区必须是有效的 IANA 时区") from None
