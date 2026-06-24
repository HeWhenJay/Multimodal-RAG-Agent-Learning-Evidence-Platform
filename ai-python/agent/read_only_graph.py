from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph

from agent.java_gateway import JavaAgentGatewayClient
from app.schemas.agent import AgentTaskEvent, AgentTaskStartRequest, AgentTaskStartResponse, AgentToolCallEvent


class ReadOnlyAgentState(TypedDict, total=False):
    task_id: str
    task_type: str
    thread_id: str
    task_input: dict[str, Any]
    tool_call_id: str
    tool_name: str
    tool_arguments: dict[str, Any]
    tool_result: dict[str, Any]
    memory_context: list[dict[str, Any]]
    final_result: dict[str, Any]
    status: str
    error_code: str | None
    error_message: str | None


def run_read_only_agent(request: AgentTaskStartRequest, client: JavaAgentGatewayClient) -> AgentTaskStartResponse:
    """运行阶段 2 纯只读 Agent 闭环，所有业务工具只通过 Java Gateway 调用。"""
    thread_id = request.threadId or request.taskId
    client.publish_event(
        AgentTaskEvent(
            eventType="TASK_STARTED",
            status="RUNNING",
            pythonThreadId=thread_id,
            draft={"message": "只读 Agent 已启动"},
        )
    )
    initial_state: ReadOnlyAgentState = {
        "task_id": request.taskId,
        "task_type": request.taskType,
        "thread_id": thread_id,
        "task_input": request.input,
        "status": "RUNNING",
    }
    graph = build_read_only_graph(client)
    result = graph.invoke(initial_state)
    return AgentTaskStartResponse(
        taskId=request.taskId,
        threadId=thread_id,
        accepted=True,
        status=str(result.get("status") or "FAILED"),
        errorCode=result.get("error_code"),
        errorMessage=result.get("error_message"),
    )


def build_read_only_graph(client: JavaAgentGatewayClient):
    """构建只读 LangGraph，节点顺序为记忆预取、准备工具、调用工具、生成最终结果。"""
    workflow = StateGraph(ReadOnlyAgentState)
    workflow.add_node("memory_prefetch", lambda state: memory_prefetch(state, client))
    workflow.add_node("prepare_tool", prepare_tool)
    workflow.add_node("call_java_gateway", lambda state: call_java_gateway(state, client))
    workflow.add_node("finalize", lambda state: finalize(state, client))
    workflow.set_entry_point("memory_prefetch")
    workflow.add_edge("memory_prefetch", "prepare_tool")
    workflow.add_edge("prepare_tool", "call_java_gateway")
    workflow.add_edge("call_java_gateway", "finalize")
    workflow.add_edge("finalize", END)
    return workflow.compile()


def memory_prefetch(state: ReadOnlyAgentState, client: JavaAgentGatewayClient) -> ReadOnlyAgentState:
    """在主检索前读取当前任务可用记忆，失败时不阻断只读任务。"""
    query = task_query(state.get("task_input") or {}, state.get("task_type"))
    memory_context = prefetch_memory_context(
        task_id=state["task_id"],
        thread_id=state["thread_id"],
        task_input=state.get("task_input") or {},
        query=query,
        client=client,
    )
    return {**state, "memory_context": memory_context}


def prepare_tool(state: ReadOnlyAgentState) -> ReadOnlyAgentState:
    """根据任务输入选择阶段 2 支持的只读工具。"""
    if state["task_type"] != "pure_read_query":
        return {
            **state,
            "status": "FAILED",
            "error_code": "AGENT_VALIDATION_FAILED",
            "error_message": "阶段 2 仅支持 pure_read_query",
        }
    task_input = state.get("task_input") or {}
    question = text_value(task_input.get("question")) or text_value(task_input.get("goal"))
    if not question:
        return {
            **state,
            "status": "FAILED",
            "error_code": "AGENT_VALIDATION_FAILED",
            "error_message": "只读 Agent 任务缺少 goal 或 question",
        }
    tool_hints = task_input.get("toolHints")
    tool_name = "retrieval_coverage_probe" if isinstance(tool_hints, list) and "retrieval_coverage_probe" in tool_hints else "rag_query_probe_non_persistent"
    arguments: dict[str, Any] = {
        "question": question,
        "topK": int_value(task_input.get("topK"), 5),
        "candidateMultiplier": int_value(task_input.get("candidateMultiplier"), 4),
    }
    metadata_filter = task_input.get("metadataFilter")
    if isinstance(metadata_filter, dict):
        arguments["metadataFilter"] = metadata_filter
    return {
        **state,
        "tool_call_id": f"tool-call-{uuid.uuid4().hex}",
        "tool_name": tool_name,
        "tool_arguments": arguments,
    }


def call_java_gateway(state: ReadOnlyAgentState, client: JavaAgentGatewayClient) -> ReadOnlyAgentState:
    """调用 Java Read Tool Gateway 并回写 Observation。"""
    if state.get("status") == "FAILED":
        return state
    payload = {
        "taskId": state["task_id"],
        "toolCallId": state["tool_call_id"],
        "toolName": state["tool_name"],
        "arguments": state["tool_arguments"],
    }
    try:
        result = client.execute_read_tool(payload)
    except Exception as exc:
        return {
            **state,
            "status": "FAILED",
            "error_code": "AGENT_TOOL_DOWNSTREAM_FAILED",
            "error_message": f"Java 只读工具调用失败：{exc}",
        }
    client.publish_event(
        AgentTaskEvent(
            eventType="TOOL_OBSERVATION",
            status="RUNNING",
            pythonThreadId=state["thread_id"],
            toolCall=AgentToolCallEvent(
                id=state["tool_call_id"],
                toolName=state["tool_name"],
                status=str(result.get("status") or "FAILED"),
                response=tool_observation_summary(result),
                ownershipVerified=bool(result.get("ownershipVerified")),
                scope=result.get("scope"),
                errorCode=result.get("errorCode"),
                errorMessage=result.get("errorMessage"),
            ),
            draft={"lastToolName": state["tool_name"]},
        )
    )
    if result.get("status") != "SUCCEEDED":
        return {
            **state,
            "tool_result": result,
            "status": "FAILED",
            "error_code": str(result.get("errorCode") or "AGENT_TOOL_DOWNSTREAM_FAILED"),
            "error_message": str(result.get("errorMessage") or "只读工具执行失败"),
        }
    return {**state, "tool_result": result}


