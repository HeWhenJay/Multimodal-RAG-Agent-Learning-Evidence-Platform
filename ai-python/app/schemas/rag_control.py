"""Python 公开 RAG 控制面的请求与响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.rag import Evidence, ProgressEvent, QueryResponse, QueryTaskResponse


MaterialStatus = Literal["PENDING", "PARSING", "READY", "PARTIAL", "FAILED", "REINDEXING"]


class RagIndexTextPublicRequest(BaseModel):
    """前端提交手工文本资料的公开请求，不接受用户 ID。"""

    title: str
    documentType: str = "markdown"
    source: str = "manual"
    content: str

    @field_validator("title", "content")
    @classmethod
    def reject_blank_text(cls, value: str, info) -> str:
        """保持 Java 接口使用的中文空值错误。"""
        if not isinstance(value, str) or not value.strip():
            label = "标题" if info.field_name == "title" else "内容"
            raise ValueError(f"{label}不能为空")
        return value.strip()


class RagQueryPublicRequest(BaseModel):
    """公开查询请求；身份和可见范围由服务端强制注入。"""

    question: str
    topK: int | None = 5
    candidateMultiplier: int | None = 4
    metadataFilter: dict[str, Any] = Field(default_factory=dict)

    @field_validator("question")
    @classmethod
    def reject_blank_question(cls, value: str) -> str:
        """拒绝空问题，避免将无效查询写入历史。"""
        if not isinstance(value, str) or not value.strip():
            raise ValueError("问题不能为空")
        return value.strip()


class RagOverviewPublicResponse(BaseModel):
    """与前端 `RagOverview` 对齐的用户级概览。"""

    materialCount: int = 0
    chunkCount: int = 0
    evidenceCount: int = 0
    lastIndexedTitle: str | None = None


class RagMaterialResponse(BaseModel):
    """学习资料及其最近可见进度。"""

    id: int
    title: str
    userId: str
    documentType: str
    source: str | None = None
    status: MaterialStatus
    parser: str | None = None
    documentSummary: str | None = None
    chunkCount: int = 0
    originalFilename: str | None = None
    originalFilePath: str | None = None
    storageType: str | None = None
    objectKey: str | None = None
    publicUrl: str | None = None
    latestProgress: ProgressEvent | None = None
    progressEvents: list[ProgressEvent] = Field(default_factory=list)
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


class MaterialPreviewResponse(BaseModel):
    """文本类原文件预览结果。"""

    materialId: int
    title: str
    documentType: str
    source: str | None = None
    contentType: str
    content: str


class MaterialUploadChunkResponse(BaseModel):
    """分片上传的可续传状态。"""

    uploadId: str
    filename: str
    chunkIndex: int
    totalChunks: int
    receivedChunks: int
    nextChunkIndex: int
    status: str
    message: str
    completed: bool
    material: RagMaterialResponse | None = None


class RagQueryHistoryResponse(BaseModel):
    """持久化查询历史，字段与既有 React 类型一致。"""

    id: int
    taskId: str | None = None
    question: str
    answer: str | None = None
    answerStatus: str = "REFUSED"
    refusalReason: str | None = None
    refusalPolicy: str = "STRICT_EVIDENCE_GUARD_V1"
    confidence: float = 0.0
    supportingEvidenceIds: list[str] = Field(default_factory=list)
    refusalMessage: str | None = None
    status: str
    topK: int = 5
    evidenceCount: int = 0
    expandedQueries: list[str] = Field(default_factory=list)
    evidences: list[Evidence] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    progressEvents: list[ProgressEvent] = Field(default_factory=list)
    errorMessage: str | None = None
    durationMs: int | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None


PublicQueryResponse = QueryResponse
PublicQueryTaskResponse = QueryTaskResponse
