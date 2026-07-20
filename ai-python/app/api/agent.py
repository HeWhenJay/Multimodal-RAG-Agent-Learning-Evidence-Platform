"""纯 Python Agent 与记忆公开 API。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
import json
import logging
import os
from typing import Any, TypeVar

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from app.agent_runtime.models import TERMINAL_TASK_STATUSES
from app.agent_runtime.service import AgentBusinessError, AgentRuntimeService
from app.api.auth import get_auth_service
from app.auth.service import AuthService
from app.core.current_user import CurrentUser
from app.core.result import BusinessError, Result


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/agent", tags=["Agent"])
T = TypeVar("T")


def get_agent_runtime_service() -> AgentRuntimeService:
    """提供默认持久化服务；测试可替换为内存仓储。"""
    return AgentRuntimeService()


def current_agent_user_id(user: CurrentUser) -> str:
    """只从 Python 登录会话读取当前用户 ID。"""
    return str(user.id)


def current_stream_user_id(
    token: str | None = Query(default=None),
    auth_service: AuthService = Depends(get_auth_service),
) -> str:
    """兼容 EventSource 的 query token，并且不记录令牌内容。"""
    return str(auth_service.current_user(token).id)


@router.post("/tasks", response_model=Result[dict[str, Any]])
def create_task(
    payload: Any = Body(default_factory=dict),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """创建任务与首条用户消息，耐久 worker 会异步领取执行。"""
    return Result.success(execute("创建 Agent 任务", lambda: service.create_task(user_id, object_payload(payload))))


@router.get("/tasks", response_model=Result[list[dict[str, Any]]])
def list_tasks(
    limit: str | None = Query(default=None),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[list[dict[str, Any]]]:
    """查询当前登录用户最近的 Agent 会话。"""
    return Result.success(execute("查询 Agent 任务", lambda: service.list_tasks(user_id, integer_or_none(limit))))


@router.get("/tasks/{task_id}/messages", response_model=Result[dict[str, Any]])
def list_task_messages(
    task_id: str,
    before_sequence_no: str | None = Query(default=None, alias="beforeSequenceNo"),
    after_sequence_no: str | None = Query(default=None, alias="afterSequenceNo"),
    limit: str | None = Query(default=None),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """按持久 sequenceNo 分页读取当前用户消息。"""
    return Result.success(
        execute(
            "查询 Agent 消息",
            lambda: service.list_messages(
                task_id,
                user_id,
                integer_or_none(before_sequence_no),
                integer_or_none(after_sequence_no),
                integer_or_none(limit),
            ),
        )
    )


@router.get("/tasks/{task_id}", response_model=Result[dict[str, Any]])
def get_task(
    task_id: str,
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """查询当前用户任务详情、审批、操作和最近消息窗口。"""
    return Result.success(execute("查询 Agent 任务详情", lambda: service.get_task(task_id, user_id)))


@router.post("/tasks/{task_id}/folder", response_model=Result[dict[str, Any]])
def move_task_folder(
    task_id: str,
    payload: Any = Body(default_factory=dict),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """移动当前用户会话，空 folderId 表示回到未分类。"""
    body = object_payload(payload)
    folder_id = nullable_text(body.get("folderId"))
    return Result.success(execute("移动 Agent 会话", lambda: service.move_conversation(task_id, user_id, folder_id)))


@router.post("/tasks/{task_id}/reviews/{review_id}/decide", response_model=Result[dict[str, Any]])
def decide_review(
    task_id: str,
    review_id: str,
    payload: Any = Body(default_factory=dict),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """持久化审批决定；HTTP 请求不会同步运行 LangGraph。"""
    return Result.success(
        execute("提交 Agent 审批", lambda: service.decide_review(task_id, review_id, user_id, object_payload(payload)))
    )


@router.post("/operations/{operation_id}/undo", response_model=Result[dict[str, Any]])
def undo_operation(
    operation_id: str,
    payload: Any = Body(default_factory=dict),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """按当前用户和幂等键撤销仍在窗口内的操作。"""
    return Result.success(
        execute("撤销 Agent 操作", lambda: service.undo_operation(operation_id, user_id, object_payload(payload)))
    )


@router.get("/conversations/tree", response_model=Result[dict[str, Any]])
def conversation_tree(
    limit_per_folder: str | None = Query(default=None, alias="limitPerFolder"),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """查询用户私有文件夹和未分类会话树。"""
    return Result.success(
        execute("查询 Agent 会话树", lambda: service.conversation_tree(user_id, integer_or_none(limit_per_folder)))
    )


@router.post("/conversation-folders", response_model=Result[dict[str, Any]])
def create_folder(
    payload: Any = Body(default_factory=dict),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """创建当前用户私有会话文件夹。"""
    return Result.success(execute("创建 Agent 文件夹", lambda: service.create_folder(user_id, object_payload(payload))))


@router.put("/conversation-folders/{folder_id}", response_model=Result[dict[str, Any]])
def update_folder(
    folder_id: str,
    payload: Any = Body(default_factory=dict),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """更新当前用户私有文件夹。"""
    return Result.success(
        execute("更新 Agent 文件夹", lambda: service.update_folder(folder_id, user_id, object_payload(payload)))
    )


@router.delete("/conversation-folders/{folder_id}", response_model=Result[None])
def delete_folder(
    folder_id: str,
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[None]:
    """删除文件夹并将其会话移动到未分类。"""
    execute("删除 Agent 文件夹", lambda: service.delete_folder(folder_id, user_id))
    return Result.success()


@router.get("/tools", response_model=Result[list[dict[str, Any]]])
def list_tools(
    _: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[list[dict[str, Any]]]:
    """返回当前阶段受控工具白名单。"""
    return Result.success(execute("查询 Agent 工具", service.list_tools))


@router.get("/memories", response_model=Result[list[dict[str, Any]]])
def list_memories(
    status: str | None = Query(default=None),
    memory_type: str | None = Query(default=None, alias="memoryType"),
    namespace: str | None = Query(default=None),
    scope_type: str | None = Query(default=None, alias="scopeType"),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[list[dict[str, Any]]]:
    """按状态和元数据过滤当前用户的记忆。"""
    filters = {"status": status or "", "memoryType": memory_type or "", "namespace": namespace or "", "scopeType": scope_type or ""}
    return Result.success(execute("查询 Agent 记忆", lambda: service.list_memories(user_id, filters)))


@router.post("/memories", response_model=Result[dict[str, Any]])
def create_memory(
    payload: Any = Body(default_factory=dict),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """创建当前用户显式授权的长期记忆。"""
    return Result.success(execute("创建 Agent 记忆", lambda: service.create_memory(user_id, object_payload(payload))))


@router.get("/memories/{memory_id}", response_model=Result[dict[str, Any]])
def get_memory(
    memory_id: str,
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """查询当前用户的一条记忆，不泄露跨用户资源是否存在。"""
    return Result.success(execute("查询 Agent 记忆详情", lambda: service.get_memory(memory_id, user_id)))


@router.post("/memories/{memory_id}/confirm", response_model=Result[dict[str, Any]])
def confirm_memory(
    memory_id: str,
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """确认候选记忆并将其激活到 Python 检索范围。"""
    return Result.success(execute("确认 Agent 记忆", lambda: service.confirm_memory(memory_id, user_id)))


@router.post("/memories/{memory_id}/reject", response_model=Result[dict[str, Any]])
def reject_memory(
    memory_id: str,
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """拒绝待审 Agent 记忆。"""
    return Result.success(execute("拒绝 Agent 记忆", lambda: service.reject_memory(memory_id, user_id)))


@router.patch("/memories/{memory_id}", response_model=Result[dict[str, Any]])
def patch_memory(
    memory_id: str,
    payload: Any = Body(default_factory=dict),
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """修改记忆内容或收窄作用域，并生成新版本。"""
    return Result.success(
        execute("修改 Agent 记忆", lambda: service.patch_memory(memory_id, user_id, object_payload(payload)))
    )


@router.post("/memories/{memory_id}/archive", response_model=Result[dict[str, Any]])
def archive_memory(
    memory_id: str,
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """归档记忆并使其退出默认检索。"""
    return Result.success(execute("归档 Agent 记忆", lambda: service.archive_memory(memory_id, user_id)))


@router.delete("/memories/{memory_id}", response_model=Result[dict[str, Any]])
def delete_memory(
    memory_id: str,
    user_id: str = Depends(current_agent_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> Result[dict[str, Any]]:
    """擦除记忆正文并保留最小审计链。"""
    return Result.success(execute("删除 Agent 记忆", lambda: service.delete_memory(memory_id, user_id)))


@router.get("/tasks/{task_id}/stream")
async def stream_task(
    task_id: str,
    request: Request,
    user_id: str = Depends(current_stream_user_id),
    service: AgentRuntimeService = Depends(get_agent_runtime_service),
) -> StreamingResponse:
    """从 PostgreSQL 轮询任务和消息投影，提供可重连 SSE。"""
    initial = execute("订阅 Agent 任务事件", lambda: service.get_task(task_id, user_id))

    async def events() -> AsyncIterator[str]:
        snapshot = initial
        last_fingerprint = task_fingerprint(snapshot)
        last_sequence = latest_sequence(snapshot)
        yield sse_event("task", snapshot)
        if task_terminal(snapshot):
            yield sse_event("done", snapshot)
            return
        while not await request.is_disconnected():
            await asyncio.sleep(stream_poll_seconds())
            try:
                new_messages = service.list_messages(task_id, user_id, None, last_sequence or None, 100)
                for message in new_messages.get("messages") or []:
                    sequence_no = integer_or_none(message.get("sequenceNo"))
                    if sequence_no is not None:
                        last_sequence = max(last_sequence, sequence_no)
                    yield sse_event("agent_event", message_event(task_id, message))
                snapshot = service.get_task(task_id, user_id)
            except BusinessError as exc:
                yield sse_event("agent_event", {"taskId": task_id, "eventType": "TASK_STREAM_FAILED", "status": "FAILED", "message": exc.message})
                return
            fingerprint = task_fingerprint(snapshot)
            if fingerprint != last_fingerprint:
                yield sse_event("task", snapshot)
                last_fingerprint = fingerprint
            if task_terminal(snapshot):
                yield sse_event("done", snapshot)
                return

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def execute(operation: str, action: Callable[[], T]) -> T:
    """将未预期异常转换为不泄露内部细节的业务错误。"""
    try:
        return action()
    except BusinessError:
        raise
    except Exception:
        logger.exception("%s失败", operation)
        raise AgentBusinessError(f"AGENT_UNEXPECTED_ERROR: {operation}失败") from None


def object_payload(value: Any) -> dict[str, Any]:
    """将非对象请求体转为可控业务错误，而非让框架泄露校验细节。"""
    if not isinstance(value, dict):
        raise AgentBusinessError("AGENT_VALIDATION_FAILED: 请求体必须是 JSON 对象")
    return value


def nullable_text(value: Any) -> str | None:
    """把空字符串归一成数据库空值。"""
    return value.strip() or None if isinstance(value, str) else None


def integer_or_none(value: Any) -> int | None:
    """安全解析可选整数查询参数。"""
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def task_terminal(task: dict[str, Any]) -> bool:
    """判断 SSE 是否应以 done 事件结束。"""
    return str(task.get("status") or "") in TERMINAL_TASK_STATUSES


def latest_sequence(task: dict[str, Any]) -> int:
    """读取当前详情窗口的最新消息序号。"""
    return max((integer_or_none(item.get("sequenceNo")) or 0 for item in task.get("messages") or []), default=0)


def task_fingerprint(task: dict[str, Any]) -> str:
    """构造不含正文的快照指纹，避免无变化时重复推送 task。"""
    return "|".join(
        str(item)
        for item in (
            task.get("status"),
            task.get("updatedAt"),
            len(task.get("toolCalls") or []),
            len(task.get("reviews") or []),
            len(task.get("operations") or []),
            len(task.get("messages") or []),
            task.get("errorCode"),
            task.get("errorMessage"),
        )
    )


def message_event(task_id: str, message: dict[str, Any]) -> dict[str, Any]:
    """将消息投影转为不含隐藏推理的 Agent SSE 增量。"""
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    return {
        "taskId": task_id,
        "eventType": payload.get("eventType") or message.get("sourceEventType") or "MESSAGE_APPENDED",
        "status": payload.get("status") or "RUNNING",
        "message": message,
        "createdAt": message.get("createdAt"),
    }


def sse_event(name: str, data: dict[str, Any]) -> str:
    """按 SSE 文本协议编码单个事件。"""
    encoded = json.dumps(jsonable_encoder(data), ensure_ascii=False, separators=(",", ":"))
    return f"event: {name}\ndata: {encoded}\n\n"


def stream_poll_seconds() -> float:
    """读取可测试的轮询间隔，非法配置采用一秒默认值。"""
    try:
        value = float(os.getenv("AGENT_STREAM_POLL_SECONDS", "1"))
    except ValueError:
        return 1.0
    return max(0.1, min(value, 10.0))