def finalize(state: ReadOnlyAgentState, client: JavaAgentGatewayClient) -> ReadOnlyAgentState:
    """生成只读任务最终结果并回写 Java。"""
    if state.get("status") == "FAILED":
        final_result = {
            "answer": state.get("error_message") or "只读 Agent 执行失败",
            "riskLevel": "MEDIUM",
            "evidenceIds": [],
            "toolName": state.get("tool_name"),
            "memoryContext": state.get("memory_context") or [],
        }
        client.publish_event(
            AgentTaskEvent(
                eventType="TASK_FAILED",
                status="FAILED",
                pythonThreadId=state["thread_id"],
                final=final_result,
                errorCode=state.get("error_code"),
                errorMessage=state.get("error_message"),
            )
        )
        return {**state, "final_result": final_result}

    tool_result = state.get("tool_result") or {}
    data = tool_result.get("data") if isinstance(tool_result.get("data"), dict) else {}
    evidences = data.get("evidences") if isinstance(data.get("evidences"), list) else []
    final_result = {
        "answer": text_value(data.get("answer")) or "只读 Agent 已完成检索覆盖诊断",
        "evidenceIds": [str(item.get("evidenceId")) for item in evidences if isinstance(item, dict) and item.get("evidenceId")],
        "evidenceCount": len(evidences) if evidences else int_value(data.get("evidenceCount"), 0),
        "expandedQueries": data.get("expandedQueries") if isinstance(data.get("expandedQueries"), list) else [],
        "toolName": state.get("tool_name"),
        "memoryContext": state.get("memory_context") or [],
        "observedAt": utc_time_provider()["utcTime"],
        "riskLevel": "LOW",
    }
    client.publish_event(
        AgentTaskEvent(
            eventType="TASK_COMPLETED",
            status="COMPLETED",
            pythonThreadId=state["thread_id"],
            final=final_result,
        )
    )
    return {**state, "status": "COMPLETED", "final_result": final_result}


def utc_time_provider() -> dict[str, str]:
    """返回 UTC 时间；该系统工具不访问用户数据。"""
    return {"utcTime": datetime.now(timezone.utc).isoformat()}


def tool_observation_summary(result: dict[str, Any]) -> dict[str, Any]:
    """回写给 Java 的 Observation 只保留摘要，避免保存正文。"""
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    evidences = data.get("evidences") if isinstance(data.get("evidences"), list) else []
    diagnostics = data.get("diagnostics") if isinstance(data.get("diagnostics"), dict) else result.get("diagnostics")
    return {
        "status": result.get("status"),
        "toolName": result.get("toolName"),
        "answerLength": len(str(data.get("answer") or "")),
        "evidenceCount": len(evidences) if evidences else int_value(data.get("evidenceCount"), 0),
        "expandedQueryCount": len(data.get("expandedQueries")) if isinstance(data.get("expandedQueries"), list) else 0,
        "memoryCount": int_value(data.get("memoryCount"), 0),
        "candidateCount": int_value(data.get("candidateCount"), 0),
        "diagnosticKeys": list(diagnostics.keys()) if isinstance(diagnostics, dict) else [],
    }


def prefetch_memory_context(
    *,
    task_id: str,
    thread_id: str,
    task_input: dict[str, Any],
    query: str,
    client: JavaAgentGatewayClient,
) -> list[dict[str, Any]]:
    """通过 Java Tool Gateway 预取可注入记忆，Python 不自行判断用户权限。"""
    if not query:
        return []
    payload = {
        "taskId": task_id,
        "toolCallId": f"tool-call-memory-{uuid.uuid4().hex}",
        "toolName": "agent_memory_retriever",
        "arguments": {
            "query": query,
            "topK": int_value(task_input.get("memoryTopK"), 5),
        },
    }
    try:
        result = client.execute_read_tool(payload)
    except Exception:
        return []
    if result.get("status") != "SUCCEEDED":
        return []
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    memory_context = data.get("memoryContext") if isinstance(data.get("memoryContext"), list) else data.get("memories")
    if not isinstance(memory_context, list):
        return []
    return [item for item in memory_context if isinstance(item, dict)]


def task_query(task_input: dict[str, Any], task_type: str | None = None) -> str:
    """生成记忆预取使用的查询文本。"""
    goal = text_value(task_input.get("question")) or text_value(task_input.get("goal"))
    if task_type == "planning_task":
        jd_text = text_value(task_input.get("jobDescription"))
        resume_text = text_value(task_input.get("resumeText"))
        return "\n".join(item for item in [goal, jd_text[:300], resume_text[:240]] if item)
    return goal


def text_value(value: Any) -> str:
    """读取非空文本。"""
    text = "" if value is None else str(value).strip()
    return text


def int_value(value: Any, default: int) -> int:
    """读取整数，失败时使用默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
