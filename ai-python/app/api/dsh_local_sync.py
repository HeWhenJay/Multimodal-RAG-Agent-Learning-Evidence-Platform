"""当前项目主动同步 DSH 插件本地知识库的登录接口。"""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import TypeVar

from fastapi import APIRouter, Depends

from app.core.current_user import CurrentUser
from app.core.result import BusinessError, Result
from app.core.result_route import ResultValidationRoute
from app.dsh_local_sync import DshLocalSyncService
from app.schemas.dsh_local_sync import DshLocalSyncResult, DshLocalSyncStatus


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dsh-local-sync", tags=["DSH 本地同步"], route_class=ResultValidationRoute)
T = TypeVar("T")


# 构造服务端配置的个人适配器，测试通过依赖替换阻断真实文件和数据库访问。
def get_dsh_local_sync_service() -> DshLocalSyncService:
    """提供项目主动拉取 DSH 本地 v2 资料库的默认服务。"""
    return DshLocalSyncService()


@router.get("/status", response_model=Result[DshLocalSyncStatus])
def status(
    current_user: CurrentUser,
    service: DshLocalSyncService = Depends(get_dsh_local_sync_service),
) -> Result[DshLocalSyncStatus]:
    """返回当前认证用户可见的同步统计，不暴露资料正文或服务器路径。"""
    return Result.success(execute("获取 DSH 本地同步状态", lambda: service.status(str(current_user.id))))


@router.post("/sync", response_model=Result[DshLocalSyncResult])
def sync(
    current_user: CurrentUser,
    service: DshLocalSyncService = Depends(get_dsh_local_sync_service),
) -> Result[DshLocalSyncResult]:
    """由项目服务端读取固定本地库并同步到当前认证用户。"""
    return Result.success(execute("同步 DSH 本地知识库", lambda: service.sync(str(current_user.id))))


# 将基础设施异常统一收敛为中文业务错误，同时保留受控领域提示。
def execute(operation: str, action: Callable[[], T]) -> T:
    """执行同步操作并避免向浏览器暴露路径、SQL 或系统异常。"""
    try:
        return action()
    except BusinessError:
        raise
    except Exception:
        logger.exception("%s失败", operation)
        raise BusinessError(f"{operation}失败") from None
