"""系统日志公开 API 的请求与响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LogEventCreateRequest(BaseModel):
    """新增业务事件日志的兼容请求模型。"""

    traceId: str | None = None
    sessionId: str | None = None
    userId: str | None = None
    source: str | None = "java"
    domain: str | None = "system"
    level: str | None = "INFO"
    module: str | None = None
    stage: str | None = None
    eventType: str | None = "business_state"
    action: str | None = None
    message: str | None = None
    route: str | None = None
    httpMethod: str | None = None
    requestPath: str | None = None
    statusCode: int | None = None
    success: bool | None = True
    durationMs: int | None = None
    materialId: int | None = None
    documentId: str | None = None
    parser: str | None = None
    clientTime: datetime | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class LogErrorCreateRequest(BaseModel):
    """新增错误日志的兼容请求模型。"""

    traceId: str | None = None
    sessionId: str | None = None
    userId: str | None = None
    source: str | None = "java"
    domain: str | None = "system"
    severity: str | None = "ERROR"
    module: str | None = None
    stage: str | None = None
    action: str | None = None
    errorType: str | None = None
    errorCode: str | None = None
    message: str | None = None
    stackTrace: str | None = None
    fingerprint: str | None = None
    route: str | None = None
    httpMethod: str | None = None
    requestPath: str | None = None
    statusCode: int | None = None
    durationMs: int | None = None
    materialId: int | None = None
    documentId: str | None = None
    parser: str | None = None
    clientTime: datetime | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class LogEventResponse(BaseModel):
    """前端展示的精简业务事件日志。"""

    id: int
    traceId: str
    source: str
    domain: str
    level: str
    module: str
    stage: str | None = None
    eventType: str
    action: str
    message: str | None = None
    success: bool
    durationMs: int | None = None
    materialId: int | None = None
    documentId: str | None = None
    parser: str | None = None
    contextJson: str
    createdAt: datetime | None = None


class LogErrorResponse(BaseModel):
    """前端展示的聚合错误日志。"""

    id: int
    traceId: str
    source: str
    domain: str
    severity: str
    module: str
    stage: str | None = None
    action: str | None = None
    errorType: str
    errorCode: str | None = None
    message: str
    fingerprint: str
    statusCode: int | None = None
    durationMs: int | None = None
    materialId: int | None = None
    documentId: str | None = None
    parser: str | None = None
    contextJson: str
    occurrenceCount: int
    status: str
    firstSeenAt: datetime | None = None
    lastSeenAt: datetime | None = None
    createdAt: datetime | None = None


class LogOverviewResponse(BaseModel):
    """日志概览统计。"""

    eventCount: int = 0
    errorCount: int = 0
    openErrorCount: int = 0
    frontendErrorCount: int = 0
    javaErrorCount: int = 0
    pythonErrorCount: int = 0
