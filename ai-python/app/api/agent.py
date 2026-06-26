import os

from fastapi import APIRouter, Header, HTTPException

from agents.gateway.java_gateway import JavaAgentGatewayClient
from agents.jd_learning_plan.planning_graph import resume_planning_agent, start_planning_agent
from agents.read_only.read_only_graph import run_read_only_agent
from app.schemas.agent import AgentTaskResumeRequest, AgentTaskStartRequest, AgentTaskStartResponse


router = APIRouter(prefix="/internal/agent", tags=["Agent"])


@router.post("/tasks", response_model=AgentTaskStartResponse)
def start_task(
    request: AgentTaskStartRequest,
    x_agent_internal_token: str | None = Header(default=None, alias="X-Agent-Internal-Token"),
) -> AgentTaskStartResponse:
    """启动阶段 2 纯只读 Agent 任务。"""
    token = require_internal_token(x_agent_internal_token)
    client = JavaAgentGatewayClient(
        java_tool_gateway_base_url=request.javaToolGatewayBaseUrl,
        callback_url=request.callbackUrl,
        internal_token=token,
    )
    if request.taskType == "planning_task":
        return start_planning_agent(request, client)
    return run_read_only_agent(request, client)


@router.post("/tasks/{task_id}/resume", response_model=AgentTaskStartResponse)
def resume_task(
    task_id: str,
    request: AgentTaskResumeRequest,
    x_agent_internal_token: str | None = Header(default=None, alias="X-Agent-Internal-Token"),
) -> AgentTaskStartResponse:
    """根据 Java 审批结果恢复阶段 3 规划任务。"""
    token = require_internal_token(x_agent_internal_token)
    if task_id != request.taskId:
        raise HTTPException(status_code=400, detail="AGENT_TASK_ID_MISMATCH")
    client = JavaAgentGatewayClient(
        java_tool_gateway_base_url=request.javaToolGatewayBaseUrl,
        callback_url=request.callbackUrl,
        internal_token=token,
    )
    if request.taskType != "planning_task":
        raise HTTPException(status_code=400, detail="AGENT_VALIDATION_FAILED")
    return resume_planning_agent(request, client)


def require_internal_token(token: str | None) -> str:
    """校验 Java 调 Python Agent 的内部令牌，未配置时拒绝处理。"""
    configured = os.getenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "").strip()
    if not configured or token != configured:
        raise HTTPException(status_code=401, detail="AGENT_INTERNAL_TOKEN_INVALID")
    return configured
