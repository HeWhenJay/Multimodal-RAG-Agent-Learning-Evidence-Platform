"""学习资料复习与提醒公开路由。"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, TypeVar
from uuid import uuid4

from fastapi import APIRouter, Depends, Query

from app.core.current_user import CurrentUser
from app.core.io_concurrency import configured_io_workers
from app.core.result import BusinessError, Result
from app.review.card_rewriter import infer_material_rewrite_card_count
from app.review.generation_guard import ReviewGenerationGuard
from app.review.service import ReviewService
from app.schemas.review import (
    ReviewCard,
    ReviewBatchDeletionResult,
    ReviewCardBatchDeleteRequest,
    ReviewCardLibrary,
    ReviewCardRewritePreview,
    ReviewCardRewriteRequest,
    ReviewCardRewriteTask,
    ReviewCardUpdateRequest,
    ReviewMaterialRewriteApplyRequest,
    ReviewMaterialRewriteApplyResult,
    ReviewMaterialRewritePreview,
    ReviewMaterialRewriteRequest,
    ReviewMaterialRewriteTask,
    ReviewSegmentGenerationRequest,
    ReviewSegmentGenerationResult,
    ReviewSegmentGenerationTask,
    ReviewSegmentMergeRequest,
    ReviewSegmentWorkspace,
    ReviewDeletionResult,
    ReviewDueGroups,
    ReviewFolder,
    ReviewFolderAssignmentResult,
    ReviewFolderDeletionResult,
    ReviewFolderDetail,
    ReviewFolderMaterialOrderRequest,
    ReviewFolderNameRequest,
    ReviewGroupOrderRequest,
    ReviewGroupOrderResult,
    ReviewGradeRequest,
    ReviewGradeResult,
    ReviewMaterial,
    ReviewMaterialBatchDeleteRequest,
    ReviewMaterialFolderRequest,
    ReviewGenerationRequest,
    ReviewManualCardRequest,
    ReviewMissingKnowledgeRequest,
    ReviewMissingKnowledgeResult,
    ReviewMissingKnowledgeTask,
    ReviewOverview,
    ReviewSettings,
    ReviewSyncResult,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reviews", tags=["学习复习"])
T = TypeVar("T")
_generation_guard = ReviewGenerationGuard()
_review_generation_executor = ThreadPoolExecutor(
    max_workers=configured_io_workers("LLM_IO_MAX_WORKERS"),
    thread_name_prefix="review-generation",
)
_review_generation_jobs_lock = Lock()
_review_generation_jobs: set[tuple[str, int]] = set()
_missing_knowledge_executor = ThreadPoolExecutor(
    max_workers=configured_io_workers("LLM_IO_MAX_WORKERS"),
    thread_name_prefix="review-missing-knowledge",
)
_missing_knowledge_jobs_lock = Lock()
_missing_knowledge_jobs: dict[str, "_MissingKnowledgeJob"] = {}
_latest_missing_knowledge_jobs: dict[tuple[str, int], str] = {}
_review_rewrite_executor = ThreadPoolExecutor(
    max_workers=configured_io_workers("LLM_IO_MAX_WORKERS"),
    thread_name_prefix="review-rewrite",
)
_review_rewrite_jobs_lock = Lock()
_review_rewrite_jobs: dict[str, "_ReviewRewriteJob"] = {}
_latest_review_rewrite_jobs: dict[tuple[str, str, int], str] = {}
_review_segment_executor = ThreadPoolExecutor(
    max_workers=configured_io_workers("LLM_IO_MAX_WORKERS"),
    thread_name_prefix="review-segment-generation",
)
_review_segment_jobs_lock = Lock()
_review_segment_jobs: dict[str, "_ReviewSegmentJob"] = {}
_latest_review_segment_jobs: dict[tuple[str, int], str] = {}
_REVIEW_SEGMENT_HEARTBEAT_SECONDS = 15.0


@dataclass
class _MissingKnowledgeJob:
    """进程内保存一条补漏任务的可查询状态。"""

    task_id: str
    user_id: str
    material_id: int
    message: str
    payload: ReviewMissingKnowledgeRequest
    status: str = "QUEUED"
    progress: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    result: ReviewMissingKnowledgeResult | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _ReviewRewriteJob:
    """进程内保存一条单卡片或资料级 LLM 改写任务。"""

    task_id: str
    user_id: str
    target_kind: str
    target_id: int
    payload: ReviewCardRewriteRequest | ReviewMaterialRewriteRequest
    target_card_count: int = 1
    base_cards: tuple[Any, ...] = ()
    status: str = "QUEUED"
    progress: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    result: ReviewCardRewritePreview | ReviewMaterialRewritePreview | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class _ReviewSegmentJob:
    """保存交互式分段任务的选择、逐段提示词和后台结果。"""

    task_id: str
    user_id: str
    material_id: int
    payload: ReviewSegmentGenerationRequest
    status: str = "QUEUED"
    progress: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    result: ReviewSegmentGenerationResult | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def get_review_service() -> ReviewService:
    """提供默认复习服务，测试可替换为内存实现。"""
    # 复用同一 Redis 连接池和进程内锁，避免每个请求重复创建生成锁客户端。
    return ReviewService(generation_guard=_generation_guard)


@router.post("/sync", response_model=Result[ReviewSyncResult])
def sync_reviews(
    current_user: CurrentUser,
    limit: int = Query(default=1, ge=1, le=1),
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewSyncResult]:
    """增量扫描当前用户尚未同步的学习资料。"""
    return Result.success(execute("同步学习资料复习卡片", lambda: service.sync(str(current_user.id), limit)))


@router.get("/overview", response_model=Result[ReviewOverview])
def review_overview(
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewOverview]:
    """读取当前用户的到期数和今日复习统计。"""
    return Result.success(execute("获取复习概览", lambda: service.overview(str(current_user.id))))


@router.get("/due", response_model=Result[list[ReviewCard]])
def due_reviews(
    current_user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    service: ReviewService = Depends(get_review_service),
) -> Result[list[ReviewCard]]:
    """读取当前用户已到期的关键知识点卡片。"""
    return Result.success(execute("获取待复习卡片", lambda: service.list_due(str(current_user.id), limit)))


@router.get("/due-groups", response_model=Result[ReviewDueGroups])
def due_review_groups(
    current_user: CurrentUser,
    limit: int = Query(default=20, ge=1, le=100),
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewDueGroups]:
    """按用户上传资料分组读取今日到期卡片，答案由揭示接口单独返回。"""
    return Result.success(execute("获取分组待复习卡片", lambda: service.list_due_groups(str(current_user.id), limit)))


@router.put("/due-groups/order", response_model=Result[ReviewGroupOrderResult])
def reorder_due_review_groups(
    payload: ReviewGroupOrderRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewGroupOrderResult]:
    """批量保存当前用户今日资料分组的拖拽顺序。"""
    return Result.success(
        execute(
            "保存今日复习资料顺序",
            lambda: service.reorder_due_groups(payload.materialIds, str(current_user.id)),
        )
    )


@router.get("/materials", response_model=Result[list[ReviewMaterial]])
def review_materials(
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[list[ReviewMaterial]]:
    """读取资料分类和卡片生成状态。"""
    return Result.success(execute("获取复习资料列表", lambda: service.list_materials(str(current_user.id))))


@router.put("/materials/folder", response_model=Result[ReviewFolderAssignmentResult])
def assign_review_materials_to_folder(
    payload: ReviewMaterialFolderRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewFolderAssignmentResult]:
    """以整份文档为单位批量移入文件夹或恢复未归档。"""
    return Result.success(
        execute(
            "更新复习资料文件夹",
            lambda: service.assign_materials_to_folder(payload, str(current_user.id)),
        )
    )


@router.get("/folders", response_model=Result[list[ReviewFolder]])
def review_folders(
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[list[ReviewFolder]]:
    """读取当前用户的复习文件夹与实时卡片统计。"""
    return Result.success(execute("获取复习文件夹", lambda: service.list_folders(str(current_user.id))))


@router.post("/folders", response_model=Result[ReviewFolder])
def create_review_folder(
    payload: ReviewFolderNameRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewFolder]:
    """创建一个当前用户可见的空复习文件夹。"""
    return Result.success(
        execute("创建复习文件夹", lambda: service.create_folder(payload.name, str(current_user.id)))
    )


@router.get("/folders/{folder_id}", response_model=Result[ReviewFolderDetail])
def review_folder_detail(
    folder_id: int,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewFolderDetail]:
    """进入文件夹并按文档读取全部活动卡片，答案由揭示接口返回。"""
    return Result.success(
        execute("获取复习文件夹详情", lambda: service.get_folder(folder_id, str(current_user.id)))
    )


@router.put("/folders/{folder_id}/materials/order", response_model=Result[ReviewGroupOrderResult])
def reorder_review_folder_materials(
    folder_id: int,
    payload: ReviewFolderMaterialOrderRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewGroupOrderResult]:
    """批量保存当前用户文件夹内文档的拖拽顺序。"""
    return Result.success(
        execute(
            "保存复习文件夹文档顺序",
            lambda: service.reorder_folder_materials(folder_id, payload.materialIds, str(current_user.id)),
        )
    )


@router.patch("/folders/{folder_id}", response_model=Result[ReviewFolder])
def rename_review_folder(
    folder_id: int,
    payload: ReviewFolderNameRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewFolder]:
    """重命名文件夹且保留已有文档归属。"""
    return Result.success(
        execute(
            "重命名复习文件夹",
            lambda: service.rename_folder(folder_id, payload.name, str(current_user.id)),
        )
    )


@router.delete("/folders/{folder_id}", response_model=Result[ReviewFolderDeletionResult])
def delete_review_folder(
    folder_id: int,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewFolderDeletionResult]:
    """删除文件夹并解除文档归档，不删除资料和卡片。"""
    return Result.success(
        execute("删除复习文件夹", lambda: service.delete_folder(folder_id, str(current_user.id)))
    )


@router.post("/materials/{material_id}/generate", response_model=Result[ReviewMaterial])
def generate_review_material(
    material_id: int,
    current_user: CurrentUser,
    payload: ReviewGenerationRequest | None = None,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewMaterial]:
    """按用户选择保留当前卡片，或用指定门禁模式重新生成。"""
    user_id = str(current_user.id)
    feedback = payload.userFeedback if payload else None
    action = payload.action if payload else "REGENERATE"
    mode = payload.mode if payload else "STANDARD"
    if action == "KEEP_CURRENT":
        return Result.success(
            execute(
                "保留当前学习资料复习卡片",
                lambda: service.keep_current_generation(material_id, user_id),
            )
        )
    def prepare_generation() -> ReviewMaterial:
        """兼容旧测试替身，同时把非标准门禁档位传递给服务层。"""
        if mode == "STANDARD":
            return service.prepare_material_generation(material_id, user_id)
        return service.prepare_material_generation(material_id, user_id, generation_mode=mode)

    queued = execute("准备后台生成学习资料复习卡片", prepare_generation)
    _submit_review_generation(service, material_id, user_id, feedback, mode)
    return Result.success(
        queued
    )


@router.post(
    "/materials/{material_id}/rewrite-preview",
    response_model=Result[ReviewMaterialRewritePreview],
)
def preview_review_material_rewrite(
    material_id: int,
    payload: ReviewMaterialRewriteRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewMaterialRewritePreview]:
    """生成资料级卡片合并预览，不写入数据库。"""
    return Result.success(
        execute(
            "生成资料级复习卡片改写预览",
            lambda: service.preview_material_rewrite(material_id, payload, str(current_user.id)),
        )
    )


@router.post(
    "/materials/{material_id}/rewrite-tasks",
    response_model=Result[ReviewMaterialRewriteTask],
)
def start_review_material_rewrite_task(
    material_id: int,
    payload: ReviewMaterialRewriteRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewMaterialRewriteTask]:
    """创建资料级后台合并改写任务，并立即返回任务状态。"""
    user_id = str(current_user.id)
    with _review_rewrite_jobs_lock:
        existing = _find_active_review_rewrite_job(user_id, "MATERIAL", material_id, payload)
        if existing is not None:
            return Result.success(_material_rewrite_task_response(existing))
        job = _create_review_rewrite_job(user_id, "MATERIAL", material_id, payload)
    _review_rewrite_executor.submit(_run_review_rewrite_job, service, job)
    return Result.success(_material_rewrite_task_response(job))


@router.get(
    "/materials/{material_id}/rewrite-tasks/latest",
    response_model=Result[ReviewMaterialRewriteTask | None],
)
def latest_review_material_rewrite_task(
    material_id: int,
    current_user: CurrentUser,
) -> Result[ReviewMaterialRewriteTask | None]:
    """读取当前资料最近一次合并改写任务，供重新打开弹窗恢复。"""
    user_id = str(current_user.id)
    with _review_rewrite_jobs_lock:
        task_id = _latest_review_rewrite_jobs.get((user_id, "MATERIAL", material_id))
        job = _review_rewrite_jobs.get(task_id) if task_id else None
        return Result.success(_material_rewrite_task_response(job) if job else None)


@router.get(
    "/materials/{material_id}/rewrite-tasks/{task_id}",
    response_model=Result[ReviewMaterialRewriteTask],
)
def get_review_material_rewrite_task(
    material_id: int,
    task_id: str,
    current_user: CurrentUser,
) -> Result[ReviewMaterialRewriteTask]:
    """读取当前用户指定资料级合并改写任务。"""
    user_id = str(current_user.id)
    with _review_rewrite_jobs_lock:
        job = _review_rewrite_jobs.get(task_id)
        if job is None or not _review_rewrite_job_matches(job, user_id, "MATERIAL", material_id):
            raise BusinessError("资料改写任务不存在或已过期")
        return Result.success(_material_rewrite_task_response(job))


@router.post(
    "/materials/{material_id}/rewrite-apply",
    response_model=Result[ReviewMaterialRewriteApplyResult],
)
def apply_review_material_rewrite(
    material_id: int,
    payload: ReviewMaterialRewriteApplyRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewMaterialRewriteApplyResult]:
    """应用用户确认后的资料级改写，并原子替换活动卡片。"""
    user_id = str(current_user.id)
    applied = execute(
        "应用资料级复习卡片改写",
        lambda: service.apply_material_rewrite(material_id, payload, user_id),
    )
    _invalidate_review_rewrite_job(user_id, "MATERIAL", material_id)
    return Result.success(applied)


@router.get(
    "/materials/{material_id}/segments",
    response_model=Result[ReviewSegmentWorkspace],
)
def get_review_segment_workspace(
    material_id: int,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewSegmentWorkspace]:
    """读取当前资料可选择的原始分段和正式卡片版本。"""
    return Result.success(
        execute(
            "读取复习资料分段",
            lambda: service.get_segment_workspace(material_id, str(current_user.id)),
        )
    )


@router.post(
    "/materials/{material_id}/segment-tasks",
    response_model=Result[ReviewSegmentGenerationTask],
)
def start_review_segment_task(
    material_id: int,
    payload: ReviewSegmentGenerationRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewSegmentGenerationTask]:
    """创建用户选中分段的后台生成任务。"""
    user_id = str(current_user.id)
    with _review_segment_jobs_lock:
        existing = _find_active_review_segment_job(user_id, material_id, payload)
        if existing is not None:
            return Result.success(_review_segment_task_response(existing))
        # forceRestart 只控制本次替代动作，不参与后续同请求幂等比较。
        stored_payload = payload.model_copy(update={"forceRestart": False})
        job = _create_review_segment_job(user_id, material_id, stored_payload)
    _review_segment_executor.submit(_run_review_segment_job, service, job)
    return Result.success(_review_segment_task_response(job))


@router.get(
    "/materials/{material_id}/segment-tasks/latest",
    response_model=Result[ReviewSegmentGenerationTask | None],
)
def latest_review_segment_task(
    material_id: int,
    current_user: CurrentUser,
) -> Result[ReviewSegmentGenerationTask | None]:
    """恢复当前资料最近一次交互式分段生成任务。"""
    user_id = str(current_user.id)
    with _review_segment_jobs_lock:
        task_id = _latest_review_segment_jobs.get((user_id, material_id))
        job = _review_segment_jobs.get(task_id) if task_id else None
        return Result.success(_review_segment_task_response(job) if job else None)


@router.get(
    "/materials/{material_id}/segment-tasks/{task_id}",
    response_model=Result[ReviewSegmentGenerationTask],
)
def get_review_segment_task(
    material_id: int,
    task_id: str,
    current_user: CurrentUser,
) -> Result[ReviewSegmentGenerationTask]:
    """读取当前用户的一条交互式分段生成任务。"""
    user_id = str(current_user.id)
    with _review_segment_jobs_lock:
        job = _review_segment_jobs.get(task_id)
        if job is None or job.user_id != user_id or job.material_id != material_id:
            raise BusinessError("分段生成任务不存在或已过期")
        return Result.success(_review_segment_task_response(job))


@router.post(
    "/materials/{material_id}/segments/merge",
    response_model=Result[ReviewMaterialRewriteApplyResult],
)
def merge_review_segments(
    material_id: int,
    payload: ReviewSegmentMergeRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewMaterialRewriteApplyResult]:
    """校验用户编辑候选，并原子发布为当前资料的正式复习卡片。"""
    user_id = str(current_user.id)
    merged = execute(
        "合并分段复习卡片",
        lambda: service.apply_segment_cards(material_id, payload, user_id),
    )
    _invalidate_review_segment_job(user_id, material_id)
    return Result.success(merged)


@router.post(
    "/materials/{material_id}/cards",
    response_model=Result[ReviewCard],
)
def create_manual_review_card(
    material_id: int,
    payload: ReviewManualCardRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewCard]:
    """保存用户自定义的复习卡片，不依赖模型补漏。"""
    return Result.success(
        execute(
            "创建手动复习卡片",
            lambda: service.create_manual_card(material_id, payload, str(current_user.id)),
        )
    )


@router.post(
    "/materials/{material_id}/missing-knowledge",
    response_model=Result[ReviewMissingKnowledgeResult],
)
def supplement_review_missing_knowledge(
    material_id: int,
    payload: ReviewMissingKnowledgeRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewMissingKnowledgeResult]:
    """根据用户提示从当前文档 evidence 中只追加遗漏卡片。"""
    return Result.success(
        execute(
            "查找遗漏复习知识点",
            lambda: service.supplement_missing_knowledge(material_id, payload, str(current_user.id)),
        )
    )


@router.post(
    "/materials/{material_id}/missing-knowledge/tasks",
    response_model=Result[ReviewMissingKnowledgeTask],
)
def start_supplement_review_missing_knowledge(
    material_id: int,
    payload: ReviewMissingKnowledgeRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewMissingKnowledgeTask]:
    """创建后台补漏任务，立即返回可继续查询的任务状态。"""
    user_id = str(current_user.id)
    with _missing_knowledge_jobs_lock:
        existing = _find_active_missing_knowledge_job(user_id, material_id)
        if existing is not None:
            return Result.success(_missing_knowledge_task_response(existing))
        task_id = f"missing-knowledge-{uuid4().hex[:12]}"
        job = _MissingKnowledgeJob(
            task_id=task_id,
            user_id=user_id,
            material_id=material_id,
            message=payload.message,
            payload=payload,
        )
        _set_missing_knowledge_progress(
            job,
            status="QUEUED",
            stage_code="missing.queue",
            stage_label="后台排队",
            message="已收到补漏请求，任务将在后台继续执行",
            percent=0,
        )
        _missing_knowledge_jobs[task_id] = job
        _latest_missing_knowledge_jobs[(user_id, material_id)] = task_id
        _trim_missing_knowledge_jobs()
    _missing_knowledge_executor.submit(_run_missing_knowledge_job, service, job)
    return Result.success(_missing_knowledge_task_response(job))


@router.get(
    "/materials/{material_id}/missing-knowledge/tasks/latest",
    response_model=Result[ReviewMissingKnowledgeTask | None],
)
def latest_supplement_review_missing_knowledge(
    material_id: int,
    current_user: CurrentUser,
) -> Result[ReviewMissingKnowledgeTask | None]:
    """读取当前资料最近一次补漏任务，便于关闭弹窗后恢复进度。"""
    user_id = str(current_user.id)
    with _missing_knowledge_jobs_lock:
        task_id = _latest_missing_knowledge_jobs.get((user_id, material_id))
        job = _missing_knowledge_jobs.get(task_id) if task_id else None
        return Result.success(_missing_knowledge_task_response(job) if job else None)


@router.get(
    "/materials/{material_id}/missing-knowledge/tasks/{task_id}",
    response_model=Result[ReviewMissingKnowledgeTask],
)
def get_supplement_review_missing_knowledge(
    material_id: int,
    task_id: str,
    current_user: CurrentUser,
) -> Result[ReviewMissingKnowledgeTask]:
    """读取当前用户指定补漏任务的阶段、进度和结果。"""
    user_id = str(current_user.id)
    with _missing_knowledge_jobs_lock:
        job = _missing_knowledge_jobs.get(task_id)
        if job is None or job.user_id != user_id or job.material_id != material_id:
            raise BusinessError("补漏任务不存在或已过期")
        return Result.success(_missing_knowledge_task_response(job))


@router.post("/materials/batch-delete", response_model=Result[ReviewBatchDeletionResult])
def batch_delete_review_materials(
    payload: ReviewMaterialBatchDeleteRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewBatchDeletionResult]:
    """在单个事务中批量将资料移出复习中心。"""
    return Result.success(
        execute("批量移出复习资料", lambda: service.delete_materials(payload.materialIds, str(current_user.id)))
    )


@router.delete("/materials/{material_id}", response_model=Result[ReviewDeletionResult])
def delete_review_material(
    material_id: int,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewDeletionResult]:
    """将资料永久移出复习中心，但保留原始 RAG 文件与索引。"""
    return Result.success(
        execute("将资料移出复习中心", lambda: service.delete_material(material_id, str(current_user.id)))
    )


@router.get("/cards/library", response_model=Result[ReviewCardLibrary])
def review_card_library(
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewCardLibrary]:
    """读取当前用户全部活动卡片，包括已经复习过的卡片。"""
    return Result.success(execute("获取全量复习卡片库", lambda: service.list_card_library(str(current_user.id))))


@router.get("/cards/{card_id}", response_model=Result[ReviewCard])
def reveal_review_card(
    card_id: int,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewCard]:
    """用户主动查看答案时返回答案正文和原文 evidence。"""
    return Result.success(execute("查看复习卡片答案", lambda: service.get_card(card_id, str(current_user.id))))


@router.post("/cards/{card_id}/rewrite-preview", response_model=Result[ReviewCardRewritePreview])
def preview_review_card_rewrite(
    card_id: int,
    payload: ReviewCardRewriteRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewCardRewritePreview]:
    """按照三档来源约束生成原卡片与新卡片的无副作用对比预览。"""
    return Result.success(
        execute(
            "生成复习卡片改写预览",
            lambda: service.preview_card_rewrite(card_id, payload, str(current_user.id)),
        )
    )


@router.post("/cards/{card_id}/rewrite-tasks", response_model=Result[ReviewCardRewriteTask])
def start_review_card_rewrite_task(
    card_id: int,
    payload: ReviewCardRewriteRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewCardRewriteTask]:
    """创建单卡片后台改写任务，并立即返回任务状态。"""
    user_id = str(current_user.id)
    with _review_rewrite_jobs_lock:
        existing = _find_active_review_rewrite_job(user_id, "CARD", card_id, payload)
        if existing is not None:
            return Result.success(_card_rewrite_task_response(existing))
        job = _create_review_rewrite_job(user_id, "CARD", card_id, payload)
    _review_rewrite_executor.submit(_run_review_rewrite_job, service, job)
    return Result.success(_card_rewrite_task_response(job))


@router.get(
    "/cards/{card_id}/rewrite-tasks/latest",
    response_model=Result[ReviewCardRewriteTask | None],
)
def latest_review_card_rewrite_task(
    card_id: int,
    current_user: CurrentUser,
) -> Result[ReviewCardRewriteTask | None]:
    """读取当前卡片最近一次后台改写任务，供重新打开弹窗恢复。"""
    user_id = str(current_user.id)
    with _review_rewrite_jobs_lock:
        task_id = _latest_review_rewrite_jobs.get((user_id, "CARD", card_id))
        job = _review_rewrite_jobs.get(task_id) if task_id else None
        return Result.success(_card_rewrite_task_response(job) if job else None)


@router.get(
    "/cards/{card_id}/rewrite-tasks/{task_id}",
    response_model=Result[ReviewCardRewriteTask],
)
def get_review_card_rewrite_task(
    card_id: int,
    task_id: str,
    current_user: CurrentUser,
) -> Result[ReviewCardRewriteTask]:
    """读取当前用户指定单卡片改写任务。"""
    user_id = str(current_user.id)
    with _review_rewrite_jobs_lock:
        job = _review_rewrite_jobs.get(task_id)
        if job is None or not _review_rewrite_job_matches(job, user_id, "CARD", card_id):
            raise BusinessError("卡片改写任务不存在或已过期")
        return Result.success(_card_rewrite_task_response(job))


@router.put("/cards/{card_id}", response_model=Result[ReviewCard])
def update_review_card(
    card_id: int,
    payload: ReviewCardUpdateRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewCard]:
    """应用用户确认后的卡片正文，同时保留原 FSRS 复习进度。"""
    user_id = str(current_user.id)
    updated = execute("更新复习卡片", lambda: service.update_card(card_id, payload, user_id))
    _invalidate_review_rewrite_job(user_id, "CARD", card_id)
    _invalidate_review_rewrite_job(user_id, "MATERIAL", updated.materialId)
    return Result.success(updated)


@router.post("/cards/{card_id}/grade", response_model=Result[ReviewGradeResult])
def grade_review_card(
    card_id: int,
    payload: ReviewGradeRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewGradeResult]:
    """提交主动回忆评分并计算下一次复习时间。"""
    return Result.success(execute("提交复习评分", lambda: service.grade(card_id, payload, str(current_user.id))))


@router.post("/cards/batch-delete", response_model=Result[ReviewBatchDeletionResult])
def batch_delete_review_cards(
    payload: ReviewCardBatchDeleteRequest,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewBatchDeletionResult]:
    """在单个事务中批量删除复习卡片。"""
    return Result.success(
        execute("批量删除复习卡片", lambda: service.delete_cards(payload.cardIds, str(current_user.id)))
    )


@router.delete("/cards/{card_id}", response_model=Result[ReviewDeletionResult])
def delete_review_card(
    card_id: int,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewDeletionResult]:
    """删除一张复习卡片并保存稳定来源键排除记录。"""
    return Result.success(execute("删除复习卡片", lambda: service.delete_card(card_id, str(current_user.id))))


@router.put("/settings", response_model=Result[ReviewSettings])
def update_review_settings(
    payload: ReviewSettings,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewSettings]:
    """更新当前用户的提醒与 FSRS 目标记忆率设置。"""
    return Result.success(execute("更新复习设置", lambda: service.update_settings(payload, str(current_user.id))))


def execute(operation: str, action: Callable[[], T]) -> T:
    """把未预期异常转换为稳定中文业务错误。"""
    try:
        return action()
    except BusinessError:
        raise
    except Exception:
        logger.exception("%s失败", operation)
        raise BusinessError(f"{operation}失败") from None


def _find_active_review_rewrite_job(
    user_id: str,
    target_kind: str,
    target_id: int,
    payload: ReviewCardRewriteRequest | ReviewMaterialRewriteRequest,
) -> _ReviewRewriteJob | None:
    """只复用请求内容完全相同的运行任务，避免新指令被旧任务吞掉。"""
    task_id = _latest_review_rewrite_jobs.get((user_id, target_kind, target_id))
    job = _review_rewrite_jobs.get(task_id) if task_id else None
    return (
        job
        if job
        and job.status in {"QUEUED", "RUNNING"}
        and job.payload.model_dump(mode="json") == payload.model_dump(mode="json")
        else None
    )


def _create_review_rewrite_job(
    user_id: str,
    target_kind: str,
    target_id: int,
    payload: ReviewCardRewriteRequest | ReviewMaterialRewriteRequest,
) -> _ReviewRewriteJob:
    """在持锁状态下登记改写任务及其初始排队进度。"""
    prefix = "card-rewrite" if target_kind == "CARD" else "material-rewrite"
    key = (user_id, target_kind, target_id)
    previous_task_id = _latest_review_rewrite_jobs.get(key)
    previous = _review_rewrite_jobs.get(previous_task_id) if previous_task_id else None
    if previous and previous.status in {"QUEUED", "RUNNING"}:
        previous.error = "已提交新的改写要求，本任务结果不再使用"
        _set_review_rewrite_progress(
            previous,
            status="FAILED",
            stage_code=f"rewrite.{target_kind.lower()}.superseded",
            stage_label="已由新任务替代",
            message=previous.error,
            percent=100,
        )
    job = _ReviewRewriteJob(
        task_id=f"{prefix}-{uuid4().hex[:12]}",
        user_id=user_id,
        target_kind=target_kind,
        target_id=target_id,
        payload=payload,
        target_card_count=(
            infer_material_rewrite_card_count(
                payload.instruction,
                payload.targetCardCount,
                base_card_count=len(payload.baseCards) or 1,
            )
            if target_kind == "MATERIAL" and isinstance(payload, ReviewMaterialRewriteRequest)
            else 1
        ),
        base_cards=(
            tuple(payload.baseCards)
            if target_kind == "MATERIAL" and isinstance(payload, ReviewMaterialRewriteRequest)
            else ()
        ),
    )
    _set_review_rewrite_progress(
        job,
        status="QUEUED",
        stage_code=f"rewrite.{target_kind.lower()}.queue",
        stage_label="后台排队",
        message="已收到改写请求，任务将在后台继续执行",
        percent=0,
    )
    _review_rewrite_jobs[job.task_id] = job
    _latest_review_rewrite_jobs[key] = job.task_id
    _trim_review_rewrite_jobs()
    return job


def _review_rewrite_job_matches(
    job: _ReviewRewriteJob | None,
    user_id: str,
    target_kind: str,
    target_id: int,
) -> bool:
    """校验任务属于当前认证用户与请求路径中的目标。"""
    return bool(
        job
        and job.user_id == user_id
        and job.target_kind == target_kind
        and job.target_id == target_id
    )


def _invalidate_review_rewrite_job(user_id: str, target_kind: str, target_id: int) -> None:
    """目标内容写入后撤销最近候选，防止重新打开时应用过期结果。"""
    key = (user_id, target_kind, target_id)
    with _review_rewrite_jobs_lock:
        task_id = _latest_review_rewrite_jobs.pop(key, None)
        job = _review_rewrite_jobs.get(task_id) if task_id else None
        if job and job.status in {"QUEUED", "RUNNING"}:
            job.error = "目标内容已更新，本次候选已失效，请重新生成"
            _set_review_rewrite_progress(
                job,
                status="FAILED",
                stage_code=f"rewrite.{target_kind.lower()}.invalidated",
                stage_label="候选已失效",
                message=job.error,
                percent=100,
            )


def _set_review_rewrite_progress(
    job: _ReviewRewriteJob,
    *,
    status: str,
    stage_code: str,
    stage_label: str,
    message: str,
    percent: int,
) -> None:
    """更新后台改写阶段，并仅保留最近 12 条展示事件。"""
    now = datetime.now(timezone.utc)
    event = {
        "stageCode": stage_code,
        "stageLabel": stage_label,
        "message": message,
        "status": "RUNNING" if status in {"QUEUED", "RUNNING"} else status,
        "percent": max(0, min(100, percent)),
        "createdAt": now,
    }
    job.status = status
    job.progress = {**event, "events": [*job.events, event][-12:]}
    job.events = job.progress["events"]
    job.updated_at = now


def _card_rewrite_task_response(job: _ReviewRewriteJob) -> ReviewCardRewriteTask:
    """把单卡片进程内任务转换为公开响应。"""
    return ReviewCardRewriteTask(
        taskId=job.task_id,
        cardId=job.target_id,
        instruction=job.payload.instruction,
        mode=job.payload.mode,
        status=job.status,  # type: ignore[arg-type]
        progress=job.progress,
        result=job.result if isinstance(job.result, ReviewCardRewritePreview) else None,
        error=job.error,
        createdAt=job.created_at,
        updatedAt=job.updated_at,
    )


def _material_rewrite_task_response(job: _ReviewRewriteJob) -> ReviewMaterialRewriteTask:
    """把资料级进程内任务转换为公开响应。"""
    return ReviewMaterialRewriteTask(
        taskId=job.task_id,
        materialId=job.target_id,
        instruction=job.payload.instruction,
        mode=job.payload.mode,
        targetCardCount=job.target_card_count,
        status=job.status,  # type: ignore[arg-type]
        progress=job.progress,
        result=job.result if isinstance(job.result, ReviewMaterialRewritePreview) else None,
        error=job.error,
        createdAt=job.created_at,
        updatedAt=job.updated_at,
    )


def _trim_review_rewrite_jobs() -> None:
    """限制进程内改写历史为 256 条，最旧完成任务允许过期。"""
    overflow = len(_review_rewrite_jobs) - 256
    if overflow <= 0:
        return
    removable = sorted(
        (job for job in _review_rewrite_jobs.values() if job.status in {"SUCCEEDED", "FAILED"}),
        key=lambda item: item.updated_at,
    )
    for job in removable[:overflow]:
        _review_rewrite_jobs.pop(job.task_id, None)
        key = (job.user_id, job.target_kind, job.target_id)
        if _latest_review_rewrite_jobs.get(key) == job.task_id:
            _latest_review_rewrite_jobs.pop(key, None)


def _find_active_review_segment_job(
    user_id: str,
    material_id: int,
    payload: ReviewSegmentGenerationRequest,
) -> _ReviewSegmentJob | None:
    """复用同一资料、同一分段选择和提示词的运行任务。"""
    if payload.forceRestart:
        return None
    task_id = _latest_review_segment_jobs.get((user_id, material_id))
    job = _review_segment_jobs.get(task_id) if task_id else None
    return (
        job
        if job
        and job.status in {"QUEUED", "RUNNING"}
        and job.payload.model_dump(mode="json") == payload.model_dump(mode="json")
        else None
    )


def _create_review_segment_job(
    user_id: str,
    material_id: int,
    payload: ReviewSegmentGenerationRequest,
) -> _ReviewSegmentJob:
    """登记一条交互式分段生成任务，并用新请求替代旧运行任务。"""
    key = (user_id, material_id)
    previous_task_id = _latest_review_segment_jobs.get(key)
    previous = _review_segment_jobs.get(previous_task_id) if previous_task_id else None
    if previous and previous.status in {"QUEUED", "RUNNING"}:
        previous.error = "已提交新的分段选择，本任务结果不再使用"
        _set_review_segment_progress(
            previous,
            status="FAILED",
            stage_code="review.segment.superseded",
            stage_label="已由新任务替代",
            message=previous.error,
            percent=100,
        )
    job = _ReviewSegmentJob(
        task_id=f"segment-generation-{uuid4().hex[:12]}",
        user_id=user_id,
        material_id=material_id,
        payload=payload,
    )
    _set_review_segment_progress(
        job,
        status="QUEUED",
        stage_code="review.segment.queue",
        stage_label="后台排队",
        message=f"已选择 {len(payload.segmentIds)} 个分段，任务将在后台继续执行",
        percent=0,
    )
    _review_segment_jobs[job.task_id] = job
    _latest_review_segment_jobs[key] = job.task_id
    _trim_review_segment_jobs()
    return job


def _set_review_segment_progress(
    job: _ReviewSegmentJob,
    *,
    status: str,
    stage_code: str,
    stage_label: str,
    message: str,
    percent: int,
    metadata: dict[str, object] | None = None,
) -> None:
    """更新分段任务当前阶段，并保留最近 12 条展示事件。"""
    now = datetime.now(timezone.utc)
    event = {
        "stageCode": stage_code,
        "stageLabel": stage_label,
        "message": message,
        "status": "RUNNING" if status in {"QUEUED", "RUNNING"} else status,
        "percent": max(0, min(100, int(percent))),
        "createdAt": now,
    }
    allowed_metadata = {
        "currentStep",
        "totalSteps",
        "attempt",
        "maxAttempts",
        "currentSegmentId",
        "currentSegmentIndex",
        "totalSegments",
        "completedSegments",
        "detail",
        "elapsedSeconds",
        "heartbeatAt",
    }
    event.update(
        {
            key: value
            for key, value in (metadata or {}).items()
            if key in allowed_metadata and value is not None
        }
    )
    job.status = status
    job.progress = {**event, "events": [*job.events, event][-12:]}
    job.events = job.progress["events"]
    job.updated_at = now


def _touch_review_segment_heartbeat(job: _ReviewSegmentJob, started_at: float) -> None:
    """只刷新任务心跳，不用重复事件淹没真实的模型阶段时间线。"""
    now = datetime.now(timezone.utc)
    job.progress = {
        **job.progress,
        "elapsedSeconds": max(0, int(monotonic() - started_at)),
        "heartbeatAt": now,
        "events": job.events,
    }
    job.updated_at = now


def _run_review_segment_heartbeat(
    job: _ReviewSegmentJob,
    stop_event: Event,
    started_at: float,
) -> None:
    """长模型调用期间持续证明后台线程仍存活，终态或任务被替代时自动退出。"""
    while not stop_event.wait(_REVIEW_SEGMENT_HEARTBEAT_SECONDS):
        with _review_segment_jobs_lock:
            if job.status not in {"QUEUED", "RUNNING"}:
                return
            if _latest_review_segment_jobs.get((job.user_id, job.material_id)) != job.task_id:
                return
            _touch_review_segment_heartbeat(job, started_at)


def _review_segment_task_response(job: _ReviewSegmentJob) -> ReviewSegmentGenerationTask:
    """把进程内分段任务转换为公开响应。"""
    return ReviewSegmentGenerationTask(
        taskId=job.task_id,
        materialId=job.material_id,
        mode=job.payload.mode,
        segmentIds=job.payload.segmentIds,
        status=job.status,  # type: ignore[arg-type]
        progress=job.progress,
        result=job.result,
        error=job.error,
        createdAt=job.created_at,
        updatedAt=job.updated_at,
    )


def _run_review_segment_job(service: ReviewService, job: _ReviewSegmentJob) -> None:
    """后台逐段生成候选，单段质量失败由结果对象承载。"""
    started_at = monotonic()
    heartbeat_stop = Event()
    with _review_segment_jobs_lock:
        if job.task_id not in _review_segment_jobs:
            return
        _set_review_segment_progress(
            job,
            status="RUNNING",
            stage_code="review.segment.prepare",
            stage_label="读取分段原文",
            message="正在校验资料版本和用户选择的 evidence 分段",
            percent=5,
            metadata={
                "completedSegments": 0,
                "totalSegments": len(job.payload.segmentIds),
                "elapsedSeconds": 0,
                "heartbeatAt": datetime.now(timezone.utc),
            },
        )
    Thread(
        target=_run_review_segment_heartbeat,
        args=(job, heartbeat_stop, started_at),
        name=f"review-segment-heartbeat-{job.task_id[-6:]}",
        daemon=True,
    ).start()

    def on_progress(event: dict[str, object]) -> None:
        """把服务层逐段进度同步到可轮询任务。"""
        with _review_segment_jobs_lock:
            if job.task_id not in _review_segment_jobs or job.status not in {"QUEUED", "RUNNING"}:
                return
            _set_review_segment_progress(
                job,
                status="RUNNING",
                stage_code=str(event.get("stageCode") or "review.segment.generate"),
                stage_label=str(event.get("stageLabel") or "生成分段候选"),
                message=str(event.get("message") or "正在生成所选分段"),
                percent=int(event.get("percent") or 10),
                metadata={
                    **event,
                    "elapsedSeconds": max(0, int(monotonic() - started_at)),
                    "heartbeatAt": datetime.now(timezone.utc),
                },
            )

    try:
        result = service.generate_selected_segments(
            job.material_id,
            job.user_id,
            job.payload.segmentIds,
            job.payload.prompts,
            job.payload.mode,
            on_progress,
        )
        with _review_segment_jobs_lock:
            if _latest_review_segment_jobs.get((job.user_id, job.material_id)) != job.task_id:
                return
            job.result = result
            success_count = sum(1 for item in result.segments if item.status == "SUCCEEDED")
            _set_review_segment_progress(
                job,
                status="SUCCEEDED",
                stage_code="review.segment.completed",
                stage_label="所选分段已完成",
                message=f"本轮 {success_count}/{len(result.segments)} 个分段生成成功，可继续选择其他分段或合并",
                percent=100,
            )
    except BusinessError as exc:
        with _review_segment_jobs_lock:
            if _latest_review_segment_jobs.get((job.user_id, job.material_id)) != job.task_id:
                return
            job.error = exc.message
            _set_review_segment_progress(
                job,
                status="FAILED",
                stage_code="review.segment.failed",
                stage_label="分段生成失败",
                message=exc.message,
                percent=100,
            )
    except Exception as exc:  # noqa: BLE001 - 后台任务必须收敛为可查询失败状态。
        logger.exception("交互式分段生成任务失败: material_id=%s", job.material_id)
        with _review_segment_jobs_lock:
            if _latest_review_segment_jobs.get((job.user_id, job.material_id)) != job.task_id:
                return
            job.error = f"分段生成失败：{exc.__class__.__name__}"
            _set_review_segment_progress(
                job,
                status="FAILED",
                stage_code="review.segment.failed",
                stage_label="分段生成失败",
                message=job.error,
                percent=100,
            )
    finally:
        heartbeat_stop.set()


def _invalidate_review_segment_job(user_id: str, material_id: int) -> None:
    """正式合并后移除最近任务入口，避免旧候选再次被恢复。"""
    with _review_segment_jobs_lock:
        _latest_review_segment_jobs.pop((user_id, material_id), None)


def _trim_review_segment_jobs() -> None:
    """限制进程内分段任务历史，优先淘汰最旧终态任务。"""
    overflow = len(_review_segment_jobs) - 256
    if overflow <= 0:
        return
    removable = sorted(
        (job for job in _review_segment_jobs.values() if job.status in {"SUCCEEDED", "FAILED"}),
        key=lambda item: item.updated_at,
    )
    for job in removable[:overflow]:
        _review_segment_jobs.pop(job.task_id, None)
        key = (job.user_id, job.material_id)
        if _latest_review_segment_jobs.get(key) == job.task_id:
            _latest_review_segment_jobs.pop(key, None)


def _run_review_rewrite_job(service: ReviewService, job: _ReviewRewriteJob) -> None:
    """后台生成无副作用对比候选，关闭弹窗不会中断模型调用。"""
    is_card = job.target_kind == "CARD"
    target_label = "卡片" if is_card else "资料"
    stage_prefix = f"rewrite.{job.target_kind.lower()}"
    with _review_rewrite_jobs_lock:
        if job.task_id not in _review_rewrite_jobs:
            return
        _set_review_rewrite_progress(
            job,
            status="RUNNING",
            stage_code=f"{stage_prefix}.prepare",
            stage_label=f"读取{target_label}",
            message=f"正在读取当前{target_label}内容与可用原文证据",
            percent=15,
        )
        _set_review_rewrite_progress(
            job,
            status="RUNNING",
            stage_code=f"{stage_prefix}.generate",
            stage_label="调用 AI 生成候选",
            message="AI 正在后台生成修改后的候选内容",
            percent=40,
        )
    try:
        if is_card:
            if not isinstance(job.payload, ReviewCardRewriteRequest):
                raise BusinessError("卡片改写任务参数无效")
            result = service.preview_card_rewrite(job.target_id, job.payload, job.user_id)
        else:
            if not isinstance(job.payload, ReviewMaterialRewriteRequest):
                raise BusinessError("资料改写任务参数无效")
            result = service.preview_material_rewrite(job.target_id, job.payload, job.user_id)
        with _review_rewrite_jobs_lock:
            latest_task_id = _latest_review_rewrite_jobs.get(
                (job.user_id, job.target_kind, job.target_id)
            )
            if latest_task_id != job.task_id:
                return
            job.result = result
            _set_review_rewrite_progress(
                job,
                status="RUNNING",
                stage_code=f"{stage_prefix}.validate",
                stage_label="校验证据与候选",
                message="候选已生成，正在整理修改前后对比",
                percent=85,
            )
            _set_review_rewrite_progress(
                job,
                status="SUCCEEDED",
                stage_code=f"{stage_prefix}.completed",
                stage_label="对比生成完成",
                message="修改前后对比已生成，请确认后再应用",
                percent=100,
            )
    except BusinessError as exc:
        with _review_rewrite_jobs_lock:
            job.error = exc.message
            _set_review_rewrite_progress(
                job,
                status="FAILED",
                stage_code=f"{stage_prefix}.failed",
                stage_label="改写失败",
                message=exc.message,
                percent=100,
            )
    except Exception:  # noqa: BLE001 - 后台线程不能向已经返回的 HTTP 请求传播异常。
        logger.exception(
            "后台复习改写任务失败，targetKind=%s，targetId=%s，taskId=%s",
            job.target_kind,
            job.target_id,
            job.task_id,
        )
        with _review_rewrite_jobs_lock:
            job.error = f"生成{target_label}改写对比失败"
            _set_review_rewrite_progress(
                job,
                status="FAILED",
                stage_code=f"{stage_prefix}.failed",
                stage_label="改写失败",
                message=job.error,
                percent=100,
            )


def _find_active_missing_knowledge_job(user_id: str, material_id: int) -> _MissingKnowledgeJob | None:
    """按用户和资料复用尚未结束的补漏任务，避免重复调用模型。"""
    task_id = _latest_missing_knowledge_jobs.get((user_id, material_id))
    job = _missing_knowledge_jobs.get(task_id) if task_id else None
    return job if job and job.status in {"QUEUED", "RUNNING"} else None


def _set_missing_knowledge_progress(
    job: _MissingKnowledgeJob,
    *,
    status: str,
    stage_code: str,
    stage_label: str,
    message: str,
    percent: int,
) -> None:
    """更新补漏任务当前阶段，并保留最近 12 条可展示事件。"""
    now = datetime.now(timezone.utc)
    event = {
        "stageCode": stage_code,
        "stageLabel": stage_label,
        "message": message,
        "status": "RUNNING" if status in {"QUEUED", "RUNNING"} else status,
        "percent": max(0, min(100, percent)),
        "createdAt": now,
    }
    job.status = status
    job.progress = {**event, "events": [*job.events, event][-12:]}
    job.events = job.progress["events"]
    job.updated_at = now


def _missing_knowledge_task_response(job: _MissingKnowledgeJob | None) -> ReviewMissingKnowledgeTask | None:
    """把进程内任务转换为认证用户可读取的公开响应。"""
    if job is None:
        return None
    return ReviewMissingKnowledgeTask(
        taskId=job.task_id,
        materialId=job.material_id,
        message=job.message,
        status=job.status,  # type: ignore[arg-type]
        progress=job.progress,
        result=job.result,
        error=job.error,
        createdAt=job.created_at,
        updatedAt=job.updated_at,
    )


def _trim_missing_knowledge_jobs() -> None:
    """限制进程内历史任务数量，保留最近任务和所有未结束任务。"""
    if len(_missing_knowledge_jobs) <= 256:
        return
    latest_ids = set(_latest_missing_knowledge_jobs.values())
    removable = sorted(
        (
            job
            for job in _missing_knowledge_jobs.values()
            if job.status in {"SUCCEEDED", "FAILED"} and job.task_id not in latest_ids
        ),
        key=lambda item: item.updated_at,
    )
    for job in removable[: max(0, len(_missing_knowledge_jobs) - 256)]:
        _missing_knowledge_jobs.pop(job.task_id, None)


def _run_missing_knowledge_job(service: ReviewService, job: _MissingKnowledgeJob) -> None:
    """执行后台补漏任务，关闭浏览器弹窗不会中断模型和只追加事务。"""
    with _missing_knowledge_jobs_lock:
        if job.task_id not in _missing_knowledge_jobs:
            return
        _set_missing_knowledge_progress(
            job,
            status="RUNNING",
            stage_code="missing.prepare",
            stage_label="准备补漏",
            message="正在定位当前资料的 RAG 原文并核对现有卡片",
            percent=12,
        )

    def report_progress(event: dict[str, Any]) -> None:
        with _missing_knowledge_jobs_lock:
            if job.task_id not in _missing_knowledge_jobs:
                return
            _set_missing_knowledge_progress(
                job,
                status="RUNNING",
                stage_code=str(event.get("stageCode") or "missing.running"),
                stage_label=str(event.get("stageLabel") or "补漏处理中"),
                message=str(event.get("message") or "正在处理补漏任务"),
                percent=int(event.get("percent") or 0),
            )

    try:
        result = service.supplement_missing_knowledge(
            job.material_id,
            job.payload,
            job.user_id,
            progress_callback=report_progress,
        )
        with _missing_knowledge_jobs_lock:
            job.result = result
            _set_missing_knowledge_progress(
                job,
                status="SUCCEEDED",
                stage_code="missing.completed",
                stage_label="补漏完成",
                message=f"补漏完成，已追加 {result.addedCount} 张新卡片",
                percent=100,
            )
    except BusinessError as exc:
        with _missing_knowledge_jobs_lock:
            job.error = exc.message
            _set_missing_knowledge_progress(
                job,
                status="FAILED",
                stage_code="missing.failed",
                stage_label="补漏失败",
                message=exc.message,
                percent=100,
            )
    except Exception:  # noqa: BLE001 - 后台线程不能把异常传播到已完成的 HTTP 响应。
        logger.exception("后台补漏任务失败，materialId=%s，taskId=%s", job.material_id, job.task_id)
        with _missing_knowledge_jobs_lock:
            job.error = "查找遗漏复习知识点失败"
            _set_missing_knowledge_progress(
                job,
                status="FAILED",
                stage_code="missing.failed",
                stage_label="补漏失败",
                message=job.error,
                percent=100,
            )


def _submit_review_generation(
    service: ReviewService,
    material_id: int,
    user_id: str,
    user_feedback: str | None,
    generation_mode: str,
) -> None:
    """提交资料级后台任务，同一进程内避免重复排队。"""
    key = (user_id, material_id)
    with _review_generation_jobs_lock:
        if key in _review_generation_jobs:
            return
        _review_generation_jobs.add(key)
    _review_generation_executor.submit(
        _run_review_generation,
        service,
        material_id,
        user_id,
        user_feedback,
        generation_mode,
        key,
    )


def _run_review_generation(
    service: ReviewService,
    material_id: int,
    user_id: str,
    user_feedback: str | None,
    generation_mode: str,
    key: tuple[str, int],
) -> None:
    """执行后台复习生成并把异常收敛到资料终态或日志。"""
    try:
        if generation_mode == "STANDARD":
            # 兼容尚未扩展 generation_mode 的旧服务替身和外部调用方。
            service.generate_material(material_id, user_id, user_feedback)
        else:
            service.generate_material(
                material_id,
                user_id,
                user_feedback,
                generation_mode=generation_mode,
            )
    except BusinessError as exc:
        # 另一实例可能已经持有资料锁，保留真实运行方的进度即可。
        logger.info("后台复习生成未取得资料锁，materialId=%s，原因：%s", material_id, exc)
    except Exception:  # noqa: BLE001 - 后台线程不能把异常传播到已完成的 HTTP 响应。
        logger.exception("后台复习生成失败，materialId=%s", material_id)
    finally:
        with _review_generation_jobs_lock:
            _review_generation_jobs.discard(key)
