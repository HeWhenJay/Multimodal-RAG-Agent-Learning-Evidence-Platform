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
    sourceType: Literal["RAG", "MANUAL"] = "RAG"
    # 到期列表隐藏答案，用户主动揭示后才返回正文。
    answer: str | None = None
    hint: str | None = None
    evidenceRefs: list[Evidence] = Field(default_factory=list)
    dueAt: datetime
    retrievability: float = Field(default=0.0, ge=0.0, le=1.0)
    reviewCount: int = Field(default=0, ge=0)
    lapseCount: int = Field(default=0, ge=0)
    isUserEdited: bool = False
    updatedAt: datetime | None = None


class ReviewCardContent(BaseModel):
    """卡片编辑和 AI 对比预览共用的可修改正文。"""

    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1, max_length=5000)
    hint: str | None = Field(default=None, max_length=1000)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        """问题保持单行，允许保留行内 Markdown。"""
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("问题不能为空")
        return normalized

    @field_validator("answer")
    @classmethod
    def normalize_answer(cls, value: str) -> str:
        """答案保留 Markdown 换行和代码块结构。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("答案不能为空")
        return normalized

    @field_validator("hint")
    @classmethod
    def normalize_hint(cls, value: str | None) -> str | None:
        """空提示按未填写处理，其余内容保留 Markdown 结构。"""
        normalized = value.strip() if value else ""
        return normalized or None


class ReviewCardUpdateRequest(ReviewCardContent):
    """应用人工编辑或 AI 候选时使用的卡片内容。"""

    rewriteMode: Literal["STRICT_SOURCE", "SOURCE_FIRST", "SOURCE_REFERENCE"] | None = None
    evidenceIds: list[str] | None = Field(default=None, max_length=4)

    @field_validator("evidenceIds")
    @classmethod
    def normalize_evidence_ids(cls, value: list[str] | None) -> list[str] | None:
        """保留模型引用顺序并拒绝空 ID，服务层再校验资料归属。"""
        if value is None:
            return None
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        return normalized


class ReviewCardRewriteRequest(BaseModel):
    """用户对单张卡片提出的自然语言改写想法和来源档位。"""

    instruction: str = Field(..., min_length=1, max_length=2000)
    mode: Literal["STRICT_SOURCE", "SOURCE_FIRST", "SOURCE_REFERENCE"] = "SOURCE_FIRST"

    @field_validator("instruction")
    @classmethod
    def normalize_instruction(cls, value: str) -> str:
        """压缩说明中的冗余空白，避免空提示进入模型。"""
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("卡片改写想法不能为空")
        return normalized


class ReviewCardRewritePreview(BaseModel):
    """原卡片与 LLM 候选的无副作用对比结果。"""

    cardId: int = Field(ge=1)
    mode: Literal["STRICT_SOURCE", "SOURCE_FIRST", "SOURCE_REFERENCE"]
    original: ReviewCardContent
    proposed: ReviewCardContent
    evidenceRefs: list[Evidence] = Field(default_factory=list)
    modelName: str


class ReviewMaterialCardSnapshot(BaseModel):
    """资料级改写预览中的一张原卡片或候选卡片。"""

    cardId: int | None = Field(default=None, ge=1)
    content: ReviewCardContent
    evidenceRefs: list[Evidence] = Field(default_factory=list)
    evidenceIds: list[str] = Field(default_factory=list, max_length=4)


class ReviewMaterialRewriteRequest(BaseModel):
    """请求把资料当前卡片重新组织为新的资料级卡片集合。"""

    instruction: str = Field(..., min_length=1, max_length=2000)
    mode: Literal["STRICT_SOURCE", "SOURCE_FIRST", "SOURCE_REFERENCE"] = "SOURCE_FIRST"

    @field_validator("instruction")
    @classmethod
    def normalize_instruction(cls, value: str) -> str:
        """压缩资料改写说明，避免无意义空白进入模型。"""
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("资料改写想法不能为空")
        return normalized


class ReviewMaterialRewritePreview(BaseModel):
    """资料级改写的无副作用前后对比结果。"""

    materialId: int = Field(ge=1)
    title: str
    sourceVersion: int = Field(ge=0)
    originalFingerprint: str = Field(..., min_length=16, max_length=128)
    originalCardIds: list[int] = Field(default_factory=list, max_length=100)
    originalCards: list[ReviewMaterialCardSnapshot] = Field(default_factory=list, max_length=100)
    proposedCards: list[ReviewMaterialCardSnapshot] = Field(default_factory=list, min_length=1, max_length=8)
    originalSummary: str | None = None
    proposedSummary: str | None = None
    mergeNote: str | None = None
    mode: Literal["STRICT_SOURCE", "SOURCE_FIRST", "SOURCE_REFERENCE"]
    modelName: str


class ReviewRewriteProgressEvent(BaseModel):
    """单卡片或资料级后台改写任务的一条阶段事件。"""

    stageCode: str
    stageLabel: str
    message: str
    status: Literal["RUNNING", "SUCCEEDED", "FAILED"] = "RUNNING"
    percent: int = Field(default=0, ge=0, le=100)
    createdAt: datetime | None = None


class ReviewRewriteTaskProgress(ReviewRewriteProgressEvent):
    """后台改写任务当前阶段和最近事件。"""

    events: list[ReviewRewriteProgressEvent] = Field(default_factory=list, max_length=12)


class ReviewCardRewriteTask(BaseModel):
    """可关闭弹窗后继续查询的单卡片改写任务。"""

    taskId: str = Field(min_length=1, max_length=80)
    cardId: int = Field(ge=1)
    instruction: str
    mode: Literal["STRICT_SOURCE", "SOURCE_FIRST", "SOURCE_REFERENCE"]
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    progress: ReviewRewriteTaskProgress
    result: ReviewCardRewritePreview | None = None
    error: str | None = None
    createdAt: datetime
    updatedAt: datetime


class ReviewMaterialRewriteTask(BaseModel):
    """可关闭弹窗后继续查询的资料级合并改写任务。"""

    taskId: str = Field(min_length=1, max_length=80)
    materialId: int = Field(ge=1)
    instruction: str
    mode: Literal["STRICT_SOURCE", "SOURCE_FIRST", "SOURCE_REFERENCE"]
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    progress: ReviewRewriteTaskProgress
    result: ReviewMaterialRewritePreview | None = None
    error: str | None = None
    createdAt: datetime
    updatedAt: datetime


class ReviewMaterialRewriteApplyRequest(BaseModel):
    """携带预览版本与用户确认后的候选内容，原子覆盖资料卡片。"""

    sourceVersion: int = Field(ge=0)
    originalFingerprint: str = Field(..., min_length=16, max_length=128)
    originalCardIds: list[int] = Field(..., min_length=1, max_length=100)
    proposedCards: list[ReviewCardUpdateRequest] = Field(..., min_length=1, max_length=8)
    proposedSummary: str | None = Field(default=None, max_length=5000)

    @field_validator("originalCardIds")
    @classmethod
    def normalize_original_card_ids(cls, value: list[int]) -> list[int]:
        """去重并排序原卡片 ID，确保并发校验稳定。"""
        if any(item <= 0 for item in value):
            raise ValueError("原卡片 ID 必须是正整数")
        return sorted(set(value))


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
    folderId: int | None = Field(default=None, ge=1)
    folderName: str | None = None
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


class ReviewFolderMaterialOrderRequest(ReviewGroupOrderRequest):
    """保存一个复习文件夹内文档顺序的批量请求。"""


class ReviewGroupOrderResult(BaseModel):
    """成功持久化的复习资料顺序。"""

    materialIds: list[int] = Field(..., min_length=1, max_length=100)
    orderedCount: int = Field(ge=1, le=100)


class ReviewGenerationRequest(BaseModel):
    """用户在人工修复阶段提供的可选生成说明。"""

    userFeedback: str | None = Field(default=None, max_length=2000)

    @field_validator("userFeedback")
    @classmethod
    def normalize_user_feedback(cls, value: str | None) -> str | None:
        """去除首尾空白，空说明按未提供处理。"""
        normalized = " ".join((value or "").split()).strip()
        return normalized or None


class ReviewMissingKnowledgeConversationMessage(BaseModel):
    """补漏对话中由前端携带的一条会话级消息。"""

    role: Literal["USER", "ASSISTANT"]
    content: str = Field(..., min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        """压缩空白，避免空消息或无意义上下文进入模型。"""
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("对话内容不能为空")
        return normalized


class ReviewMissingKnowledgeRequest(BaseModel):
    """用户针对单份文档指出遗漏知识点的补充请求。"""

    message: str = Field(..., min_length=1, max_length=2000)
    conversation: list[ReviewMissingKnowledgeConversationMessage] = Field(
        default_factory=list,
        max_length=12,
    )

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        """去掉本轮提示的多余空白，空提示在调用模型前拒绝。"""
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("遗漏知识点提示不能为空")
        return normalized


class ReviewManualCardRequest(BaseModel):
    """用户直接创建一张复习卡片时使用的内容。"""

    question: str = Field(..., min_length=1, max_length=500)
    answer: str = Field(..., min_length=1, max_length=5000)
    hint: str | None = Field(default=None, max_length=1000)

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str) -> str:
        """压缩问题中的多余空白，保持手动卡片易于回忆。"""
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("问题不能为空")
        return normalized

    @field_validator("answer")
    @classmethod
    def normalize_answer(cls, value: str) -> str:
        """只去掉答案首尾空白，保留用户输入的 Markdown 换行结构。"""
        normalized = value.strip()
        if not normalized:
            raise ValueError("答案不能为空")
        return normalized

    @field_validator("hint")
    @classmethod
    def normalize_hint(cls, value: str | None) -> str | None:
        """空提示按未填写处理，非空提示只去除首尾空白。"""
        normalized = value.strip() if value else ""
        return normalized or None


class ReviewMissingKnowledgeResult(BaseModel):
    """只追加新卡片后的对话答复与实际写入结果。"""

    materialId: int = Field(ge=1)
    assistantMessage: str
    addedCount: int = Field(default=0, ge=0)
    skippedCount: int = Field(default=0, ge=0)
    cards: list[ReviewCard] = Field(default_factory=list)


class ReviewMissingKnowledgeProgressEvent(BaseModel):
    """后台补漏任务的一条可展示阶段事件。"""

    stageCode: str
    stageLabel: str
    message: str
    status: Literal["RUNNING", "SUCCEEDED", "FAILED"] = "RUNNING"
    percent: int = Field(default=0, ge=0, le=100)
    createdAt: datetime | None = None


class ReviewMissingKnowledgeTaskProgress(ReviewMissingKnowledgeProgressEvent):
    """后台补漏任务当前阶段和最近事件。"""

    events: list[ReviewMissingKnowledgeProgressEvent] = Field(default_factory=list, max_length=12)


class ReviewMissingKnowledgeTask(BaseModel):
    """可关闭对话窗口后继续查询的补漏任务。"""

    taskId: str = Field(min_length=1, max_length=80)
    materialId: int = Field(ge=1)
    message: str
    status: Literal["QUEUED", "RUNNING", "SUCCEEDED", "FAILED"]
    progress: ReviewMissingKnowledgeTaskProgress
    result: ReviewMissingKnowledgeResult | None = None
    error: str | None = None
    createdAt: datetime
    updatedAt: datetime


class ReviewGenerationProgressEvent(BaseModel):
    """复习生成图的一条阶段事件，供前端展示真实处理进度。"""

    stageCode: str
    stageLabel: str
    message: str
    status: str = "RUNNING"
    currentStep: int | None = Field(default=None, ge=0)
    totalSteps: int | None = Field(default=None, ge=1)
    percent: int = Field(default=0, ge=0, le=100)
    attempt: int | None = Field(default=None, ge=0)
    maxAttempts: int | None = Field(default=None, ge=1)
    detail: str | None = None
    createdAt: datetime | None = None


class ReviewGenerationProgress(ReviewGenerationProgressEvent):
    """当前复习生成阶段与最近事件时间线。"""

    events: list[ReviewGenerationProgressEvent] = Field(default_factory=list, max_length=12)


class ReviewMaterial(BaseModel):
    """一条资料的学习分类与卡片生成状态。"""

    materialId: int
    title: str
    summary: str | None = None
    documentType: str
    materialStatus: str
    isLearningContent: bool | None = None
    category: str | None = None
    status: Literal["PENDING", "GENERATING", "GENERATED", "SKIPPED", "FAILED", "NEEDS_REVIEW"] = "PENDING"
    reason: str | None = None
    cardCount: int = Field(default=0, ge=0)
    generationAttempts: int = Field(default=0, ge=0)
    qualityFeedback: list[str] = Field(default_factory=list)
    generationProgress: ReviewGenerationProgress | None = None
    needsManualReview: bool = False
    folderId: int | None = Field(default=None, ge=1)
    folderName: str | None = None
    indexRequestVersion: int = Field(default=0, ge=0)
    syncedIndexRequestVersion: int | None = Field(default=None, ge=0)
    updatedAt: datetime | None = None


class ReviewMaterialRewriteApplyResult(BaseModel):
    """资料级改写确认后的资料状态和新卡片。"""

    material: ReviewMaterial
    cards: list[ReviewCard] = Field(default_factory=list)
    replacedCardIds: list[int] = Field(default_factory=list)


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
    materialStatus: str | None = None
    category: str | None = None
    status: Literal["PENDING", "GENERATING", "GENERATED", "SKIPPED", "FAILED", "NEEDS_REVIEW"] | str = "PENDING"
    reason: str | None = None
    generationProgress: ReviewGenerationProgress | None = None
    indexRequestVersion: int = Field(default=0, ge=0)
    cardCount: int = Field(default=0, ge=0)
    cards: list[ReviewCard] = Field(default_factory=list)


class ReviewFolderDetail(BaseModel):
    """进入文件夹后按文档展示卡片的响应。"""

    folder: ReviewFolder
    materials: list[ReviewFolderMaterial] = Field(default_factory=list)


class ReviewCardLibraryMaterial(BaseModel):
    """卡片库中按文档聚合的全部活动卡片。"""

    materialId: int
    title: str
    summary: str | None = None
    documentType: str
    folderId: int | None = Field(default=None, ge=1)
    folderName: str | None = None
    cardCount: int = Field(default=0, ge=0)
    reviewedCardCount: int = Field(default=0, ge=0)
    cards: list[ReviewCard] = Field(default_factory=list)


class ReviewCardLibrary(BaseModel):
    """不提供评分能力的当前用户全量卡片库。"""

    totalMaterialCount: int = Field(default=0, ge=0)
    totalCardCount: int = Field(default=0, ge=0)
    reviewedCardCount: int = Field(default=0, ge=0)
    materials: list[ReviewCardLibraryMaterial] = Field(default_factory=list)


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
