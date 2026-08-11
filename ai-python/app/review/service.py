"""学习资料分类、知识点生成与 FSRS 复习编排服务。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import logging
import os
from threading import Lock
from time import monotonic
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.result import BusinessError
from app.core.io_concurrency import configured_io_workers
from app.review.fsrs_scheduler import FsrsReviewScheduler, as_utc
from app.review.execution_budget import ReviewExecutionBudget
from app.review.card_rewriter import (
    CardRewriter,
    infer_material_rewrite_card_count,
)
from app.review.generation_graph import ReviewManualReviewRequired
from app.review.generation_guard import ReviewGenerationGuard
from app.review.knowledge_extractor import (
    KnowledgePointExtractor,
    LearningMaterialContext,
    ReviewExtractionError,
    answer_is_grounded,
    clean_review_evidences,
    deduplicate_evidences,
    split_review_evidence_segments,
)
from app.review.missing_knowledge import MissingKnowledgeExtractor
from app.review.repository import (
    MaterialSourceRecord,
    ReviewCardDraft,
    ReviewCardRecord,
    ReviewFolderRecord,
    ReviewMaterialRecord,
    ReviewOverviewStats,
    ReviewRepository,
    ReviewRepositoryProtocol,
    ReviewSettingsRecord,
)
from app.schemas.rag import Evidence
from app.schemas.review import (
    ReviewCard,
    ReviewCardContent,
    ReviewCardLibrary,
    ReviewCardLibraryMaterial,
    ReviewCardRewritePreview,
    ReviewCardRewriteRequest,
    ReviewCardUpdateRequest,
    ReviewMaterialCardSnapshot,
    ReviewMaterialRewriteApplyRequest,
    ReviewMaterialRewriteApplyResult,
    ReviewMaterialRewritePreview,
    ReviewMaterialRewriteRequest,
    ReviewEvidenceSegment,
    ReviewSegmentGenerationResult,
    ReviewSegmentMergeRequest,
    ReviewSegmentResult,
    ReviewSegmentWorkspace,
    ReviewBatchDeletionResult,
    ReviewCardGroup,
    ReviewDeletionResult,
    ReviewDueGroups,
    ReviewFolder,
    ReviewFolderAssignmentResult,
    ReviewFolderDeletionResult,
    ReviewFolderDetail,
    ReviewFolderMaterial,
    ReviewGroupOrderResult,
    ReviewGradeRequest,
    ReviewGradeResult,
    ReviewGenerationProgress,
    ReviewMaterial,
    ReviewMaterialFolderRequest,
    ReviewMissingKnowledgeRequest,
    ReviewMissingKnowledgeResult,
    ReviewManualCardRequest,
    ReviewOverview,
    ReviewSettings,
    ReviewSyncResult,
)
from prompts.review import REVIEW_CARD_PROMPT_VERSION


logger = logging.getLogger(__name__)
_review_segment_extract_executor = ThreadPoolExecutor(
    max_workers=configured_io_workers("LLM_IO_MAX_WORKERS"),
    thread_name_prefix="review-segment",
)


def configured_review_segment_timeout_seconds() -> float:
    """读取单个交互式分段的总执行预算，避免异常模型请求永久占用整轮任务。"""
    try:
        configured = float(os.getenv("REVIEW_SEGMENT_TIMEOUT_SECONDS", "1800"))
    except (TypeError, ValueError):
        configured = 1800.0
    return max(0.05, configured)


def configured_review_segment_request_timeout_seconds() -> float:
    """读取交互式分段单次模型请求预算，默认明显小于单段总预算。"""
    try:
        configured = float(os.getenv("REVIEW_SEGMENT_REQUEST_TIMEOUT_SECONDS", "240"))
    except (TypeError, ValueError):
        configured = 240.0
    return max(0.05, min(900.0, configured))


def report_progress(
    callback: Callable[[dict[str, object]], None] | None,
    *,
    stage_code: str,
    stage_label: str,
    message: str,
    percent: int,
) -> None:
    """向后台补漏任务报告阶段，回调失败不影响补漏主流程。"""
    if callback is None:
        return
    try:
        callback(
            {
                "stageCode": stage_code,
                "stageLabel": stage_label,
                "message": message,
                "percent": percent,
            }
        )
    except Exception:  # noqa: BLE001 - 进度展示不能破坏卡片追加事务。
        logger.warning("复习补漏进度回调失败，stageCode=%s", stage_code, exc_info=True)


class ReviewService:
    """协调 evidence 提炼、持久化和 FSRS 状态更新。"""

    def __init__(
        self,
        repository: ReviewRepositoryProtocol | None = None,
        extractor: KnowledgePointExtractor | None = None,
        *,
        now_provider: Callable[[], datetime] | None = None,
        generation_guard: ReviewGenerationGuard | None = None,
        missing_knowledge_extractor: MissingKnowledgeExtractor | None = None,
        card_rewriter: CardRewriter | None = None,
    ) -> None:
        self.repository = repository or ReviewRepository()
        self.extractor = extractor or KnowledgePointExtractor()
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self.generation_guard = generation_guard or ReviewGenerationGuard()
        self.missing_knowledge_extractor = missing_knowledge_extractor or MissingKnowledgeExtractor()
        self.card_rewriter = card_rewriter or CardRewriter()

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

    def generate_material(
        self,
        material_id: int,
        user_id: str,
        user_feedback: str | None = None,
        generation_mode: str = "STANDARD",
    ) -> ReviewMaterial:
        """携带门禁模式和可选人工说明，显式重新生成当前用户的一条资料。"""
        with self.repository.transaction() as transaction:
            material = transaction.find_material(material_id, user_id)
            excluded = transaction.is_material_excluded(material_id, user_id)
        if material is None:
            raise BusinessError("学习资料不存在")
        if excluded:
            raise BusinessError("该资料已从复习中心移除")
        result = self._generate(
            material,
            user_id,
            force=True,
            user_feedback=user_feedback,
            generation_mode=generation_mode,
        )
        if result is None:
            with self.repository.transaction() as transaction:
                if transaction.is_material_excluded(material_id, user_id):
                    raise BusinessError("该资料已从复习中心移除")
            raise BusinessError("该资料的复习卡片正在生成，请稍后刷新")
        return result

    def prepare_material_generation(
        self,
        material_id: int,
        user_id: str,
        generation_mode: str = "STANDARD",
    ) -> ReviewMaterial:
        """先持久化后台生成状态，避免人工触发请求同步等待模型返回。"""
        with self.repository.transaction() as transaction:
            material = transaction.find_material(material_id, user_id)
            excluded = transaction.is_material_excluded(material_id, user_id)
            current = transaction.find_review_material(material_id, user_id)
        if material is None:
            raise BusinessError("学习资料不存在")
        if excluded:
            raise BusinessError("该资料已从复习中心移除")
        if material.material_status not in {"READY", "PARTIAL"}:
            raise BusinessError("学习资料尚未完成索引")
        if current is not None and current.status == "GENERATING":
            return material_response(current)
        self._record_generation_progress(
            material,
            {
                "stageCode": "review.queued",
                "stageLabel": "后台排队",
                "message": f"已选择{generation_mode_label(generation_mode)}，生成请求已转入后台队列",
                "status": "RUNNING",
                "currentStep": 1,
                "totalSteps": 4,
                "percent": 0,
                "attempt": 0,
            },
        )
        with self.repository.transaction() as transaction:
            queued = transaction.find_review_material(material_id, user_id)
        if queued is None:
            raise BusinessError("复习生成任务入队失败，请稍后重试")
        return material_response(queued)

    def keep_current_generation(self, material_id: int, user_id: str) -> ReviewMaterial:
        """用户确认保留当前活动卡片，跳过本轮模型重新生成。"""
        with self.repository.transaction() as transaction:
            material = transaction.find_material(material_id, user_id)
            excluded = transaction.is_material_excluded(material_id, user_id)
        if material is None:
            raise BusinessError("学习资料不存在")
        if excluded:
            raise BusinessError("该资料已从复习中心移除")
        event = terminal_generation_progress_event(
            "GENERATED",
            "用户选择保留当前可用卡片",
            0,
            at=as_utc(self.now_provider()),
        )
        event.update(
            {
                "stageCode": "review.keep_current",
                "stageLabel": "保留当前版本",
                "message": "已保留当前可用卡片，本次没有调用模型",
            }
        )
        with self.repository.transaction() as transaction:
            record = transaction.keep_current_generation(material, event)
        if record is None:
            raise BusinessError("当前没有可保留的复习卡片，请选择重新生成方式")
        return material_response(record)

    def generate_indexed_material(self, material_id: int) -> ReviewMaterial | None:
        """在 RAG worker 确认索引终态后，按资料真实归属幂等生成复习卡片。"""
        with self.repository.transaction() as transaction:
            material = transaction.find_material_by_id(material_id)
        if material is None or material.material_status not in {"READY", "PARTIAL"}:
            return None
        return self._generate(material, material.user_id, force=False)

    def supplement_missing_knowledge(
        self,
        material_id: int,
        payload: ReviewMissingKnowledgeRequest,
        user_id: str,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> ReviewMissingKnowledgeResult:
        """按用户提示查找遗漏知识点，并通过独立 add-only 事务追加新卡。"""
        report_progress(
            progress_callback,
            stage_code="missing.evidence",
            stage_label="整理原文",
            message="正在读取当前文档的 RAG evidence",
            percent=25,
        )
        with self.repository.transaction() as transaction:
            material = transaction.find_material(material_id, user_id)
            excluded = transaction.is_material_excluded(material_id, user_id)
            review_material = transaction.find_review_material(material_id, user_id)
        if material is None:
            raise BusinessError("学习资料不存在")
        if excluded:
            raise BusinessError("该资料已从复习中心移除")
        if material.material_status not in {"READY", "PARTIAL"}:
            raise BusinessError("学习资料尚未完成索引")
        if review_material is None or not material_generation_is_current(
            review_material,
            material.index_request_version,
        ) or review_material.status != "GENERATED":
            raise BusinessError("请先成功生成该资料的复习卡片，再查找遗漏知识点")
        lock = self.generation_guard.acquire(f"{user_id}:{material.id}:{material.index_request_version}")
        if lock is None:
            raise BusinessError("该资料正在处理复习卡片，请稍后再试")
        try:
            with self.repository.transaction() as transaction:
                evidences = transaction.list_evidences(material)
                existing_cards = transaction.list_active_cards_for_material(material.id, user_id)
            report_progress(
                progress_callback,
                stage_code="missing.evidence.ready",
                stage_label="核对现有卡片",
                message=f"已读取 {len(evidences)} 个原文片段和 {len(existing_cards)} 张现有卡片",
                percent=38,
            )
            report_progress(
                progress_callback,
                stage_code="missing.model",
                stage_label="模型核对",
                message="正在根据遗漏主题定位相关原文并执行 evidence 质量门禁",
                percent=55,
            )
            extraction = self.missing_knowledge_extractor.extract(
                LearningMaterialContext(
                    material_id=material.id,
                    title=material.title,
                    document_type=material.document_type,
                    summary=material.document_summary,
                ),
                evidences,
                message=payload.message,
                conversation=payload.conversation,
                existing_cards=existing_cards,
            )
            report_progress(
                progress_callback,
                stage_code="missing.quality",
                stage_label="质量校验",
                message=f"候选核对完成，准备写入 {len(extraction.knowledge_points)} 张新卡片",
                percent=78,
            )
            if not extraction.knowledge_points:
                return ReviewMissingKnowledgeResult(
                    materialId=material.id,
                    assistantMessage=extraction.assistant_message or "没有找到同时满足原文支撑和去重要求的新知识点。",
                    addedCount=0,
                    skippedCount=extraction.skipped_count,
                    cards=[],
                )
            now = as_utc(self.now_provider())
            scheduler = FsrsReviewScheduler()
            drafts = [
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
            ]
            with self.repository.transaction() as transaction:
                report_progress(
                    progress_callback,
                    stage_code="missing.persist",
                    stage_label="保存卡片",
                    message="正在以只追加方式保存新卡片，不修改既有复习记录",
                    percent=90,
                )
                inserted = transaction.append_review_cards(material, drafts)
            skipped_count = extraction.skipped_count + len(drafts) - len(inserted)
            added_count = len(inserted)
            added_topics = "；".join(card.question for card in inserted[:3])
            message = (
                f"找到并追加了 {added_count} 个有原文支撑、且未被现有卡片覆盖的知识点：{added_topics}"
                if added_count
                else "候选知识点已被现有卡片覆盖或曾被删除，本次没有新增卡片。"
            )
            return ReviewMissingKnowledgeResult(
                materialId=material.id,
                assistantMessage=message,
                addedCount=added_count,
                skippedCount=skipped_count,
                cards=[card_response(card, scheduler, now, include_answer=True) for card in inserted],
            )
        except ReviewExtractionError as exc:
            raise BusinessError(str(exc)) from exc
        except RuntimeError as exc:
            raise BusinessError(str(exc)) from exc
        finally:
            lock.release()

    def create_manual_card(
        self,
        material_id: int,
        payload: ReviewManualCardRequest,
        user_id: str,
    ) -> ReviewCard:
        """把用户自定义问题追加到指定资料，不改写既有复习状态。"""
        with self.repository.transaction() as transaction:
            material = transaction.find_material(material_id, user_id)
            excluded = transaction.is_material_excluded(material_id, user_id)
            review_material = transaction.find_review_material(material_id, user_id)
        if material is None:
            raise BusinessError("学习资料不存在")
        if excluded:
            raise BusinessError("该资料已从复习中心移除")
        if material.material_status not in {"READY", "PARTIAL"}:
            raise BusinessError("学习资料尚未完成索引")
        if review_material is None or not material_generation_is_current(
            review_material,
            material.index_request_version,
        ) or review_material.status != "GENERATED":
            raise BusinessError("请先成功生成该资料的复习卡片，再创建手动卡片")
        now = as_utc(self.now_provider())
        scheduler = FsrsReviewScheduler()
        draft = ReviewCardDraft(
            source_key=f"manual:{uuid4().hex}",
            question=payload.question,
            answer=payload.answer,
            hint=payload.hint,
            evidence_refs_json="[]",
            fsrs_card_json=scheduler.new_card_json(now),
            due_at=now,
        )
        with self.repository.transaction() as transaction:
            inserted = transaction.append_review_cards(material, [draft])
        if not inserted:
            raise BusinessError("手动卡片未能保存，请刷新后重试")
        return card_response(inserted[0], scheduler, now, include_answer=True)

    def list_materials(self, user_id: str) -> list[ReviewMaterial]:
        """读取当前用户所有资料的复习同步状态。"""
        with self.repository.transaction() as transaction:
            records = transaction.list_review_materials(user_id)
        return [material_response(record) for record in records]

    def list_folders(self, user_id: str) -> list[ReviewFolder]:
        """读取当前用户的复习文件夹和实时文档、卡片统计。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            records = transaction.list_review_folders(user_id, now=now)
        return [folder_response(record) for record in records]

    def get_folder(self, folder_id: int, user_id: str) -> ReviewFolderDetail:
        """进入一个文件夹，并按文档返回全部活动卡片且隐藏答案。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            folder = transaction.find_review_folder(folder_id, user_id, now=now)
            if folder is None:
                raise BusinessError("复习文件夹不存在")
            materials = transaction.list_review_materials_in_folder(folder_id, user_id)
            cards = transaction.list_review_cards_in_folder(folder_id, user_id)
            settings = transaction.get_or_create_settings(user_id)
        scheduler = FsrsReviewScheduler(settings.desired_retention)
        cards_by_material: dict[int, list[ReviewCard]] = {}
        for card in cards:
            cards_by_material.setdefault(card.material_id, []).append(
                card_response(card, scheduler, now, include_answer=False)
            )
        return ReviewFolderDetail(
            folder=folder_response(folder),
            materials=[
                ReviewFolderMaterial(
                    materialId=material.material_id,
                    title=material.title,
                    summary=material.summary,
                    documentType=material.document_type,
                    materialStatus=material.material_status,
                    category=material.category,
                    status=material.status,
                    reason=material.reason,
                    generationProgress=generation_progress_response(material.generation_progress),
                    indexRequestVersion=material.index_request_version,
                    cardCount=material.card_count,
                    cards=cards_by_material.get(material.material_id, []),
                )
                for material in materials
            ],
        )

    def create_folder(self, name: str, user_id: str) -> ReviewFolder:
        """创建当前用户的空复习文件夹，并拒绝同名目录。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            if transaction.review_folder_name_exists(user_id, name):
                raise BusinessError("复习文件夹名称已存在")
            record = transaction.create_review_folder(user_id, name, now=now)
        return folder_response(record)

    def rename_folder(self, folder_id: int, name: str, user_id: str) -> ReviewFolder:
        """重命名当前用户文件夹并保留全部文档归属。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            if transaction.review_folder_name_exists(user_id, name, exclude_folder_id=folder_id):
                raise BusinessError("复习文件夹名称已存在")
            record = transaction.rename_review_folder(folder_id, user_id, name, now=now)
        if record is None:
            raise BusinessError("复习文件夹不存在")
        return folder_response(record)

    def delete_folder(self, folder_id: int, user_id: str) -> ReviewFolderDeletionResult:
        """删除文件夹并把其中资料恢复为未归档，不删除任何卡片。"""
        with self.repository.transaction() as transaction:
            material_count = transaction.delete_review_folder(folder_id, user_id)
        if material_count is None:
            raise BusinessError("复习文件夹不存在")
        return ReviewFolderDeletionResult(
            folderId=folder_id,
            deleted=True,
            unfiledMaterialCount=material_count,
        )

    def assign_materials_to_folder(
        self,
        payload: ReviewMaterialFolderRequest,
        user_id: str,
    ) -> ReviewFolderAssignmentResult:
        """在一个事务中移动整份文档，禁止部分成功或越权归档。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            if payload.folderId is not None and transaction.find_review_folder(
                payload.folderId,
                user_id,
                now=now,
            ) is None:
                raise BusinessError("复习文件夹不存在")
            moved_ids = transaction.assign_review_materials_to_folder(
                user_id,
                payload.materialIds,
                payload.folderId,
            )
        if moved_ids is None:
            raise BusinessError("复习资料不存在")
        return ReviewFolderAssignmentResult(
            folderId=payload.folderId,
            materialIds=moved_ids,
            movedCount=len(moved_ids),
        )

    def reorder_folder_materials(
        self,
        folder_id: int,
        material_ids: list[int],
        user_id: str,
    ) -> ReviewGroupOrderResult:
        """原子保存文件夹内文档优先级，并拒绝越权或不属于该文件夹的资料。"""
        with self.repository.transaction() as transaction:
            ordered_ids = transaction.reorder_review_folder_materials(folder_id, user_id, material_ids)
        if ordered_ids is None:
            raise BusinessError("复习文件夹不存在或资料不属于该文件夹")
        return ReviewGroupOrderResult(materialIds=ordered_ids, orderedCount=len(ordered_ids))

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
                    folderId=record.folder_id,
                    folderName=record.folder_name,
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

    def list_card_library(self, user_id: str) -> ReviewCardLibrary:
        """按文档返回全部活动卡片，包括已复习但尚未再次到期的卡片。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            cards = transaction.list_all_active_cards(user_id)
            settings = transaction.get_or_create_settings(user_id)
        scheduler = FsrsReviewScheduler(settings.desired_retention)
        grouped: dict[int, ReviewCardLibraryMaterial] = {}
        reviewed_card_count = 0
        for record in cards:
            card = card_response(record, scheduler, now, include_answer=True)
            group = grouped.setdefault(
                record.material_id,
                ReviewCardLibraryMaterial(
                    materialId=record.material_id,
                    title=record.material_title,
                    summary=record.material_summary,
                    documentType=record.document_type,
                    folderId=record.folder_id,
                    folderName=record.folder_name,
                    cardCount=0,
                    reviewedCardCount=0,
                    cards=[],
                ),
            )
            group.cards.append(card)
            group.cardCount += 1
            if record.review_count > 0:
                group.reviewedCardCount += 1
                reviewed_card_count += 1
        return ReviewCardLibrary(
            totalMaterialCount=len(grouped),
            totalCardCount=len(cards),
            reviewedCardCount=reviewed_card_count,
            materials=list(grouped.values()),
        )

    def preview_card_rewrite(
        self,
        card_id: int,
        payload: ReviewCardRewriteRequest,
        user_id: str,
    ) -> ReviewCardRewritePreview:
        """调用 LLM 生成无副作用候选，数据库内容保持不变。"""
        with self.repository.transaction() as transaction:
            card = transaction.find_card(card_id, user_id)
            if card is None:
                raise BusinessError("复习卡片不存在")
            material = transaction.find_material(card.material_id, user_id)
            if material is None:
                raise BusinessError("学习资料不存在")
            evidences = transaction.list_evidences(material)
        try:
            candidate = self.card_rewriter.rewrite(
                LearningMaterialContext(
                    material_id=material.id,
                    title=material.title,
                    document_type=material.document_type,
                    summary=material.document_summary,
                ),
                card,
                evidences,
                instruction=payload.instruction,
                mode=payload.mode,
            )
        except ReviewExtractionError as exc:
            raise BusinessError(str(exc)) from exc
        return ReviewCardRewritePreview(
            cardId=card.id,
            mode=payload.mode,
            original=ReviewCardContent(question=card.question, answer=card.answer, hint=card.hint),
            proposed=ReviewCardContent(
                question=candidate.question,
                answer=candidate.answer,
                hint=candidate.hint,
            ),
            evidenceRefs=list(candidate.evidence_refs),
            modelName=candidate.model_name,
        )

    def preview_material_rewrite(
        self,
        material_id: int,
        payload: ReviewMaterialRewriteRequest,
        user_id: str,
    ) -> ReviewMaterialRewritePreview:
        """读取资料全部活动卡片并生成指定数量的候选，不修改数据库。"""
        with self.repository.transaction() as transaction:
            material = transaction.find_material(material_id, user_id)
            if material is None:
                raise BusinessError("学习资料不存在")
            if transaction.is_material_excluded(material_id, user_id):
                raise BusinessError("该资料已从复习中心移除")
            cards = transaction.list_active_cards_for_material(material_id, user_id)
            evidences = transaction.list_evidences(material)
            review_record = transaction.find_review_material(material_id, user_id)
        if not cards:
            raise BusinessError("该资料暂无可合并的活动卡片")
        target_card_count = infer_material_rewrite_card_count(
            payload.instruction,
            payload.targetCardCount,
            base_card_count=len(payload.baseCards) or 1,
        )
        base_cards = list(payload.baseCards)
        if base_cards and target_card_count <= len(base_cards):
            raise BusinessError(
                f"已要求保留 {len(base_cards)} 张本次候选；新增卡片时目标总数必须大于 {len(base_cards)}"
            )
        try:
            candidate = self.card_rewriter.rewrite_material(
                LearningMaterialContext(
                    material_id=material.id,
                    title=material.title,
                    document_type=material.document_type,
                    summary=(review_record.summary if review_record and review_record.summary else material.document_summary),
                ),
                cards,
                evidences,
                instruction=payload.instruction,
                mode=payload.mode,
                target_card_count=target_card_count,
                base_cards=[
                    {
                        "question": item.content.question,
                        "answer": item.content.answer,
                        "hint": item.content.hint,
                        "evidenceIds": item.evidenceIds,
                    }
                    for item in base_cards
                ],
            )
        except ReviewExtractionError as exc:
            raise BusinessError(str(exc)) from exc
        generated_snapshots = [
            ReviewMaterialCardSnapshot(
                cardId=None,
                content=ReviewCardContent(question=item.question, answer=item.answer, hint=item.hint),
                evidenceRefs=list(item.evidence_refs),
                evidenceIds=[evidence.evidenceId for evidence in item.evidence_refs],
            )
            for item in candidate.cards
        ]
        proposed_cards = generated_snapshots
        if base_cards:
            # 前端把上一轮候选作为 baseCards 传回；服务端只采用模型返回的末尾新增卡，硬保证基础候选不被改写。
            additional_count = target_card_count - len(base_cards)
            proposed_cards = [
                item.model_copy(update={"cardId": None})
                for item in base_cards
            ] + generated_snapshots[-additional_count:]
        return ReviewMaterialRewritePreview(
            materialId=material.id,
            title=material.title,
            sourceVersion=material.index_request_version,
            originalFingerprint=material_cards_fingerprint(
                cards,
                review_record.summary if review_record else material.document_summary,
            ),
            originalCardIds=[card.id for card in cards],
            originalCards=[material_card_snapshot(card) for card in cards],
            proposedCards=proposed_cards,
            targetCardCount=len(proposed_cards),
            originalSummary=(review_record.summary if review_record and review_record.summary else material.document_summary),
            proposedSummary=candidate.summary or (review_record.summary if review_record and review_record.summary else material.document_summary),
            mergeNote=candidate.merge_note or f"已将 {len(cards)} 张原卡片重组为 {len(proposed_cards)} 张候选卡片",
            mode=payload.mode,
            modelName=candidate.model_name,
        )

    def get_segment_workspace(self, material_id: int, user_id: str) -> ReviewSegmentWorkspace:
        """读取当前资料的原始分段和正式卡片版本，供用户选择生成范围。"""
        material, cards, evidences, review_record = self._load_segment_material(material_id, user_id)
        segments = split_review_evidence_segments(clean_review_evidences(deduplicate_evidences(evidences)))
        if not segments:
            raise BusinessError("该资料暂无可用于分段生成的原始 evidence")
        return ReviewSegmentWorkspace(
            materialId=material.id,
            title=material.title,
            sourceVersion=material.index_request_version,
            originalFingerprint=material_cards_fingerprint(
                cards,
                review_record.summary if review_record else material.document_summary,
            ),
            originalCardIds=[card.id for card in cards],
            originalCards=[material_card_snapshot(card) for card in cards],
            originalSummary=(
                review_record.summary
                if review_record and review_record.summary
                else material.document_summary
            ),
            segments=[
                build_review_segment(segment, index=index, total=len(segments), material_id=material.id, version=material.index_request_version)
                for index, segment in enumerate(segments, start=1)
            ],
        )

    def generate_selected_segments(
        self,
        material_id: int,
        user_id: str,
        segment_ids: list[str],
        prompts: dict[str, str],
        mode: str = "RELAXED",
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> ReviewSegmentGenerationResult:
        """只生成用户勾选的分段，单段失败不影响其他段返回。"""
        material, _cards, raw_evidences, _review_record = self._load_segment_material(material_id, user_id)
        evidences = clean_review_evidences(deduplicate_evidences(raw_evidences))
        segments = split_review_evidence_segments(evidences)
        if not segments:
            raise BusinessError("该资料暂无可用于分段生成的原始 evidence")
        segment_models = [
            build_review_segment(segment, index=index, total=len(segments), material_id=material.id, version=material.index_request_version)
            for index, segment in enumerate(segments, start=1)
        ]
        segment_map = {
            item.segmentId: (item, segment)
            for item, segment in zip(segment_models, segments, strict=True)
        }
        unknown_ids = [item for item in segment_ids if item not in segment_map]
        if unknown_ids:
            raise BusinessError("所选资料分段已过期，请重新打开分段工作台")
        normalized_mode = str(mode or "RELAXED").strip().upper()
        if normalized_mode not in {"STANDARD", "RELAXED"}:
            normalized_mode = "RELAXED"
        total = len(segment_ids)
        segment_timeout_seconds = configured_review_segment_timeout_seconds()
        segment_request_timeout_seconds = configured_review_segment_request_timeout_seconds()
        segment_progress_lock = Lock()
        local_progress = {segment_id: 0 for segment_id in segment_ids}
        selected_positions = {
            segment_id: position for position, segment_id in enumerate(segment_ids, start=1)
        }
        completed_segment_ids: set[str] = set()

        def emit_segment_progress(segment_id: str, event: dict[str, object]) -> None:
            """把单段模型进度聚合为整轮单调进度，并保留模型轮次等诊断字段。"""
            if progress_callback is None:
                return
            segment_model, _segment = segment_map[segment_id]
            try:
                reported_percent = int(event.get("percent") or 0)
            except (TypeError, ValueError):
                reported_percent = 0
            with segment_progress_lock:
                local_progress[segment_id] = max(
                    local_progress[segment_id],
                    max(0, min(100, reported_percent)),
                )
                completed_count = len(completed_segment_ids)
                # 5% 留给资料校验，95% 以后留给任务收尾；取所有段平均值可避免并行回调导致进度倒退。
                overall_percent = min(
                    94,
                    5 + round(sum(local_progress.values()) / max(1, total) * 0.89),
                )
            stage_label = str(event.get("stageLabel") or "生成候选")
            message = str(event.get("message") or "正在生成本段复习卡片")
            selected_position = selected_positions[segment_id]
            try:
                progress_callback(
                    {
                        **event,
                        "stageCode": str(event.get("stageCode") or "review.segment.generate"),
                        "stageLabel": f"原文第 {segment_model.segmentIndex} 段 · {stage_label}",
                        "message": f"本轮第 {selected_position}/{total} 个（原文第 {segment_model.segmentIndex} 段）：{message}",
                        "percent": overall_percent,
                        "currentSegmentId": segment_id,
                        "currentSegmentIndex": segment_model.segmentIndex,
                        "totalSegments": total,
                        "completedSegments": completed_count,
                    }
                )
            except Exception:  # noqa: BLE001 - 页面轮询断开不能影响模型生成。
                logger.debug("分段进度回调已断开: material_id=%s segment_id=%s", material.id, segment_id)

        def mark_segment_finished(segment_id: str, result: ReviewSegmentResult) -> None:
            """记录单段终态并立即上报，供并行任务继续计算整体百分比。"""
            with segment_progress_lock:
                completed_segment_ids.add(segment_id)
                local_progress[segment_id] = 100
                completed_count = len(completed_segment_ids)
                overall_percent = min(94, 5 + round(sum(local_progress.values()) / max(1, total) * 0.89))
            if progress_callback:
                try:
                    progress_callback(
                        {
                            "stageCode": "review.segment.completed" if result.status == "SUCCEEDED" else "review.segment.failed",
                            "stageLabel": f"第 {result.segmentIndex} 段{'已完成' if result.status == 'SUCCEEDED' else '未通过'}",
                            "message": (
                                f"已完成 {completed_count}/{total} 个所选分段，本段生成 {len(result.cards)} 张候选卡片"
                                if result.status == "SUCCEEDED"
                                else f"已完成 {completed_count}/{total} 个所选分段，本段可调整提示词后单独重试"
                            ),
                            "percent": overall_percent,
                            "currentSegmentId": segment_id,
                            "currentSegmentIndex": result.segmentIndex,
                            "totalSegments": total,
                            "completedSegments": completed_count,
                            "detail": (result.error or "本段已通过 evidence 质量门禁"),
                        }
                    )
                except Exception:  # noqa: BLE001 - 页面轮询断开不能影响任务收敛。
                    logger.debug("分段终态进度回调已断开: material_id=%s segment_id=%s", material.id, segment_id)

        def generate_one(
            segment_id: str,
            execution_budget: ReviewExecutionBudget,
        ) -> ReviewSegmentResult:
            """在线程池中独立生成一段，异常只影响当前段。"""
            segment_model, segment = segment_map[segment_id]
            prompt = " ".join(str(prompts.get(segment_id) or "").split()).strip()

            def failed_result(
                error: Exception,
                *,
                feedback: list[str] | tuple[str, ...],
                last_valid_result: object | None = None,
            ) -> ReviewSegmentResult:
                """保留单卡门禁通过但多卡未收敛的最后候选，交给用户逐卡决策。"""
                candidate_points = tuple(getattr(last_valid_result, "knowledge_points", ()) or ())
                candidate_cards = [knowledge_point_snapshot(point) for point in candidate_points]
                return ReviewSegmentResult(
                    segmentId=segment_id,
                    segmentIndex=segment_model.segmentIndex,
                    title=segment_model.title,
                    status="FAILED",
                    cards=candidate_cards,
                    candidateAvailable=bool(candidate_cards),
                    summary=getattr(last_valid_result, "summary", None),
                    qualityFeedback=list(feedback)[:80],
                    error=str(error),
                )

            try:
                execution_budget.ensure_active(f"原文第 {segment_model.segmentIndex} 段")
                result = self.extractor.extract(
                    LearningMaterialContext(
                        material_id=material.id,
                        title=material.title,
                        document_type=material.document_type,
                        summary=material.document_summary,
                    ),
                    segment,
                    user_feedback=prompt or "请模拟真实面试官提问，完整保留本段独立知识点。",
                    generation_mode=normalized_mode,
                    progress_callback=lambda event: emit_segment_progress(segment_id, event),
                    execution_budget=execution_budget,
                )
                if not result.knowledge_points:
                    raise ReviewExtractionError("本段没有生成通过 evidence 门禁的卡片")
                return ReviewSegmentResult(
                    segmentId=segment_id,
                    segmentIndex=segment_model.segmentIndex,
                    title=segment_model.title,
                    status="SUCCEEDED",
                    summary=result.summary,
                    cards=[knowledge_point_snapshot(point) for point in result.knowledge_points],
                    candidateAvailable=True,
                    qualityFeedback=list(result.quality_feedback),
                )
            except (ReviewManualReviewRequired, ReviewExtractionError) as exc:
                feedback = list(getattr(exc, "quality_feedback", ()) or getattr(exc, "diagnostics", ()) or [str(exc)])
                return failed_result(
                    exc,
                    feedback=feedback,
                    last_valid_result=getattr(exc, "last_valid_result", None),
                )

            except Exception as exc:  # noqa: BLE001 - 单段模型异常必须收敛为可重试结果。
                logger.exception("交互式分段生成异常: material_id=%s segment_id=%s", material.id, segment_id)
                return ReviewSegmentResult(
                    segmentId=segment_id,
                    segmentIndex=segment_model.segmentIndex,
                    title=segment_model.title,
                    status="FAILED",
                    candidateAvailable=False,
                    qualityFeedback=["本段模型调用出现异常，可调整提示词后重试"],
                    error=f"本段生成失败：{exc.__class__.__name__}",
                )

        results: list[ReviewSegmentResult] = []
        worker_count = min(total, configured_io_workers("LLM_IO_MAX_WORKERS"))
        futures: dict[object, tuple[str, ReviewExecutionBudget]] = {}

        def submit_segment(segment_id: str) -> None:
            """提交一段并记录独立截止时间，排队时间也计入用户可感知预算。"""
            started_at = monotonic()
            execution_budget = ReviewExecutionBudget.start(
                segment_timeout_seconds,
                segment_request_timeout_seconds,
                started_at=started_at,
            )
            future = _review_segment_extract_executor.submit(
                generate_one,
                segment_id,
                execution_budget,
            )
            futures[future] = (segment_id, execution_budget)

        for segment_id in segment_ids[:worker_count]:
            submit_segment(segment_id)
        # 选择数量受请求 schema 限制，但仍以稳定的共享池为上限，避免多资料同时生成时嵌套线程爆炸。
        pending_ids = segment_ids[worker_count:]
        while futures:
            next_deadline = min(
                execution_budget.deadline
                for _segment_id, execution_budget in futures.values()
            )
            wait_timeout = max(0.0, min(0.25, next_deadline - monotonic()))
            done, _not_done = wait(
                tuple(futures),
                timeout=wait_timeout,
                return_when=FIRST_COMPLETED,
            )
            now = monotonic()
            expired = {
                future
                for future, (_segment_id, execution_budget) in futures.items()
                if future not in done and now >= execution_budget.deadline
            }
            for future in [*done, *expired]:
                item = futures.pop(future, None)
                if item is None:
                    continue
                segment_id, execution_budget = item
                segment_model, _segment = segment_map[segment_id]
                if future in expired:
                    execution_budget.cancel("本段总执行预算已耗尽")
                    future.cancel()
                    segment_result = ReviewSegmentResult(
                        segmentId=segment_id,
                        segmentIndex=segment_model.segmentIndex,
                        title=segment_model.title,
                        status="FAILED",
                        qualityFeedback=[
                            "本段超过执行时间预算，已阻止后续模型请求；可稍后单独重试本段",
                        ],
                        error=(
                            f"本段生成超过 {int(segment_timeout_seconds)} 秒，已从本轮任务中释放；"
                            f"单次模型请求上限 {int(segment_request_timeout_seconds)} 秒"
                        ),
                    )
                else:
                    segment_result = future.result()
                results.append(segment_result)
                mark_segment_finished(segment_id, segment_result)
                if pending_ids:
                    submit_segment(pending_ids.pop(0))
        results.sort(key=lambda item: item.segmentIndex)
        return ReviewSegmentGenerationResult(
            materialId=material.id,
            sourceVersion=material.index_request_version,
            segments=results,
        )

    def _load_segment_material(
        self,
        material_id: int,
        user_id: str,
    ) -> tuple[MaterialSourceRecord, list[ReviewCardRecord], list[Evidence], ReviewMaterialRecord | None]:
        """在一次只读事务中读取分段、卡片和并发合并所需的资料基线。"""
        with self.repository.transaction() as transaction:
            material = transaction.find_material(material_id, user_id)
            if material is None:
                raise BusinessError("学习资料不存在")
            if transaction.is_material_excluded(material_id, user_id):
                raise BusinessError("该资料已从复习中心移除")
            return (
                material,
                transaction.list_active_cards_for_material(material_id, user_id),
                transaction.list_evidences(material),
                transaction.find_review_material(material_id, user_id),
            )

    def apply_material_rewrite(
        self,
        material_id: int,
        payload: ReviewMaterialRewriteApplyRequest,
        user_id: str,
    ) -> ReviewMaterialRewriteApplyResult:
        """校验预览版本后，在一个事务中替换资料的全部活动卡片。"""
        if len(payload.proposedCards) < 1:
            raise BusinessError("资料级改写最终至少需要确认 1 张卡片")
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            material = transaction.find_material(material_id, user_id)
            if material is None:
                raise BusinessError("学习资料不存在")
            if transaction.is_material_excluded(material_id, user_id):
                raise BusinessError("该资料已从复习中心移除")
            if material.index_request_version != payload.sourceVersion:
                raise BusinessError("资料内容已更新，请重新生成对比预览")
            current_cards = transaction.list_active_cards_for_material(material_id, user_id)
            current_ids = sorted(card.id for card in current_cards)
            if current_ids != sorted(payload.originalCardIds):
                raise BusinessError("资料卡片已被其他操作修改，请重新生成对比预览")
            current_review_record = transaction.find_review_material(material_id, user_id)
            current_summary = current_review_record.summary if current_review_record else material.document_summary
            if material_cards_fingerprint(current_cards, current_summary) != payload.originalFingerprint:
                raise BusinessError("资料卡片内容已被其他操作修改，请重新生成对比预览")
            evidence_by_id = {item.evidenceId: item for item in transaction.list_evidences(material)}
            drafts: list[ReviewCardDraft] = []
            for candidate in payload.proposedCards:
                evidence_refs: list[Evidence] = []
                if candidate.evidenceIds is not None:
                    unknown_ids = [item for item in candidate.evidenceIds if item not in evidence_by_id]
                    if unknown_ids:
                        raise BusinessError("候选卡片引用的原文证据不存在")
                    evidence_refs = [evidence_by_id[item] for item in candidate.evidenceIds]
                if candidate.rewriteMode == "STRICT_SOURCE":
                    if not evidence_refs or not answer_is_grounded(candidate.answer, tuple(evidence_refs)):
                        raise BusinessError("严格依赖原文的候选卡片未通过 evidence 忠实度校验")
                evidence_refs_json = json.dumps(
                    [item.model_dump(mode="json") for item in evidence_refs],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                drafts.append(
                    ReviewCardDraft(
                        # 资料级确认属于用户编辑内容，后续索引同步不得把它当作普通 AI 卡片停用。
                        source_key=f"custom:material-rewrite:{material.id}:{uuid4().hex[:20]}",
                        question=candidate.question,
                        answer=candidate.answer,
                        hint=candidate.hint,
                        evidence_refs_json=evidence_refs_json,
                        fsrs_card_json=FsrsReviewScheduler().new_card_json(now),
                        due_at=now,
                    )
                )
            replaced = transaction.replace_active_cards_for_material(
                material,
                payload.originalCardIds,
                drafts,
                summary=payload.proposedSummary,
            )
            if replaced is None:
                raise BusinessError("资料卡片替换失败，请刷新后重试")
            record = transaction.find_review_material(material_id, user_id)
            settings = transaction.get_or_create_settings(user_id)
        if record is None:
            raise BusinessError("保存资料改写后无法读取资料状态")
        return ReviewMaterialRewriteApplyResult(
            material=material_response(record),
            cards=[card_response(card, FsrsReviewScheduler(settings.desired_retention), now, include_answer=True) for card in replaced],
            replacedCardIds=list(payload.originalCardIds),
        )

    def apply_segment_cards(
        self,
        material_id: int,
        payload: ReviewSegmentMergeRequest,
        user_id: str,
    ) -> ReviewMaterialRewriteApplyResult:
        """重新校验用户编辑候选，并原子发布交互式分段结果。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            material = transaction.find_material(material_id, user_id)
            if material is None:
                raise BusinessError("学习资料不存在")
            if transaction.is_material_excluded(material_id, user_id):
                raise BusinessError("该资料已从复习中心移除")
            if material.index_request_version != payload.sourceVersion:
                raise BusinessError("资料内容已更新，请重新打开分段工作台")
            current_cards = transaction.list_active_cards_for_material(material_id, user_id)
            current_ids = sorted(card.id for card in current_cards)
            if current_ids != payload.originalCardIds:
                raise BusinessError("资料卡片已被其他操作修改，请重新打开分段工作台")
            current_review = transaction.find_review_material(material_id, user_id)
            current_summary = current_review.summary if current_review else material.document_summary
            if material_cards_fingerprint(current_cards, current_summary) != payload.originalFingerprint:
                raise BusinessError("资料卡片内容已被其他操作修改，请重新打开分段工作台")
            evidence_by_id = {item.evidenceId: item for item in transaction.list_evidences(material)}
            drafts: list[ReviewCardDraft] = []
            for candidate in payload.proposedCards:
                evidence_ids = candidate.evidenceIds or []
                if not evidence_ids:
                    raise BusinessError("分段候选卡片必须保留真实 evidence 引用")
                unknown_ids = [item for item in evidence_ids if item not in evidence_by_id]
                if unknown_ids:
                    raise BusinessError("分段候选卡片引用的原文证据不存在")
                evidence_refs = tuple(evidence_by_id[item] for item in evidence_ids)
                if not answer_is_grounded(candidate.answer, evidence_refs):
                    raise BusinessError("编辑后的分段候选未通过 evidence 忠实度校验")
                drafts.append(
                    ReviewCardDraft(
                        source_key=f"custom:segment-workspace:{material.id}:{uuid4().hex[:20]}",
                        question=candidate.question,
                        answer=candidate.answer,
                        hint=candidate.hint,
                        evidence_refs_json=json.dumps(
                            [item.model_dump(mode="json") for item in evidence_refs],
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        fsrs_card_json=FsrsReviewScheduler().new_card_json(now),
                        due_at=now,
                    )
                )
            published = transaction.publish_segment_cards_for_material(
                material,
                payload.originalCardIds,
                drafts,
                summary=payload.proposedSummary,
            )
            if published is None:
                raise BusinessError("分段候选发布失败，请刷新后重试")
            record = transaction.find_review_material(material_id, user_id)
            settings = transaction.get_or_create_settings(user_id)
        if record is None:
            raise BusinessError("发布分段卡片后无法读取资料状态")
        return ReviewMaterialRewriteApplyResult(
            material=material_response(record),
            cards=[
                card_response(
                    card,
                    FsrsReviewScheduler(settings.desired_retention),
                    now,
                    include_answer=True,
                )
                for card in published
            ],
            replacedCardIds=list(payload.originalCardIds),
        )

    def update_card(
        self,
        card_id: int,
        payload: ReviewCardUpdateRequest,
        user_id: str,
    ) -> ReviewCard:
        """应用人工或 AI 编辑，保留卡片 FSRS 排程、评分日志和到期时间。"""
        now = as_utc(self.now_provider())
        with self.repository.transaction() as transaction:
            card = transaction.find_card_for_update(card_id, user_id)
            if card is None:
                raise BusinessError("复习卡片不存在")
            evidence_refs: list[Evidence] | None = None
            if payload.evidenceIds is not None:
                material = transaction.find_material(card.material_id, user_id)
                if material is None:
                    raise BusinessError("学习资料不存在")
                evidence_by_id = {
                    item.evidenceId: item
                    for item in transaction.list_evidences(material)
                }
                unknown_ids = [item for item in payload.evidenceIds if item not in evidence_by_id]
                if unknown_ids:
                    raise BusinessError("卡片引用的原文证据不存在")
                evidence_refs = [evidence_by_id[item] for item in payload.evidenceIds]
            if payload.rewriteMode == "STRICT_SOURCE":
                if not evidence_refs:
                    raise BusinessError("严格依赖原文的卡片必须保留真实 evidence 引用")
                if not answer_is_grounded(payload.answer, tuple(evidence_refs)):
                    raise BusinessError("编辑后的答案未通过严格原文忠实度校验")
            evidence_refs_json = (
                json.dumps(
                    [item.model_dump(mode="json") for item in evidence_refs],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if evidence_refs is not None
                else None
            )
            updated = transaction.update_card_content(
                card_id,
                user_id,
                question=payload.question,
                answer=payload.answer,
                hint=payload.hint,
                evidence_refs_json=evidence_refs_json,
            )
            settings = transaction.get_or_create_settings(user_id)
        if updated is None:
            raise BusinessError("复习卡片不存在")
        return card_response(updated, FsrsReviewScheduler(settings.desired_retention), now, include_answer=True)

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

    def _generate(
        self,
        material: MaterialSourceRecord,
        user_id: str,
        *,
        force: bool,
        user_feedback: str | None = None,
        generation_mode: str = "STANDARD",
    ) -> ReviewMaterial | None:
        """读取 evidence 后运行复习生成图，再用资料级短事务更新结果。"""
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
            self._record_generation_progress(
                material,
                {
                    "stageCode": "review.evidence.load",
                    "stageLabel": "读取证据",
                    "message": "正在读取当前索引版本的 RAG evidence",
                    "status": "RUNNING",
                    "currentStep": 1,
                    "totalSteps": 4,
                    "percent": 6,
                    "attempt": 0,
                },
            )
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
                    reason="资料暂无可用 evidence，无法调用 gpt-5.6-terra 生成复习内容",
                    extractor=f"failed:{REVIEW_CARD_PROMPT_VERSION}",
                    cards=[],
                    generation_attempts=0,
                    quality_feedback=["资料暂无可用 evidence，无法执行模型生成与质量校验"],
                )
            try:
                context = LearningMaterialContext(
                    material_id=material.id,
                    title=material.title,
                    document_type=material.document_type,
                    summary=material.document_summary,
                )
                if isinstance(self.extractor, KnowledgePointExtractor):
                    extraction = self.extractor.extract(
                        context,
                        evidences,
                        user_feedback=user_feedback,
                        generation_mode=generation_mode,
                        progress_callback=lambda event: self._record_generation_progress(material, event),
                    )
                else:
                    # 测试替身和外部扩展提取器继续兼容原有双参数调用方式。
                    extraction = (
                        self.extractor.extract(context, evidences, user_feedback=user_feedback)
                        if (user_feedback or "").strip()
                        else self.extractor.extract(context, evidences)
                    )
            except ReviewManualReviewRequired as exc:
                return self._save_generation(
                    material,
                    is_learning_content=True,
                    category=None,
                    summary=None,
                    status="NEEDS_REVIEW",
                    reason=str(exc),
                    extractor=f"failed:{REVIEW_CARD_PROMPT_VERSION}",
                    cards=[],
                    generation_attempts=exc.attempts,
                    quality_feedback=list(exc.quality_feedback),
                )
            except ReviewExtractionError as exc:
                # 失败只保存诊断，Repository 会保留上一个已经发布的可用版本。
                return self._save_generation(
                    material,
                    is_learning_content=None,
                    category=None,
                    summary=None,
                    status="FAILED",
                    reason=str(exc),
                    extractor=f"failed:{REVIEW_CARD_PROMPT_VERSION}",
                    cards=[],
                    generation_attempts=0,
                    quality_feedback=list(exc.diagnostics),
                )
            except Exception as exc:  # noqa: BLE001 - 未预期错误也必须收敛为可重试终态。
                logger.exception("复习内容生成发生未预期错误，materialId=%s", material.id)
                safe_reason = f"复习生成遇到未预期错误（{type(exc).__name__}），请稍后重新生成"
                return self._save_generation(
                    material,
                    is_learning_content=None,
                    category=None,
                    summary=None,
                    status="FAILED",
                    reason=safe_reason,
                    extractor=f"failed:{REVIEW_CARD_PROMPT_VERSION}",
                    cards=[],
                    generation_attempts=0,
                    quality_feedback=[safe_reason],
                )
            generation_attempts = max(0, int(getattr(extraction, "generation_attempts", 0) or 0))
            quality_feedback = list(getattr(extraction, "quality_feedback", ()) or ())
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
                    generation_attempts=generation_attempts,
                    quality_feedback=quality_feedback,
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
                    generation_attempts=generation_attempts,
                    quality_feedback=quality_feedback,
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
                generation_attempts=generation_attempts,
                quality_feedback=quality_feedback,
            )
        finally:
            lock.release()

    def _record_generation_progress(
        self,
        material: MaterialSourceRecord,
        event: dict[str, object],
    ) -> None:
        """持久化一条真实阶段事件；进度写入失败不能破坏卡片主流程。"""
        payload = dict(event)
        payload.setdefault("createdAt", as_utc(self.now_provider()).isoformat())
        try:
            with self.repository.transaction() as transaction:
                save_progress = getattr(transaction, "save_generation_progress", None)
                if callable(save_progress):
                    save_progress(material, payload)
        except Exception:  # noqa: BLE001 - 可观测性失败只记录日志，最终生成仍需收敛。
            logger.exception("复习生成进度保存失败，materialId=%s", material.id)

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
        generation_attempts: int = 0,
        quality_feedback: list[str] | None = None,
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
                generation_attempts=generation_attempts,
                quality_feedback=quality_feedback or [],
                generation_progress_event=terminal_generation_progress_event(
                    status,
                    reason,
                    generation_attempts,
                    at=as_utc(self.now_provider()),
                ),
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
        sourceType="MANUAL" if record.source_key.startswith("manual:") else "RAG",
        answer=record.answer if include_answer else None,
        # 提示不等于答案，允许用户在揭示前选择查看。
        hint=record.hint,
        evidenceRefs=references,
        dueAt=record.due_at,
        retrievability=retrievability,
        reviewCount=record.review_count,
        lapseCount=record.lapse_count,
        isUserEdited=record.source_key.startswith("custom:"),
        updatedAt=record.updated_at,
    )


