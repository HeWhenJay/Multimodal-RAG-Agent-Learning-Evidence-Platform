import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from agents.gateway.java_gateway import JavaAgentGatewayClient
from agents.orchestration.pae_react_graph import resume_unified_agent, start_unified_agent
from app.core.agent_internal_token import resolve_agent_internal_token
from app.schemas.agent import AgentTaskEvent, AgentTaskResumeRequest, AgentTaskStartRequest, AgentTaskStartResponse


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
    background_tasks: BackgroundTasks,
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
    background_tasks.add_task(run_start_task, request, client)
    agent_log("启动请求已接收，后台执行统一图", taskId=request.taskId, status="RUNNING", accepted=True)
    return AgentTaskStartResponse(taskId=request.taskId, threadId=request.threadId or request.taskId, accepted=True, status="RUNNING")


@router.post("/tasks/{task_id}/resume", response_model=AgentTaskStartResponse)
def resume_task(
    task_id: str,
    request: AgentTaskResumeRequest,
    background_tasks: BackgroundTasks,
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
    background_tasks.add_task(run_resume_task, request, client)
    agent_log("恢复请求已接收，后台继续统一图", taskId=request.taskId, status="RUNNING", accepted=True)
    return AgentTaskStartResponse(taskId=request.taskId, threadId=request.threadId or request.taskId, accepted=True, status="RUNNING")


def run_start_task(request: AgentTaskStartRequest, client: JavaAgentGatewayClient) -> None:
    """后台启动 LangGraph，结果通过 Java events 回写。"""
    try:
        response = start_unified_agent(request, client)
        agent_log("后台启动任务完成", taskId=response.taskId, status=response.status, accepted=response.accepted)
    except Exception as exc:
        publish_background_failure(request.taskId, request.threadId or request.taskId, client, exc, "AGENT_PYTHON_UNEXPECTED_ERROR")


def run_resume_task(request: AgentTaskResumeRequest, client: JavaAgentGatewayClient) -> None:
    """后台恢复 LangGraph，避免审批接口等待整图执行完成。"""
    try:
        response = resume_unified_agent(request, client)
        agent_log("后台恢复任务完成", taskId=response.taskId, status=response.status, accepted=response.accepted)
    except Exception as exc:
        publish_background_failure(request.taskId, request.threadId or request.taskId, client, exc, "AGENT_PYTHON_UNEXPECTED_ERROR")


def publish_background_failure(task_id: str, thread_id: str, client: JavaAgentGatewayClient, exc: Exception, error_code: str) -> None:
    """后台任务异常时仍回写 Java，避免前端长期停在运行中。"""
    message = f"Python Agent 后台执行失败：{exc}"
    agent_log("后台任务异常", taskId=task_id, errorCode=error_code, errorMessage=message)
    try:
        client.publish_event(
            AgentTaskEvent(
                eventType="TASK_FAILED",
                status="FAILED",
                pythonThreadId=thread_id,
                errorCode=error_code,
                errorMessage=message,
            )
        )
    except Exception as callback_exc:
        agent_log("后台失败事件回写失败", taskId=task_id, errorMessage=str(callback_exc))


def require_internal_token(token: str | None) -> str:
    """校验 Java 调 Python Agent 的内部令牌，本地未显式配置时使用共享文件兜底。"""
    configured = resolve_agent_internal_token()
    if not configured or token != configured:
        agent_log("拒绝内部令牌", configured=bool(configured), provided=bool(token))
        raise HTTPException(status_code=401, detail="AGENT_INTERNAL_TOKEN_INVALID")
    return configured
