"""Python 对外 RAG 控制面路由。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
import inspect
import logging
import os
from pathlib import Path
import tempfile
from typing import TypeVar

from fastapi import APIRouter, Depends, File, Form, Header, Query, UploadFile

from app.api.auth import bearer_token, get_auth_service
from app.auth.service import AuthService
from app.core.result import BusinessError, Result
from app.schemas.rag import Evidence, QueryResponse, QueryTaskResponse
from app.schemas.rag_control import (
    MaterialPreviewResponse,
    MaterialUploadChunkResponse,
    RagIndexTextPublicRequest,
    RagMaterialResponse,
    RagOverviewPublicResponse,
    RagQueryHistoryResponse,
    RagQueryPublicRequest,
)
from app.services.rag_control_service import RagControlService


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rag", tags=["RAG"])
T = TypeVar("T")


def get_rag_control_service() -> RagControlService:
    """提供默认控制服务，测试通过依赖替换避免连接真实 PostgreSQL。"""
    return RagControlService()


def current_rag_user_id(
    authorization: str | None = Header(default=None, alias="Authorization"),
    auth_service: AuthService = Depends(get_auth_service),
) -> str:
    """只从认证会话推导用户 ID，禁止信任客户端传入的 userId。"""
    return str(auth_service.current_user(bearer_token(authorization)).id)


@router.get("/overview", response_model=Result[RagOverviewPublicResponse])
def overview(
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[RagOverviewPublicResponse]:
    """读取当前登录用户的 RAG 概览。"""
    return Result.success(execute("获取 RAG 概览", lambda: service.overview(user_id)))


@router.get("/materials", response_model=Result[list[RagMaterialResponse]])
def materials(
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[list[RagMaterialResponse]]:
    """读取当前用户最近学习资料。"""
    return Result.success(execute("获取近期学习资料", lambda: service.list_materials(user_id)))


@router.get("/materials/{material_id}", response_model=Result[RagMaterialResponse])
def material(
    material_id: int,
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[RagMaterialResponse]:
    """读取当前用户拥有的一条资料状态。"""
    return Result.success(execute("查询学习资料解析状态", lambda: service.get_material(material_id, user_id)))


@router.get("/materials/{material_id}/evidences", response_model=Result[list[Evidence]])
def material_evidences(
    material_id: int,
    limit: int = Query(default=20),
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[list[Evidence]]:
    """读取已验证归属资料的 evidence 片段。"""
    return Result.success(execute("查询学习资料 evidence", lambda: service.list_evidences(material_id, user_id, limit)))


@router.get("/materials/{material_id}/preview", response_model=Result[MaterialPreviewResponse])
def material_preview(
    material_id: int,
    source: str | None = Query(default=None),
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[MaterialPreviewResponse]:
    """预览当前用户资料的受控文本原文件。"""
    return Result.success(execute("预览学习资料文本内容", lambda: service.preview_material(material_id, source, user_id)))


@router.post("/materials/text", response_model=Result[RagMaterialResponse])
def index_text(
    payload: RagIndexTextPublicRequest,
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[RagMaterialResponse]:
    """创建并索引当前用户粘贴的文本资料。"""
    return Result.success(execute("索引文本学习资料", lambda: service.index_text(payload, user_id)))


@router.post("/materials/upload", response_model=Result[RagMaterialResponse])
async def upload_material(
    file: UploadFile = File(...),
    high_precision: bool = Form(default=False, alias="highPrecision"),
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[RagMaterialResponse]:
    """把 multipart 文件流式写入临时目录，再交给对象存储适配器。"""
    temp_path: Path | None = None
    try:
        temp_path = await stream_upload_to_temp(file)
        value = execute("上传并索引学习资料", lambda: invoke_material_upload(service, file, temp_path, high_precision, user_id))
        return Result.success(value)
    finally:
        await file.close()
        remove_temp_path(temp_path)


@router.post("/materials/upload/chunk", response_model=Result[MaterialUploadChunkResponse])
async def upload_material_chunk(
    file: UploadFile = File(...),
    filename: str = Form(...),
    chunk_index: int = Form(..., alias="chunkIndex"),
    total_chunks: int = Form(..., alias="totalChunks"),
    total_size: int | None = Form(default=None, alias="totalSize"),
    upload_id: str | None = Form(default=None, alias="uploadId"),
    high_precision: bool = Form(default=False, alias="highPrecision"),
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[MaterialUploadChunkResponse]:
    """接收 0-based 分片，流式写入受控临时文件后由 Python 合并并索引。"""
    temp_path: Path | None = None
    try:
        temp_path = await stream_upload_to_temp(file)
        value = execute(
            "分片上传并索引学习资料",
            lambda: invoke_chunk_upload(
                service,
                file,
                temp_path,
                filename,
                upload_id,
                chunk_index,
                total_chunks,
                total_size,
                high_precision,
                user_id,
            ),
        )
        return Result.success(value)
    finally:
        await file.close()
        remove_temp_path(temp_path)


@router.post("/materials/{material_id}/reindex", response_model=Result[RagMaterialResponse])
def reindex_material(
    material_id: int,
    high_precision: bool = Query(default=False, alias="highPrecision"),
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[RagMaterialResponse]:
    """重新解析当前用户的受控原文件。"""
    return Result.success(
        execute("重建学习资料索引", lambda: service.reindex_material(material_id, high_precision, user_id))
    )


@router.post("/query", response_model=Result[QueryResponse])
def query(
    payload: RagQueryPublicRequest,
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[QueryResponse]:
    """在当前用户私有资料范围执行同步检索问答。"""
    return Result.success(execute("RAG 检索问答", lambda: service.query(payload, user_id)))


@router.get("/query/history", response_model=Result[list[RagQueryHistoryResponse]])
def query_history(
    start_date: date | None = Query(default=None, alias="startDate"),
    end_date: date | None = Query(default=None, alias="endDate"),
    limit: int | None = Query(default=5),
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[list[RagQueryHistoryResponse]]:
    """读取当前用户最近七日的查询快照。"""
    return Result.success(
        execute("查询 RAG 询问历史", lambda: service.list_query_history(user_id, start_date, end_date, limit))
    )


@router.post("/query/tasks", response_model=Result[QueryTaskResponse])
def start_query_task(
    payload: RagQueryPublicRequest,
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[QueryTaskResponse]:
    """创建归属当前用户的 RAG 查询任务。"""
    return Result.success(execute("创建 RAG 查询任务", lambda: service.start_query_task(payload, user_id)))


@router.get("/query/tasks/{task_id}", response_model=Result[QueryTaskResponse])
def get_query_task(
    task_id: str,
    user_id: str = Depends(current_rag_user_id),
    service: RagControlService = Depends(get_rag_control_service),
) -> Result[QueryTaskResponse]:
    """轮询当前用户的任务状态和已产生进度。"""
    return Result.success(execute("查询 RAG 查询任务状态", lambda: service.get_query_task(task_id, user_id)))


def execute(operation: str, action: Callable[[], T]) -> T:
    """将未预期异常转换为稳定中文业务错误，避免泄露数据库或模型细节。"""
    try:
        return action()
    except BusinessError:
        raise
    except Exception:
        logger.exception("%s失败", operation)
        raise BusinessError(f"{operation}失败") from None


async def stream_upload_to_temp(file: UploadFile) -> Path:
    """按固定缓冲区将请求文件写入受控临时目录，防止一次性占满内存。"""
    root = Path(os.getenv("EVIDENCE_UPLOAD_TEMP_ROOT", tempfile.gettempdir())).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    max_bytes = positive_upload_limit()
    suffix = Path(file.filename or "").suffix[:16]
    handle = tempfile.NamedTemporaryFile(prefix="rag-upload-", suffix=suffix, dir=root, delete=False)
    path = Path(handle.name)
    total = 0
    try:
        with handle:
            while True:
                block = await file.read(1024 * 1024)
                if not block:
                    break
                total += len(block)
                if total > max_bytes:
                    raise BusinessError("上传文件超过允许大小")
                handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())
        if total <= 0:
            raise BusinessError("上传文件不能为空")
        return path
    except Exception:
        remove_temp_path(path)
        raise


def invoke_material_upload(
    service: RagControlService,
    file: UploadFile,
    temp_path: Path,
    high_precision: bool,
    user_id: str,
) -> RagMaterialResponse:
    """优先走路径上传；旧测试替身没有路径参数时才读取小型测试文件。"""
    kwargs = {
        "filename": file.filename,
        "content_type": file.content_type,
        "high_precision": high_precision,
        "user_id": user_id,
    }
    parameters = inspect.signature(service.upload_material).parameters
    if "source_path" in parameters:
        return service.upload_material(source_path=temp_path, **kwargs)
    return service.upload_material(content=temp_path.read_bytes(), **kwargs)


def invoke_chunk_upload(
    service: RagControlService,
    file: UploadFile,
    temp_path: Path,
    filename: str,
    upload_id: str | None,
    chunk_index: int,
    total_chunks: int,
    total_size: int | None,
    high_precision: bool,
    user_id: str,
) -> MaterialUploadChunkResponse:
    """优先调用路径分片接口，兼容未升级的测试替身。"""
    kwargs = {
        "filename": filename,
        "upload_id": upload_id,
        "chunk_index": chunk_index,
        "total_chunks": total_chunks,
        "total_size": total_size,
        "content_type": file.content_type,
        "high_precision": high_precision,
        "user_id": user_id,
    }
    upload_chunk_file = getattr(service, "upload_chunk_file", None)
    if callable(upload_chunk_file):
        return upload_chunk_file(source_path=temp_path, **kwargs)
    return service.upload_chunk(content=temp_path.read_bytes(), **kwargs)


def positive_upload_limit() -> int:
    """读取上传上限，非法配置回退 512 MiB。"""
    try:
        value = int(os.getenv("RAG_UPLOAD_MAX_BYTES", str(512 * 1024 * 1024)))
    except ValueError:
        return 512 * 1024 * 1024
    return value if value > 0 else 512 * 1024 * 1024


def remove_temp_path(path: Path | None) -> None:
    """删除请求结束后的临时文件。"""
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return
