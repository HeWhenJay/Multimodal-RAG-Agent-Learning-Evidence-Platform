from typing import Any

from pydantic import BaseModel, Field


class IndexTextRequest(BaseModel):
    documentId: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    documentType: str = "text"
    source: str = "manual"
    userId: str = "demo-user"
    visibilityScope: str = "private"
    language: str = "zh-CN"
    parser: str = "manual-text"
    content: str = Field(..., min_length=1)


class IndexResponse(BaseModel):
    documentId: str
    title: str
    status: str
    chunkCount: int
    parser: str
    documentSummary: str


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1)
    topK: int = Field(default=5, ge=1, le=20)
    metadataFilter: dict[str, Any] | None = None


class Evidence(BaseModel):
    evidenceId: str
    documentId: str
    title: str
    snippet: str
    source: str
    sectionName: str
    documentType: str
    score: float


class QueryResponse(BaseModel):
    answer: str
    expandedQueries: list[str]
    evidences: list[Evidence]
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class OverviewResponse(BaseModel):
    documentCount: int
    chunkCount: int
    evidenceCount: int
    lastIndexedTitle: str | None = None