def material_card_snapshot(record: ReviewCardRecord) -> ReviewMaterialCardSnapshot:
    """将带答案的现有卡片转换为资料级对比快照。"""
    references: list[Evidence] = []
    try:
        raw_references = json.loads(record.evidence_refs_json)
        references = [Evidence.model_validate(item) for item in raw_references]
    except (TypeError, ValueError):
        references = []
    return ReviewMaterialCardSnapshot(
        cardId=record.id,
        content=ReviewCardContent(question=record.question, answer=record.answer, hint=record.hint),
        evidenceRefs=references,
        evidenceIds=[item.evidenceId for item in references],
    )


def material_cards_fingerprint(cards: list[ReviewCardRecord], summary: str | None = None) -> str:
    """为资料当前卡片正文生成并发校验指纹，避免陈旧预览覆盖新编辑。"""
    payload = {
        "summary": summary,
        "cards": [
            {
                "id": card.id,
                "question": card.question,
                "answer": card.answer,
                "hint": card.hint,
                "evidenceRefs": card.evidence_refs_json,
            }
            for card in sorted(cards, key=lambda item: item.id)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_review_segment(
    evidences: list[Evidence],
    *,
    index: int,
    total: int,
    material_id: int,
    version: int,
) -> ReviewEvidenceSegment:
    """把连续 evidence 转为稳定分段 ID、可读标题和原始内容预览。"""
    evidence_ids = [item.evidenceId for item in evidences]
    digest = hashlib.sha256(
        f"{material_id}:{version}:{'|'.join(evidence_ids)}".encode("utf-8")
    ).hexdigest()[:24]
    locations: list[str] = []
    for item in evidences:
        location = item.sectionName or item.sectionTitle or item.startTime or ""
        if location and location not in locations:
            locations.append(location)
    if locations:
        title = "、".join(locations[:2])
        if len(locations) > 2:
            title += "等"
    else:
        title = f"第 {index} 段"
    raw_parts = []
    for item in evidences:
        location = item.sectionName or item.sectionTitle or "原文片段"
        if item.startTime:
            location = f"{location}（{item.startTime}-{item.endTime or item.startTime}）"
        raw_parts.append(f"[{item.evidenceId}] {location}\n{item.snippet}")
    raw_content = "\n\n".join(raw_parts).strip()
    return ReviewEvidenceSegment(
        segmentId=f"segment-{digest}",
        segmentIndex=index,
        totalSegments=total,
        title=title[:240],
        characterCount=len(raw_content),
        evidenceCount=len(evidences),
        rawContent=raw_content[:30000],
        evidenceRefs=evidences,
    )


def knowledge_point_snapshot(point: Any) -> ReviewMaterialCardSnapshot:
    """把模型知识点候选转换为可编辑、仍携带 evidence 的工作台卡片。"""
    return ReviewMaterialCardSnapshot(
        cardId=None,
        content=ReviewCardContent(question=point.question, answer=point.answer, hint=point.hint),
        evidenceRefs=list(point.evidence_refs),
        evidenceIds=[item.evidenceId for item in point.evidence_refs],
    )


def material_generation_is_current(record: ReviewMaterialRecord, index_request_version: int) -> bool:
    """按资料索引版本和持久化终态判定幂等，服务或 Prompt 升级不改写旧卡片。"""
    return (
        record.synced_index_request_version is not None
        and record.synced_index_request_version >= index_request_version
        and record.status in {"GENERATED", "SKIPPED", "FAILED", "NEEDS_REVIEW"}
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
        folderId=record.folder_id,
        folderName=record.folder_name,
        indexRequestVersion=record.index_request_version,
        syncedIndexRequestVersion=record.synced_index_request_version,
        updatedAt=record.updated_at,
        generationAttempts=record.generation_attempts,
        qualityFeedback=list(record.quality_feedback),
        generationProgress=generation_progress_response(record.generation_progress),
        needsManualReview=record.status == "NEEDS_REVIEW",
    )


def generation_progress_response(value: dict[str, object] | None) -> ReviewGenerationProgress | None:
    """把数据库 JSONB 快照转换为公开模型，损坏旧值按无进度处理。"""
    if not value:
        return None
    try:
        return ReviewGenerationProgress.model_validate(value)
    except Exception:  # noqa: BLE001 - 历史异常 JSON 不应阻断资料列表。
        logger.warning("检测到无法解析的复习生成进度快照，已忽略")
        return None


def terminal_generation_progress_event(
    status: str,
    reason: str,
    attempts: int,
    *,
    at: datetime,
) -> dict[str, object]:
    """为生成、跳过、失败和人工处理构造稳定终态事件。"""
    mapping = {
        "GENERATED": ("review.completed", "生成完成", "复习卡片已保存，FSRS 排程已初始化", "COMPLETED"),
        "SKIPPED": ("review.skipped", "已跳过", reason or "资料不属于复习内容", "SKIPPED"),
        "NEEDS_REVIEW": ("review.human_review", "等待人工处理", reason, "NEEDS_REVIEW"),
        "FAILED": ("review.failed", "生成失败", reason, "FAILED"),
    }
    stage_code, stage_label, message, progress_status = mapping.get(
        status,
        ("review.completed", "处理完成", reason or "复习资料处理完成", status),
    )
    return {
        "stageCode": stage_code,
        "stageLabel": stage_label,
        "message": message,
        "status": progress_status,
        "currentStep": 4,
        "totalSteps": 4,
        "percent": 100,
        "attempt": max(0, int(attempts)),
        "createdAt": at.isoformat(),
    }


def generation_mode_label(value: str) -> str:
    """把生成模式转换为用户可理解的中文阶段说明。"""
    return {
        "RELAXED": "宽松门禁重新生成",
        "SEGMENTED": "分段生成并合并",
    }.get(str(value or "").upper(), "标准门禁重新生成")


def folder_response(record: ReviewFolderRecord) -> ReviewFolder:
    """把文件夹聚合记录转换为公开响应。"""
    return ReviewFolder(
        id=record.id,
        name=record.name,
        materialCount=record.material_count,
        cardCount=record.card_count,
        dueCardCount=record.due_card_count,
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
