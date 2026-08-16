"""供本机 DSH 知识复习插件使用的无登录 RAG 路由。"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.core.result import BusinessError, Result
from app.schemas.rag import QueryResponse
from app.schemas.rag_control import (
    DshPluginMaterialPageResponse,
    MaterialPreviewResponse,
    RagIndexRemoteVideoPublicRequest,
    RagMaterialResponse,
    RagOverviewPublicResponse,
    RagQueryPublicRequest,
)
from app.services.rag_control_service import RagControlService
from app.api.rag_control import execute, execute_async, get_rag_control_service, query_service_async


router = APIRouter(prefix="/api/dsh-plugin/rag", tags=["DSH 插件 RAG"])


def dsh_plugin_user_id(request: Request) -> str:
    """仅允许本机 DSH 调用，并为插件资料使用固定隔离分区。"""
    if os.getenv("RAG_DSH_PLUGIN_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        raise BusinessError("DSH 插件 RAG 接口已关闭")
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}: 
        raise BusinessError("DSH 插件 RAG 接口仅允许本机访问")
    return os.getenv("DSH_PLUGIN_RAG_USER_ID", "dsh-plugin").strip() or "dsh-plugin"


@router.get("/overview", response_model=Result[RagOverviewPublicResponse])
def overview(
    user_id: Annotated[str, Depends(dsh_plugin_user_id)],
    service: Annotated[RagControlService, Depends(get_rag_control_service)],
) -> Result[RagOverviewPublicResponse]:
    """读取固定 DSH 用户分区的概览统计。"""
    return Result.success(execute("获取 DSH 插件资料概览", lambda: service.overview(user_id)))


@router.get("/materials", response_model=Result[DshPluginMaterialPageResponse])
def materials(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    query: str | None = Query(default=None, max_length=200),
    user_id: Annotated[str, Depends(dsh_plugin_user_id)] = "",
    service: Annotated[RagControlService, Depends(get_rag_control_service)] = None,
) -> Result[DshPluginMaterialPageResponse]:
    """按更新时间和 ID 游标分页返回最小资料元数据，不返回原文。"""
    return Result.success(
        execute("分页读取 DSH 插件资料", lambda: service.list_dsh_materials(user_id, cursor=cursor, limit=limit, query=query))
    )


@router.get("/materials/{material_id}/preview", response_model=Result[MaterialPreviewResponse])
def material_preview(
    material_id: int,
    source: str | None = Query(default=None),
    user_id: Annotated[str, Depends(dsh_plugin_user_id)] = "",
    service: Annotated[RagControlService, Depends(get_rag_control_service)] = None,
) -> Result[MaterialPreviewResponse]:
    """仅在点击单条资料后读取受控原文或索引预览。"""
    return Result.success(execute("预览 DSH 插件资料", lambda: service.preview_material(material_id, source, user_id)))


@router.post("/query", response_model=Result[QueryResponse])
async def query(
    payload: RagQueryPublicRequest,
    user_id: Annotated[str, Depends(dsh_plugin_user_id)],
    service: Annotated[RagControlService, Depends(get_rag_control_service)],
) -> Result[QueryResponse]:
    """在 DSH 插件固定资料分区中执行严格 evidence RAG 查询。"""
    return Result.success(
        await execute_async(
            "DSH 插件 RAG 检索问答",
            lambda: query_service_async(service, payload, user_id),
        )
    )


@router.post("/materials/url", response_model=Result[RagMaterialResponse])
def import_remote_video(
    payload: RagIndexRemoteVideoPublicRequest,
    user_id: Annotated[str, Depends(dsh_plugin_user_id)],
    service: Annotated[RagControlService, Depends(get_rag_control_service)],
) -> Result[RagMaterialResponse]:
    """把用户确认可学习的公开视频加入 DSH 插件固定资料分区。"""
    return Result.success(execute("DSH 插件接入公开视频", lambda: service.import_remote_video(payload, user_id)))
