from fastapi import APIRouter, Header, HTTPException

from agents.memory.memory_service import AgentMemoryService
from app.core.agent_internal_token import resolve_agent_internal_token
from app.schemas.agent_memory import (
    MemoryConflictRequest,
    MemoryConflictResponse,
    MemoryExtractRequest,
    MemoryExtractResponse,
    MemoryIndexDeleteRequest,
    MemoryIndexDeleteResponse,
    MemoryIndexUpsertRequest,
    MemoryIndexUpsertResponse,
    MemoryQueryRequest,
    MemoryQueryResponse,
)


router = APIRouter(prefix="/internal/agent/memory", tags=["Agent Memory"])


@router.post("/query", response_model=MemoryQueryResponse)
def query_memory(
    request: MemoryQueryRequest,
    x_agent_internal_token: str | None = Header(default=None, alias="X-Agent-Internal-Token"),
) -> MemoryQueryResponse:
    """执行 Agent 记忆检索，只接受 Java 已授权过滤条件。"""
    require_internal_token(x_agent_internal_token)
    return AgentMemoryService().query(request)


@router.post("/extract", response_model=MemoryExtractResponse)
def extract_memory(
    request: MemoryExtractRequest,
    x_agent_internal_token: str | None = Header(default=None, alias="X-Agent-Internal-Token"),
) -> MemoryExtractResponse:
    """从任务快照提炼待确认记忆候选。"""
    require_internal_token(x_agent_internal_token)
    return AgentMemoryService().extract(request)


@router.post("/conflicts", response_model=MemoryConflictResponse)
def check_memory_conflicts(
    request: MemoryConflictRequest,
    x_agent_internal_token: str | None = Header(default=None, alias="X-Agent-Internal-Token"),
) -> MemoryConflictResponse:
    """判断候选和旧记忆之间的最小冲突关系。"""
    require_internal_token(x_agent_internal_token)
    return AgentMemoryService().conflicts(request)


@router.post("/index/upsert", response_model=MemoryIndexUpsertResponse)
def upsert_memory_index(
    request: MemoryIndexUpsertRequest,
    x_agent_internal_token: str | None = Header(default=None, alias="X-Agent-Internal-Token"),
) -> MemoryIndexUpsertResponse:
    """写入或更新 Java 已确认记忆的检索索引。"""
    require_internal_token(x_agent_internal_token)
    return AgentMemoryService().upsert_index(request)


@router.post("/index/delete", response_model=MemoryIndexDeleteResponse)
def delete_memory_index(
    request: MemoryIndexDeleteRequest,
    x_agent_internal_token: str | None = Header(default=None, alias="X-Agent-Internal-Token"),
) -> MemoryIndexDeleteResponse:
    """删除或停用 Java 已确认记忆的检索索引。"""
    require_internal_token(x_agent_internal_token)
    return AgentMemoryService().delete_index(request)


def require_internal_token(token: str | None) -> str:
    """校验 Java 调 Python Memory Service 的内部令牌。"""
    configured = resolve_agent_internal_token()
    if not configured or token != configured:
        raise HTTPException(status_code=401, detail="AGENT_INTERNAL_TOKEN_INVALID")
    return configured
