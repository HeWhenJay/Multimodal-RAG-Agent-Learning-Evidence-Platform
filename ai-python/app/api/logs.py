"""系统日志公开 FastAPI 路由。"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import TypeVar

from fastapi import APIRouter, Depends, Header, Query

from app.core.result import BusinessError, Result
from app.core.result_route import ResultValidationRoute
from app.logs.service import LogService
from app.schemas.logs import (
    LogErrorCreateRequest,
    LogErrorResponse,
    LogEventCreateRequest,
    LogEventResponse,
    LogOverviewResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/logs", tags=["系统日志"], route_class=ResultValidationRoute)
T = TypeVar("T")


def get_log_service() -> LogService:
    """提供默认日志服务，测试可通过依赖替换注入内存仓储。"""
    return LogService()


@router.post("/events", response_model=Result[int | None])
def record_event(
    payload: LogEventCreateRequest,
    service: LogService = Depends(get_log_service),
) -> Result[int | None]:
    """写入一条普通业务事件日志。"""
    return Result.success(execute("记录业务事件日志", lambda: service.record_event(payload)))


@router.post("/events/batch", response_model=Result[int])
def record_events(
    payload: list[LogEventCreateRequest],
    service: LogService = Depends(get_log_service),
) -> Result[int]:
    """按配置上限批量写入普通业务事件日志。"""
    return Result.success(execute("批量记录业务事件日志", lambda: service.record_events(payload)))


@router.post("/errors", response_model=Result[int | None])
def record_error(
    payload: LogErrorCreateRequest,
    service: LogService = Depends(get_log_service),
) -> Result[int | None]:
    """写入一条错误日志或合并同指纹错误。"""
    return Result.success(execute("记录错误日志", lambda: service.record_error(payload)))


@router.post("/internal/events", response_model=Result[int | None])
def record_internal_event(
    payload: LogEventCreateRequest,
    token: str | None = Header(default=None, alias="X-Internal-Log-Token"),
    service: LogService = Depends(get_log_service),
) -> Result[int | None]:
    """接收 Python worker 的受令牌保护事件和 RAG 进度。"""
    return Result.success(execute("记录内部业务事件日志", lambda: service.record_internal_event(token, payload)))


@router.post("/internal/errors", response_model=Result[int | None])
def record_internal_error(
    payload: LogErrorCreateRequest,
    token: str | None = Header(default=None, alias="X-Internal-Log-Token"),
    service: LogService = Depends(get_log_service),
) -> Result[int | None]:
    """接收 Python worker 的受令牌保护错误日志。"""
    return Result.success(execute("记录内部错误日志", lambda: service.record_internal_error(token, payload)))


@router.get("/events/recent", response_model=Result[list[LogEventResponse]])
def recent_events(
    limit: str | None = Query(default=None),
    service: LogService = Depends(get_log_service),
) -> Result[list[LogEventResponse]]:
    """读取最近普通事件日志，非法条数保持业务错误信封。"""
    return Result.success(execute("查询最近业务事件日志", lambda: service.list_recent_events(limit)))


@router.get("/errors/recent", response_model=Result[list[LogErrorResponse]])
def recent_errors(
    limit: str | None = Query(default=None),
    service: LogService = Depends(get_log_service),
) -> Result[list[LogErrorResponse]]:
    """读取最近错误聚合日志，非法条数保持业务错误信封。"""
    return Result.success(execute("查询最近错误日志", lambda: service.list_recent_errors(limit)))


@router.get("/overview", response_model=Result[LogOverviewResponse])
def overview(
    days: str | None = Query(default=None),
    service: LogService = Depends(get_log_service),
) -> Result[LogOverviewResponse]:
    """读取指定天数内的全局日志概览。"""
    return Result.success(execute("查询日志概览", lambda: service.overview(days)))


def execute(operation: str, action: Callable[[], T]) -> T:
    """将意外仓储异常转换为不会泄露实现细节的中文业务错误。"""
    try:
        return action()
    except BusinessError:
        raise
    except Exception:
        logger.exception("%s失败", operation)
        raise BusinessError(f"{operation}失败") from None
