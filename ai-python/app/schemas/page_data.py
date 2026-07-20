"""工作台与设置页面的公开响应模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class RagProgressResponse(BaseModel):
    """资料卡片展示的 RAG 进度事件。"""

    stageCode: str | None = None
    stageLabel: str | None = None
    message: str = ""
    status: str | None = None
    currentStep: int | None = None
    totalSteps: int | None = None
    currentChunk: int | None = None
    totalChunks: int | None = None
    chunkId: str | None = None
    blockId: str | None = None
    percent: int | None = None
    detail: str | None = None
    createdAt: datetime | None = None


class LearningMaterialPageResponse(BaseModel):
    """工作台近期资料，字段与前端 `LearningMaterial` 对齐。"""

    id: int
    title: str
    userId: str
    documentType: str
    source: str | None = None
    status: str
    parser: str | None = None
    documentSummary: str | None = None
    chunkCount: int = 0
    originalFilename: str | None = None
    originalFilePath: str | None = None
    storageType: str | None = None
    objectKey: str | None = None
    publicUrl: str | None = None
    latestProgress: RagProgressResponse | None = None
    progressEvents: list[RagProgressResponse] = Field(default_factory=list)
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


class DashboardResponse(BaseModel):
    """工作台聚合数据。"""

    materialCount: int = 0
    materialDelta7Days: int = 0
    evidenceCount: int = 0
    openErrorCount: int = 0
    errorCount30Days: int = 0
    recentTaskStartDate: str
    recentTaskEndDate: str
    recentTaskLimit: int
    recentMaterials: list[LearningMaterialPageResponse] = Field(default_factory=list)


class SystemSettingResponse(BaseModel):
    """设置页面展示项。"""

    key: str
    group: str
    label: str
    value: str
    sortOrder: int
