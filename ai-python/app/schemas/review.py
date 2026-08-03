"""学习资料复习中心的公开请求与响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.rag import Evidence


class ReviewSettings(BaseModel):
    """用户的 FSRS 排程与提醒偏好。"""

    enabled: bool = True
    desiredRetention: float = Field(default=0.90, ge=0.80, le=0.97)
    dailyLimit: int = Field(default=20, ge=1, le=100)
    reminderTime: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)

    @field_validator("timezone")
    @classmethod
    def normalize_timezone(cls, value: str) -> str:
        """去掉时区名称两端空白，具体 IANA 校验由服务层完成。"""
        return value.strip()


class ReviewCard(BaseModel):
    """一张可独立评分的关键知识点卡片。"""

    id: int
    materialId: int
    materialTitle: str
    documentType: str
    question: str
    # 到期列表隐藏答案，用户主动揭示后才返回正文。
    answer: str | None = None
    hint: str | None = None
    evidenceRefs: list[Evidence] = Field(default_factory=list)
    dueAt: datetime
    retrievability: float = Field(default=0.0, ge=0.0, le=1.0)
    reviewCount: int = Field(default=0, ge=0)
    lapseCount: int = Field(default=0, ge=0)


class ReviewOverview(BaseModel):
    """复习中心顶部统计。"""

    dueCount: int = Field(default=0, ge=0)
    actionableDueCount: int = Field(default=0, ge=0)
    todayReviewedCount: int = Field(default=0, ge=0)
    totalCardCount: int = Field(default=0, ge=0)
    activeMaterialCount: int = Field(default=0, ge=0)
    nextDueAt: datetime | None = None
    settings: ReviewSettings


class ReviewCardGroup(BaseModel):
    """按用户上传资料聚合的一组每日复习卡片。"""

    materialId: int
    materialTitle: str
    materialSummary: str | None = None
    documentType: str
    dueCardCount: int = Field(default=0, ge=0)
    cards: list[ReviewCard] = Field(default_factory=list)


class ReviewDueGroups(BaseModel):
    """每日到期卡片的资料分组响应。"""

    totalDueCount: int = Field(default=0, ge=0)
    remainingToday: int = Field(default=0, ge=0)
    groups: list[ReviewCardGroup] = Field(default_factory=list)


class ReviewGroupOrderRequest(BaseModel):
    """保存今日资料分组顺序的批量请求。"""

    materialIds: list[int] = Field(..., min_length=1, max_length=100)

    @field_validator("materialIds")
    @classmethod
    def validate_material_ids(cls, value: list[int]) -> list[int]:
        """保留拖拽顺序，同时拒绝无法确定先后关系的非法 ID。"""
        if any(item <= 0 for item in value):
            raise ValueError("资料 ID 必须是正整数")
        if len(set(value)) != len(value):
            raise ValueError("资料 ID 不能重复")
        return value


class ReviewGroupOrderResult(BaseModel):
    """成功持久化的今日资料分组顺序。"""

    materialIds: list[int] = Field(..., min_length=1, max_length=100)
    orderedCount: int = Field(ge=1, le=100)


class ReviewMaterial(BaseModel):
    """一条资料的学习分类与卡片生成状态。"""

    materialId: int
    title: str
    summary: str | None = None
    documentType: str
    materialStatus: str
    isLearningContent: bool | None = None
    category: str | None = None
    status: Literal["PENDING", "GENERATING", "GENERATED", "SKIPPED", "FAILED"] = "PENDING"
    reason: str | None = None
    cardCount: int = Field(default=0, ge=0)
    folderId: int | None = Field(default=None, ge=1)
    folderName: str | None = None
    indexRequestVersion: int = Field(default=0, ge=0)
    syncedIndexRequestVersion: int | None = Field(default=None, ge=0)
    updatedAt: datetime | None = None


class ReviewFolderNameRequest(BaseModel):
    """创建或重命名复习文件夹时使用的名称。"""

    name: str = Field(..., min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        """去掉首尾空白，并拒绝只包含空白的名称。"""
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("文件夹名称不能为空")
        return normalized


class ReviewFolder(BaseModel):
    """用户自定义复习文件夹及其实时资料统计。"""

    id: int
    name: str
    materialCount: int = Field(default=0, ge=0)
    cardCount: int = Field(default=0, ge=0)
    dueCardCount: int = Field(default=0, ge=0)
    updatedAt: datetime | None = None


class ReviewFolderMaterial(BaseModel):
    """文件夹中以文档为单位聚合的全部活动卡片。"""

    materialId: int
    title: str
    summary: str | None = None
    documentType: str
    cardCount: int = Field(default=0, ge=0)
    cards: list[ReviewCard] = Field(default_factory=list)


class ReviewFolderDetail(BaseModel):
    """进入文件夹后按文档展示卡片的响应。"""

    folder: ReviewFolder
    materials: list[ReviewFolderMaterial] = Field(default_factory=list)


class ReviewMaterialFolderRequest(BaseModel):
    """以文档为最小单位批量更新文件夹归属。"""

    materialIds: list[int] = Field(..., min_length=1, max_length=100)
    folderId: int | None = Field(default=None, ge=1)

    @field_validator("materialIds")
    @classmethod
    def validate_material_ids(cls, value: list[int]) -> list[int]:
        """拒绝无效或重复 ID，确保批量归档语义明确。"""
        if any(item <= 0 for item in value):
            raise ValueError("资料 ID 必须是正整数")
        if len(set(value)) != len(value):
            raise ValueError("资料 ID 不能重复")
        return value


class ReviewFolderAssignmentResult(BaseModel):
    """批量更新文档归属后的稳定结果。"""

    folderId: int | None = Field(default=None, ge=1)
    materialIds: list[int] = Field(..., min_length=1, max_length=100)
    movedCount: int = Field(ge=1, le=100)


class ReviewFolderDeletionResult(BaseModel):
    """删除文件夹但保留资料和卡片的结果。"""

    folderId: int
    deleted: bool = True
    unfiledMaterialCount: int = Field(default=0, ge=0)


class ReviewSyncResult(BaseModel):
    """一次增量扫描的汇总结果。"""

    processedMaterialCount: int = Field(default=0, ge=0)
    generatedCardCount: int = Field(default=0, ge=0)
    skippedMaterialCount: int = Field(default=0, ge=0)
    failedMaterialCount: int = Field(default=0, ge=0)


class ReviewGradeRequest(BaseModel):
    """用户对一次主动回忆的四档评分。"""

    rating: int = Field(..., ge=1, le=4)
    durationMs: int | None = Field(default=None, ge=0, le=3_600_000)


class ReviewGradeResult(BaseModel):
    """评分后更新的卡片及下一次复习信息。"""

    card: ReviewCard
    previousDueAt: datetime
    nextDueAt: datetime
    intervalDays: float = Field(ge=0.0)
    retrievability: float = Field(ge=0.0, le=1.0)


class ReviewDeletionResult(BaseModel):
    """卡片或资料组完成持久排除后的幂等响应。"""

    scope: Literal["CARD", "MATERIAL"]
    materialId: int
    cardId: int | None = None
    deleted: bool = True


class ReviewCardBatchDeleteRequest(BaseModel):
    """批量删除卡片请求，最多处理 100 张并自动去重。"""

    cardIds: list[int] = Field(..., min_length=1, max_length=100)

    @field_validator("cardIds")
    @classmethod
    def normalize_card_ids(cls, value: list[int]) -> list[int]:
        """去重并排序 ID，降低批量事务锁顺序不一致造成死锁的概率。"""
        if any(item <= 0 for item in value):
            raise ValueError("卡片 ID 必须是正整数")
        return sorted(set(value))


class ReviewMaterialBatchDeleteRequest(BaseModel):
    """批量移出资料请求，最多处理 100 份并自动去重。"""

    materialIds: list[int] = Field(..., min_length=1, max_length=100)

    @field_validator("materialIds")
    @classmethod
    def normalize_material_ids(cls, value: list[int]) -> list[int]:
        """去重并排序资料 ID，保证批量锁定顺序稳定。"""
        if any(item <= 0 for item in value):
            raise ValueError("资料 ID 必须是正整数")
        return sorted(set(value))


class ReviewBatchDeletionResult(BaseModel):
    """批量排除结果，忽略无归属 ID，并对已排除 ID 保持幂等成功。"""

    scope: Literal["CARD", "MATERIAL"]
    requestedCount: int = Field(ge=1, le=100)
    deletedCount: int = Field(ge=1, le=100)
    cardIds: list[int] = Field(default_factory=list)
    materialIds: list[int] = Field(default_factory=list)
