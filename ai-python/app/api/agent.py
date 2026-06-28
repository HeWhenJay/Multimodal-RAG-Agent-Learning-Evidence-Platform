import os
import logging

from fastapi import APIRouter, Header, HTTPException

from agents.gateway.java_gateway import JavaAgentGatewayClient
from agents.orchestration.pae_react_graph import resume_unified_agent, start_unified_agent
from app.schemas.agent import AgentTaskResumeRequest, AgentTaskStartRequest, AgentTaskStartResponse


router = APIRouter(prefix="/internal/agent", tags=["Agent"])
logger = logging.getLogger(__name__)


def agent_log(message: str, **fields: object) -> None:
    """输出 Agent 关键链路日志，方便判断请求是否到达 Python。"""
    suffix = " ".join(f"{key}={value}" for key, value in fields.items())
    text = f"Agent链路 | {message}" + (f" | {suffix}" if suffix else "")
    logger.info(text)
    print(text, flush=True)


@router.post("/tasks", response_model=AgentTaskStartResponse)
def start_task(
    request: AgentTaskStartRequest,
    x_agent_internal_token: str | None = Header(default=None, alias="X-Agent-Internal-Token"),
) -> AgentTaskStartResponse:
    """启动统一 PAE + ReAct Agent 任务。"""
    agent_log("收到启动请求", taskId=request.taskId, taskType=request.taskType, threadId=request.threadId or request.taskId)
    token = require_internal_token(x_agent_internal_token)
    client = JavaAgentGatewayClient(
        java_tool_gateway_base_url=request.javaToolGatewayBaseUrl,
        callback_url=request.callbackUrl,
        internal_token=token,
    )
    response = start_unified_agent(request, client)
    agent_log("启动请求处理完成", taskId=response.taskId, status=response.status, accepted=response.accepted)
    return response


@router.post("/tasks/{task_id}/resume", response_model=AgentTaskStartResponse)
def resume_task(
    task_id: str,
    request: AgentTaskResumeRequest,
    x_agent_internal_token: str | None = Header(default=None, alias="X-Agent-Internal-Token"),
) -> AgentTaskStartResponse:
    """根据 Java 审批结果恢复统一 Agent 图。"""
    agent_log("收到恢复请求", taskId=request.taskId, taskType=request.taskType, reviewType=request.reviewType, decision=request.decision)
    token = require_internal_token(x_agent_internal_token)
    if task_id != request.taskId:
        agent_log("恢复请求任务 ID 不匹配", pathTaskId=task_id, bodyTaskId=request.taskId)
        raise HTTPException(status_code=400, detail="AGENT_TASK_ID_MISMATCH")
    client = JavaAgentGatewayClient(
        java_tool_gateway_base_url=request.javaToolGatewayBaseUrl,
        callback_url=request.callbackUrl,
        internal_token=token,
    )
    if request.taskType not in {"planning_task", "mutation_task"}:
        agent_log("恢复请求任务类型不支持", taskId=request.taskId, taskType=request.taskType)
        raise HTTPException(status_code=400, detail="AGENT_VALIDATION_FAILED")
    response = resume_unified_agent(request, client)
    agent_log("恢复请求处理完成", taskId=response.taskId, status=response.status, accepted=response.accepted)
    return response


def require_internal_token(token: str | None) -> str:
    """校验 Java 调 Python Agent 的内部令牌，未配置时拒绝处理。"""
    configured = os.getenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "").strip()
    if not configured or token != configured:
        agent_log("拒绝内部令牌", configured=bool(configured), provided=bool(token))
        raise HTTPException(status_code=401, detail="AGENT_INTERNAL_TOKEN_INVALID")
    return configured
