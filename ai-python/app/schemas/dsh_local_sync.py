"""当前项目主动同步 DSH 插件本地资料库的公开响应模型。"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class DshLocalSyncStatus(BaseModel):
    """返回项目可见的本地库路径和最近同步统计，不返回资料正文。"""

    configured: bool
    readable: bool
    documentCount: int = Field(default=0, ge=0)
    syncedDocumentCount: int = Field(default=0, ge=0)
    pendingDocumentCount: int = Field(default=0, ge=0)
    lastSyncedAt: datetime | None = None
    message: str


class DshLocalSyncItem(BaseModel):
    """一条插件资料同步到项目后的最小结果。"""

    documentId: str
    materialId: int | None = None
    title: str
    action: str
    status: str
    message: str | None = None


class DshLocalSyncResult(BaseModel):
    """一次登录用户主动同步的汇总。"""

    scannedCount: int = Field(ge=0)
    createdCount: int = Field(ge=0)
    updatedCount: int = Field(ge=0)
    skippedCount: int = Field(ge=0)
    failedCount: int = Field(ge=0)
    items: list[DshLocalSyncItem] = Field(default_factory=list)
