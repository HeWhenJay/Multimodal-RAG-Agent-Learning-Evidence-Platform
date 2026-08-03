"""学习资料复习与提醒公开路由。"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import TypeVar

from fastapi import APIRouter, Depends, Query

from app.core.current_user import CurrentUser
from app.core.result import BusinessError, Result
from app.review.generation_guard import ReviewGenerationGuard
from app.review.service import ReviewService
from app.schemas.review import (
    ReviewCard,
    ReviewBatchDeletionResult,
    ReviewCardBatchDeleteRequest,
    ReviewDeletionResult,
    ReviewDueGroups,
    ReviewFolder,
    ReviewFolderAssignmentResult,
    ReviewFolderDeletionResult,
    ReviewFolderDetail,
    ReviewFolderNameRequest,
    ReviewGroupOrderRequest,
    ReviewGroupOrderResult,
    ReviewGradeRequest,
    ReviewGradeResult,
    ReviewMaterial,
    ReviewMaterialBatchDeleteRequest,
    ReviewMaterialFolderRequest,
    ReviewOverview,
    ReviewSettings,
    ReviewSyncResult,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/reviews", tags=["学习复习"])
T = TypeVar("T")
_generation_guard = ReviewGenerationGuard()


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
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewMaterial]:
    """对一条当前用户资料重新分类并生成关键知识点。"""
    return Result.success(
        execute("生成学习资料复习卡片", lambda: service.generate_material(material_id, str(current_user.id)))
    )


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


@router.get("/cards/{card_id}", response_model=Result[ReviewCard])
def reveal_review_card(
    card_id: int,
    current_user: CurrentUser,
    service: ReviewService = Depends(get_review_service),
) -> Result[ReviewCard]:
    """用户主动查看答案时返回答案正文和原文 evidence。"""
    return Result.success(execute("查看复习卡片答案", lambda: service.get_card(card_id, str(current_user.id))))


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
