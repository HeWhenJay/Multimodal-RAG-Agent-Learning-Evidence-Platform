from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class MemoryScope(BaseModel):
    scopeType: str
    scopeId: str | None = None


class MemoryQueryRequest(BaseModel):
    taskId: str = Field(..., min_length=1)
    userId: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1)
    topK: int = Field(default=5, ge=1, le=20)
    namespaces: list[str] = Field(default_factory=list)
    memoryTypes: list[str] = Field(default_factory=list)
    allowedScopes: list[MemoryScope] = Field(default_factory=list)


class MemoryQueryResult(BaseModel):
    memoryId: str
    userId: str
    memoryType: str
    namespace: str
    scopeType: str
    scopeId: str | None = None
    subjectKey: str
    summary: str
    status: str
    confidence: float
    importance: float
    score: float
    deletedAt: str | None = None


class MemoryQueryResponse(BaseModel):
    memories: list[MemoryQueryResult] = Field(default_factory=list)
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class MemoryExtractRequest(BaseModel):
    taskId: str = Field(..., min_length=1)
    userId: str = Field(..., min_length=1)
    taskInput: dict[str, Any] = Field(default_factory=dict)
    draft: dict[str, Any] = Field(default_factory=dict)
    final: dict[str, Any] = Field(default_factory=dict)
    toolObservations: list[dict[str, Any]] = Field(default_factory=list)


class MemoryCandidate(BaseModel):
    memoryType: str = "EPISODIC"
    namespace: str = "agent_task"
    scopeType: str = "USER"
    scopeId: str | None = None
    subjectKey: str = "recent_task_insight"
    content: str
    summary: str
    evidenceRefs: list[dict[str, Any]] = Field(default_factory=list)
    sourceTaskId: str | None = None
    sourceToolCallId: str | None = None
    sourceReviewId: str | None = None
    sourceHash: str | None = None
    confidence: float = 0.6
    importance: float = 0.55
    sensitivityLevel: str = "LOW"
    consentSource: str = "AGENT_INFERRED"


class MemoryExtractResponse(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    provider: str = "deterministic-memory-extractor"


class MemoryConflictRequest(BaseModel):
    userId: str = Field(..., min_length=1)
    candidate: MemoryCandidate
    existingMemories: list[dict[str, Any]] = Field(default_factory=list)


class MemoryConflictResponse(BaseModel):
    relationType: str
    decision: str
    reason: str
    confidence: float = 0.6


class MemoryIndexUpsertRequest(BaseModel):
    memoryId: str = Field(..., min_length=1)
    userId: str = Field(..., min_length=1)
    memoryType: str
    namespace: str
    scopeType: str
    scopeId: str | None = None
    subjectKey: str
    content: str
    summary: str
    retrievalText: str
    status: str
    confidence: float = 0.5
    importance: float = 0.5
    sensitivityLevel: str = "LOW"


class MemoryIndexUpsertResponse(BaseModel):
    memoryId: str
    indexed: bool
    status: str
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class MemoryIndexDeleteRequest(BaseModel):
    memoryId: str = Field(..., min_length=1)
    userId: str = Field(..., min_length=1)


class MemoryIndexDeleteResponse(BaseModel):
    memoryId: str
    deleted: bool
    diagnostics: dict[str, Any] = Field(default_factory=dict)
