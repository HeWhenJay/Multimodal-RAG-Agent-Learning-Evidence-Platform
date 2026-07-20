from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.rag import IndexResponse, ProgressEvent


class KafkaEnvelope(BaseModel):
    schemaVersion: str = "1.0"
    messageId: str
    originalMessageId: str | None = None
    messageType: str
    eventTime: datetime | str
    producer: str
    traceId: str | None = None
    correlationId: str | None = None
    partitionKey: str
    idempotencyKey: str
    attempt: int = 0
    notBefore: datetime | str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class StorageSourceRef(BaseModel):
    """Python 存储层可直接打开的受控原文件引用。"""

    type: Literal["STORAGE"] = "STORAGE"
    filename: str | None = None
    contentType: str | None = None
    storageType: str | None = None
    sourcePath: str | None = None
    objectKey: str | None = None
    publicUrl: str | None = None


class InlineTextSourceRef(BaseModel):
    type: Literal["INLINE_TEXT"] = "INLINE_TEXT"
    parser: str = "python-manual-text"


class IndexRequestPayload(BaseModel):
    jobId: str
    operation: Literal["INDEX_UPLOAD", "REINDEX", "INDEX_TEXT"]
    materialId: int
    canonicalDocumentId: str
    stagingDocumentId: str
    userId: str
    title: str
    documentType: str
    source: str = "upload"
    visibilityScope: str = "private"
    stagingVisibilityScope: str = "staging"
    highPrecision: bool = False
    requestVersion: int
    sourceRef: StorageSourceRef | InlineTextSourceRef
    text: str | None = None


class PromoteRequestPayload(BaseModel):
    jobId: str
    materialId: int
    canonicalDocumentId: str
    stagingDocumentId: str
    requestVersion: int
    chunkCount: int | None = None


class IndexResultPayload(IndexResponse):
    jobId: str
    materialId: int
    canonicalDocumentId: str
    stagingDocumentId: str
    requestVersion: int
    errorCode: str | None = None
    errorMessage: str | None = None


class IndexProgressPayload(ProgressEvent):
    jobId: str
    materialId: int
    canonicalDocumentId: str
    stagingDocumentId: str
    userId: str
    parser: str | None = None
    requestVersion: int
    progressSequence: int


class PromoteResultPayload(BaseModel):
    jobId: str
    materialId: int
    canonicalDocumentId: str
    stagingDocumentId: str
    requestVersion: int
    status: Literal["SUCCEEDED", "FAILED"]
    alreadyPromoted: bool = False
    canonicalChunkCount: int = 0
    stagingChunkCount: int = 0
    errorCode: str | None = None
    errorMessage: str | None = None
