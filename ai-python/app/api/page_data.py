"""工作台与设置页的公开 FastAPI 路由。"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import TypeVar

from fastapi import APIRouter, Depends, Query

from app.core.current_user import CurrentUser
from app.core.result import BusinessError, Result
from app.core.result_route import ResultValidationRoute
from app.page_data.service import PageDataService
from app.schemas.page_data import DashboardResponse, SystemSettingResponse


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/page-data", tags=["页面数据"], route_class=ResultValidationRoute)
T = TypeVar("T")


def get_page_data_service() -> PageDataService:
    """提供默认页面数据服务，测试可注入内存仓储。"""
    return PageDataService()


@router.get("/dashboard", response_model=Result[DashboardResponse])
def dashboard(
    current_user: CurrentUser,
    start_date: str | None = Query(default=None, alias="startDate"),
    end_date: str | None = Query(default=None, alias="endDate"),
    recent_days: str | None = Query(default=None, alias="recentDays"),
    recent_limit: str | None = Query(default=None, alias="recentLimit"),
    service: PageDataService = Depends(get_page_data_service),
) -> Result[DashboardResponse]:
    """读取当前认证用户的工作台聚合数据。"""
    return Result.success(
        execute(
            "获取工作台页面数据",
            lambda: service.dashboard(
                user_id=str(current_user.id),
                start_date=start_date,
                end_date=end_date,
                recent_days=recent_days,
                recent_limit=recent_limit,
            ),
        )
    )


@router.get("/settings", response_model=Result[list[SystemSettingResponse]])
def settings(
    service: PageDataService = Depends(get_page_data_service),
) -> Result[list[SystemSettingResponse]]:
    """读取无需登录即可展示的系统设置项。"""
    return Result.success(execute("获取系统设置页面数据", service.system_settings))


def execute(operation: str, action: Callable[[], T]) -> T:
    """将非业务异常转换为稳定中文错误，避免暴露数据库细节。"""
    try:
        return action()
    except BusinessError:
        raise
    except Exception:
        logger.exception("%s失败", operation)
        raise BusinessError(f"{operation}失败") from None
