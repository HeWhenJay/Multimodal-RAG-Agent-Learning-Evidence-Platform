from __future__ import annotations

import uuid
from typing import Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from agents.gateway.java_gateway import JavaAgentGatewayClient
from agents.jd_learning_plan.planning_graph import (
    build_alignment,
    build_evidence_question,
    build_gaps,
    build_match_summary,
    build_resume_content_map,
    build_web_search_query,
    execute_approved_mutation,
    extract_requirements,
    mutation_idempotency_key,
    mutation_tool_name,
    should_request_crud_review,
)
from agents.read_only.read_only_graph import (
    int_value,
    prefetch_memory_context,
    task_query,
    text_value,
    tool_observation_summary,
    utc_time_provider,
)
from app.schemas.agent import AgentTaskEvent, AgentTaskResumeRequest, AgentTaskStartRequest, AgentTaskStartResponse, AgentToolCallEvent


class UnifiedAgentState(TypedDict, total=False):
    """统一 PAE + ReAct 图状态，覆盖规划、执行、修补、验收和记忆候选。"""

    task_id: str
    task_type: str
    thread_id: str
    task_input: dict[str, Any]
    user_goal: str
    status: str
    error_code: str | None
    error_message: str | None

    plan: dict[str, Any]
    plan_version: int
    plan_approved: bool
    completion_criteria: list[str]

    memory_context_pre: list[dict[str, Any]]
    memory_context_task: list[dict[str, Any]]

    current_step_index: int
    current_action: dict[str, Any]
    tool_calls: list[dict[str, Any]]
    observations: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    react_trace: list[dict[str, Any]]

    retry_count: int
    max_retries: int
    failure_reason: dict[str, Any]
    repair_decision: str

    verifier_result: dict[str, Any]
    completion_score: float
    missing_requirements: list[str]
    draft_result: dict[str, Any]
    final_result: dict[str, Any]
    answer: str

    pending_review: dict[str, Any]
    pending_mutation: dict[str, Any]
    pending_memory_candidates: list[dict[str, Any]]
    approved_operation_ids: list[str]
    idempotency_keys: list[str]


def start_unified_agent(request: AgentTaskStartRequest, client: JavaAgentGatewayClient) -> AgentTaskStartResponse:
    """启动统一 Agent 图；规划类任务会先停在计划审批。"""
    thread_id = request.threadId or request.taskId
    client.publish_event(
        AgentTaskEvent(
            eventType="TASK_STARTED",
            status="RUNNING",
            pythonThreadId=thread_id,
            draft={"message": "统一 Agent 图已启动"},
        )
    )
    state = initial_state(request.taskId, request.taskType, thread_id, request.input, plan_approved=False)
    result = build_unified_graph(client).invoke(state)
    return AgentTaskStartResponse(
        taskId=request.taskId,
        threadId=thread_id,
        accepted=True,
        status=str(result.get("status") or "FAILED"),
        errorCode=result.get("error_code"),
        errorMessage=result.get("error_message"),
    )


def resume_unified_agent(request: AgentTaskResumeRequest, client: JavaAgentGatewayClient) -> AgentTaskStartResponse:
    """根据人工审批结果恢复统一 Agent 图。"""
    thread_id = request.threadId or request.taskId
    if request.decision == "CHANGES_REQUESTED":
        plan_approved = request.reviewType != "PLAN"
        state = initial_state(
            request.taskId,
            request.taskType,
            thread_id,
            input_with_review_feedback(request),
            plan_approved=plan_approved,
        )
        result = build_unified_graph(client).invoke(state)
        return AgentTaskStartResponse(
            taskId=request.taskId,
            threadId=thread_id,
            accepted=True,
            status=str(result.get("status") or "FAILED"),
            errorCode=result.get("error_code"),
            errorMessage=result.get("error_message"),
        )
    if request.decision != "APPROVED":
        client.publish_event(
            AgentTaskEvent(
                eventType="TASK_FAILED",
                status="FAILED",
                pythonThreadId=thread_id,
                errorCode="AGENT_REVIEW_REJECTED",
                errorMessage="用户未批准 Agent 继续执行",
            )
        )
        return AgentTaskStartResponse(taskId=request.taskId, threadId=thread_id, accepted=True, status="FAILED")
    if request.reviewType == "CRUD":
        return execute_approved_mutation(request, client, thread_id)
    if request.reviewType == "OUTPUT":
        return resume_output_review(request, client, thread_id)
    state = initial_state(request.taskId, request.taskType, thread_id, request.input, plan_approved=True)
    result = build_unified_graph(client).invoke(state)
    return AgentTaskStartResponse(
        taskId=request.taskId,
        threadId=thread_id,
        accepted=True,
        status=str(result.get("status") or "FAILED"),
        errorCode=result.get("error_code"),
        errorMessage=result.get("error_message"),
    )


def resume_output_review(
    request: AgentTaskResumeRequest,
    client: JavaAgentGatewayClient,
    thread_id: str,
) -> AgentTaskStartResponse:
    """输出审批通过后，按保存意图进入 CRUD 审批或直接完成。"""
    if should_request_crud_review(request.input):
        review_request = build_memory_aware_crud_review_request(request)
        client.publish_event(
            AgentTaskEvent(
                eventType="MUTATION_PROPOSED",
                status="WAITING_CRUD_REVIEW",
                pythonThreadId=thread_id,
                draft={"message": "输出已确认，等待保存类变更审批"},
                reviewRequest=review_request,
            )
        )
        return AgentTaskStartResponse(taskId=request.taskId, threadId=thread_id, accepted=True, status="WAITING_CRUD_REVIEW")
    final_result = {"answer": "用户已确认规划输出", "riskLevel": "LOW"}
    client.publish_event(
        AgentTaskEvent(
            eventType="TASK_COMPLETED",
            status="COMPLETED",
            pythonThreadId=thread_id,
            final=final_result,
        )
    )
    publish_post_answer_memory_candidates(request.taskId, thread_id, request.input, final_result, client)
    return AgentTaskStartResponse(taskId=request.taskId, threadId=thread_id, accepted=True, status="COMPLETED")


def input_with_review_feedback(request: AgentTaskResumeRequest) -> dict[str, Any]:
    """把用户要求修改的审批意见放回任务输入，供下一轮规划或草稿生成参考。"""
    task_input = dict(request.input or {})
    task_input["reviewFeedback"] = {
        "reviewType": request.reviewType,
        "decision": request.decision,
        "comment": request.decisionPayload.get("comment"),
        "changes": request.decisionPayload.get("changes") or request.decisionPayload.get("requestedChanges"),
    }
    return task_input


def initial_state(
    task_id: str,
    task_type: str,
    thread_id: str,
    task_input: dict[str, Any],
    *,
    plan_approved: bool,
) -> UnifiedAgentState:
    """构造统一图初始状态。"""
    return {
        "task_id": task_id,
        "task_type": task_type,
        "thread_id": thread_id,
        "task_input": task_input,
        "user_goal": text_value(task_input.get("question")) or text_value(task_input.get("goal")) or "执行 Agent 任务",
        "status": "RUNNING",
        "plan_version": 1,
        "plan_approved": plan_approved,
        "current_step_index": 0,
        "tool_calls": [],
        "observations": [],
        "tool_results": [],
        "react_trace": [],
        "retry_count": 0,
        "max_retries": int_value(task_input.get("maxToolRetries"), 1),
        "approved_operation_ids": [],
        "idempotency_keys": [],
    }


def build_unified_graph(client: JavaAgentGatewayClient):
    """构建统一 PAE + ReAct LangGraph。"""
    workflow = StateGraph(UnifiedAgentState)
    workflow.add_node("memory_prefetch_before_planner", lambda state: memory_prefetch_before_planner(state, client))
    workflow.add_node("planner", planner_node)
    workflow.add_node("plan_review", lambda state: plan_review_node(state, client))
    workflow.add_node("memory_prefetch_after_planner", lambda state: memory_prefetch_after_planner(state, client))
    workflow.add_node("executor", executor_node)
    workflow.add_node("tool_adapter", lambda state: tool_adapter_node(state, client))
    workflow.add_node("repair", repair_node)
    workflow.add_node("acceptance", lambda state: acceptance_node(state, client))
    workflow.add_node("answer_writer", lambda state: answer_node(state, client))
    workflow.add_node("post_answer_memory", lambda state: post_answer_memory_node(state, client))

    workflow.set_entry_point("memory_prefetch_before_planner")
    workflow.add_edge("memory_prefetch_before_planner", "planner")
    workflow.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "plan_review": "plan_review",
            "memory_prefetch_after_planner": "memory_prefetch_after_planner",
        },
    )
    workflow.add_edge("plan_review", END)
    workflow.add_edge("memory_prefetch_after_planner", "executor")
    workflow.add_conditional_edges(
        "executor",
        route_after_executor,
        {
            "tool_adapter": "tool_adapter",
            "acceptance": "acceptance",
        },
    )
    workflow.add_conditional_edges(
        "tool_adapter",
        route_after_tool_adapter,
        {
            "repair": "repair",
            "acceptance": "acceptance",
        },
    )
    workflow.add_conditional_edges(
        "repair",
        route_after_repair,
        {
            "tool_adapter": "tool_adapter",
            "planner": "planner",
            "acceptance": "acceptance",
        },
    )
    workflow.add_conditional_edges(
        "acceptance",
        route_after_acceptance,
        {
            "executor": "executor",
            "repair": "repair",
            "answer_writer": "answer_writer",
        },
    )
    workflow.add_edge("answer_writer", "post_answer_memory")
    workflow.add_edge("post_answer_memory", END)
    return workflow.compile()


def memory_prefetch_before_planner(state: UnifiedAgentState, client: JavaAgentGatewayClient) -> UnifiedAgentState:
    """规划前读取偏好、历史约束和近期任务记忆。"""
    query = task_query(state.get("task_input") or {}, state.get("task_type"))
    memory_context = prefetch_memory_context(
        task_id=state["task_id"],
        thread_id=state["thread_id"],
        task_input=state.get("task_input") or {},
        query=query,
        client=client,
    )
    return {**state, "memory_context_pre": memory_context}


def planner_node(state: UnifiedAgentState) -> UnifiedAgentState:
    """PAE 规划器，输出执行计划和验收标准。"""
    if state.get("status") == "FAILED":
        return state
    task_input = state.get("task_input") or {}
    task_type = state.get("task_type")
    if task_type == "planning_task":
        plan = build_planning_plan(task_input)
    else:
        plan = build_read_plan(task_input)
    criteria = build_completion_criteria(task_type, plan)
    return {
        **state,
        "plan": plan,
        "completion_criteria": criteria,
        "current_step_index": 0,
        "current_action": {},
        "retry_count": 0,
        "repair_decision": "",
    }


def plan_review_node(state: UnifiedAgentState, client: JavaAgentGatewayClient) -> UnifiedAgentState:
    """发布计划审批请求，等待 Java 和前端恢复同一任务。"""
    plan = state.get("plan") or {}
    memory_count = len(state.get("memory_context_pre") or [])
    client.publish_event(
        AgentTaskEvent(
            eventType="REVIEW_REQUESTED",
            status="WAITING_PLAN_REVIEW",
            pythonThreadId=state["thread_id"],
            draft={"planSummary": plan.get("title"), "memoryContext": state.get("memory_context_pre") or []},
            reviewRequest={
                "id": f"review-plan-{state['task_id']}",
                "reviewType": "PLAN",
                "proposal": {**plan, "memoryCount": memory_count},
            },
        )
    )
    return {**state, "status": "WAITING_PLAN_REVIEW"}


def memory_prefetch_after_planner(state: UnifiedAgentState, client: JavaAgentGatewayClient) -> UnifiedAgentState:
    """规划后基于计划步骤重新读取任务相关记忆。"""
    if state.get("task_type") != "planning_task":
        return state
    task_input = state.get("task_input") or {}
    plan = state.get("plan") or {}
    plan_text = " ".join(str(step.get("description") or "") for step in plan.get("steps", []) if isinstance(step, dict))
    query = "\n".join(item for item in [task_query(task_input, "planning_task"), plan_text] if item)
    memory_context = prefetch_memory_context(
        task_id=state["task_id"],
        thread_id=state["thread_id"],
        task_input=task_input,
        query=query,
        client=client,
    )
    return {**state, "memory_context_task": memory_context}


def executor_node(state: UnifiedAgentState) -> UnifiedAgentState:
    """ReAct 执行器，根据计划选择当前步骤的行动。"""
    if state.get("status") == "FAILED":
        return state
    steps = list((state.get("plan") or {}).get("steps") or [])
    index = int_value(state.get("current_step_index"), 0)
    if index >= len(steps):
        return {**state, "current_action": {}}
    step = steps[index] if isinstance(steps[index], dict) else {}
    action = build_action_for_step(state, step)
    trace = list(state.get("react_trace") or [])
    trace.append(
        {
            "thought": build_react_thought(state, step),
            "action": {
                "toolName": action.get("toolName"),
                "toolType": action.get("toolType") or "READ",
                "stepIndex": index,
            },
            "observation": None,
        }
    )
    return {**state, "current_action": action, "react_trace": trace}


def tool_adapter_node(state: UnifiedAgentState, client: JavaAgentGatewayClient) -> UnifiedAgentState:
    """统一工具节点，只通过 Java Gateway 调用只读或变更工具。"""
    action = state.get("current_action") or {}
    tool_name = text_value(action.get("toolName"))
    if not tool_name:
        return {**state, "status": "FAILED", "error_code": "AGENT_TOOL_UNKNOWN", "error_message": "执行器未选择工具"}
    tool_type = text_value(action.get("toolType")) or "READ"
    tool_call_id = text_value(action.get("toolCallId")) or f"tool-call-{uuid.uuid4().hex}"
    payload = {
        "taskId": state["task_id"],
        "toolCallId": tool_call_id,
        "toolName": tool_name,
        "arguments": action.get("arguments") or {},
    }
    if tool_type == "MUTATION" and not has_approved_mutation(action):
        result = {
            "taskId": state["task_id"],
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "status": "FAILED",
            "errorCode": "AGENT_MUTATION_REQUIRES_APPROVAL",
            "errorMessage": "变更工具缺少已批准的审批、操作和幂等信息，Python Agent 已拒绝调用。",
            "retryable": False,
        }
    else:
        payload.update(mutation_fields_from_action(action))
        try:
            result = client.execute_mutation_tool(payload) if tool_type == "MUTATION" else client.execute_read_tool(payload)
        except Exception as exc:
            result = {
                "taskId": state["task_id"],
                "toolCallId": tool_call_id,
                "toolName": tool_name,
                "status": "FAILED",
                "errorCode": "AGENT_TOOL_DOWNSTREAM_FAILED",
                "errorMessage": f"Java 工具调用失败：{exc}",
                "retryable": True,
            }
    status = str(result.get("status") or "FAILED")
    client.publish_event(
        AgentTaskEvent(
            eventType="TOOL_OBSERVATION",
            status="RUNNING" if status == "SUCCEEDED" else "FAILED",
            pythonThreadId=state["thread_id"],
            toolCall=AgentToolCallEvent(
                id=tool_call_id,
                toolName=tool_name,
                toolType=tool_type,
                status=status,
                response=tool_observation_summary(result),
                ownershipVerified=bool(result.get("ownershipVerified")),
                scope=result.get("scope"),
                errorCode=result.get("errorCode"),
                errorMessage=result.get("errorMessage"),
            ),
        )
    )
    tool_calls = list(state.get("tool_calls") or [])
    tool_calls.append({"id": tool_call_id, "toolName": tool_name, "toolType": tool_type, "status": status})
    observations = list(state.get("observations") or [])
    observation = tool_observation_summary(result)
    observations.append(observation)
    react_trace = list(state.get("react_trace") or [])
    if react_trace:
        react_trace[-1] = {**react_trace[-1], "observation": observation}
    if status != "SUCCEEDED":
        return {
            **state,
            "tool_calls": tool_calls,
            "observations": observations,
            "react_trace": react_trace,
            "failure_reason": {
                "toolName": tool_name,
                "toolType": tool_type,
                "errorCode": result.get("errorCode"),
                "errorMessage": result.get("errorMessage"),
                "retryable": bool(result.get("retryable")),
            },
            "status": "TOOL_FAILED",
            "error_code": str(result.get("errorCode") or "AGENT_TOOL_DOWNSTREAM_FAILED"),
            "error_message": str(result.get("errorMessage") or "工具执行失败"),
            "tool_results": list(state.get("tool_results") or []) + [result],
        }
    return {
        **state,
        "tool_calls": tool_calls,
        "observations": observations,
        "react_trace": react_trace,
        "tool_results": list(state.get("tool_results") or []) + [result],
        "retry_count": 0,
        "status": "RUNNING",
    }


def repair_node(state: UnifiedAgentState) -> UnifiedAgentState:
    """修补节点，判断重试、跳过、重规划或汇报无法完成。"""
    failure = state.get("failure_reason") or {}
    tool_name = text_value(failure.get("toolName"))
    error_code = text_value(failure.get("errorCode"))
    retry_count = int_value(state.get("retry_count"), 0)
    max_retries = int_value(state.get("max_retries"), 1)
    hard_stop_codes = {
        "AGENT_RESOURCE_FORBIDDEN",
        "AGENT_MEMORY_FORBIDDEN",
        "AGENT_MEMORY_SCOPE_ESCALATION",
        "AGENT_INTERNAL_TOKEN_INVALID",
    }
    if error_code in hard_stop_codes:
        return {**state, "repair_decision": "REPORT_UNABLE", "status": "FAILED"}
    if tool_name == "web_search_probe" and error_code in {"AGENT_TAVILY_NOT_CONFIGURED", "AGENT_TAVILY_DOWNSTREAM_FAILED"}:
        return skip_current_tool(state, f"联网参考不可用，已降级为本地 RAG 证据：{error_code}")
    if bool(failure.get("retryable")) and retry_count < max_retries:
        return {**state, "repair_decision": "RETRY", "retry_count": retry_count + 1, "status": "RUNNING"}
    if error_code == "AGENT_TOOL_UNKNOWN":
        return {**state, "repair_decision": "REPLAN", "plan_approved": False, "status": "RUNNING"}
    return {**state, "repair_decision": "REPORT_UNABLE", "status": "FAILED"}


def acceptance_node(state: UnifiedAgentState, client: JavaAgentGatewayClient) -> UnifiedAgentState:
    """验收器，判断计划是否完成、是否回到执行器或进入回答节点。"""
    if state.get("status") == "FAILED":
        verifier_result = {
            "complete": False,
            "reason": state.get("error_message") or "工具修补后仍无法完成任务",
            "missingRequirements": state.get("completion_criteria") or [],
        }
        return {**state, "verifier_result": verifier_result, "completion_score": 0.0}
    steps = list((state.get("plan") or {}).get("steps") or [])
    current_index = int_value(state.get("current_step_index"), 0)
    action = state.get("current_action") or {}
    if action:
        current_index += 1
    if current_index < len(steps):
        return {**state, "current_step_index": current_index, "current_action": {}, "verifier_result": {"complete": False}}
    if state.get("task_type") == "planning_task":
        draft = synthesize_planning_draft(state, client)
        return {
            **state,
            "current_step_index": current_index,
            "draft_result": draft,
            "final_result": draft,
            "verifier_result": {"complete": True, "requiresOutputReview": True},
            "completion_score": 1.0,
            "status": "WAITING_OUTPUT_REVIEW",
        }
    final_result = synthesize_read_final(state)
    return {
        **state,
        "current_step_index": current_index,
        "final_result": final_result,
        "verifier_result": {"complete": True, "requiresOutputReview": False},
        "completion_score": 1.0,
        "status": "COMPLETED",
    }


def answer_node(state: UnifiedAgentState, client: JavaAgentGatewayClient) -> UnifiedAgentState:
    """回答节点，组织中文结果并回写 Java。"""
    if state.get("status") == "WAITING_OUTPUT_REVIEW":
        draft = state.get("draft_result") or {}
        client.publish_event(
            AgentTaskEvent(
                eventType="DRAFT_UPDATED",
                status="RUNNING",
                pythonThreadId=state["thread_id"],
                draft=draft,
            )
        )
        client.publish_event(
            AgentTaskEvent(
                eventType="REVIEW_REQUESTED",
                status="WAITING_OUTPUT_REVIEW",
                pythonThreadId=state["thread_id"],
                draft=draft,
                reviewRequest={
                    "id": f"review-output-{state['task_id']}",
                    "reviewType": "OUTPUT",
                    "proposal": {
                        "summary": draft.get("matchSummary") or "规划输出待确认",
                        "riskLevel": draft.get("riskLevel") or "MEDIUM",
                        "evidenceCount": len(draft.get("evidenceIds") or []),
                    },
                },
            )
        )
        return state
    if state.get("status") == "FAILED":
        final_result = {
            "answer": state.get("error_message") or "Agent 当前无法完成该任务",
            "riskLevel": "MEDIUM",
            "evidenceIds": [],
            "repairDecision": state.get("repair_decision"),
            "failureReason": state.get("failure_reason") or {},
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
    final_result = state.get("final_result") or {}
    client.publish_event(
        AgentTaskEvent(
            eventType="TASK_COMPLETED",
            status="COMPLETED",
            pythonThreadId=state["thread_id"],
            final=final_result,
        )
    )
    return {**state, "status": "COMPLETED"}


def post_answer_memory_node(state: UnifiedAgentState, client: JavaAgentGatewayClient) -> UnifiedAgentState:
    """回答后整理长期记忆候选；默认只在显式开启或用户表达记住时运行。"""
    task_input = state.get("task_input") or {}
    if state.get("status") != "COMPLETED" or not should_run_post_answer_memory(task_input):
        return state
    candidates = request_memory_candidates(
        task_id=state["task_id"],
        task_input=task_input,
        draft=state.get("draft_result") or {},
        final=state.get("final_result") or {},
        tool_observations=state.get("observations") or [],
        client=client,
    )
    if candidates:
        client.publish_event(
            AgentTaskEvent(
                eventType="DRAFT_UPDATED",
                status="COMPLETED",
                pythonThreadId=state["thread_id"],
                draft={
                    "message": "已生成回答后的待确认记忆候选",
                    "pendingMemoryCandidates": candidates,
                    "memoryWritePolicy": "候选不会自动激活，需用户确认后才会写入长期记忆。",
                },
            )
        )
    return {**state, "pending_memory_candidates": candidates}


def route_after_planner(state: UnifiedAgentState) -> Literal["plan_review", "memory_prefetch_after_planner"]:
    """规划器之后判断是否需要计划审批。"""
    if state.get("task_type") == "planning_task" and not state.get("plan_approved"):
        return "plan_review"
    return "memory_prefetch_after_planner"


def route_after_executor(state: UnifiedAgentState) -> Literal["tool_adapter", "acceptance"]:
    """执行器之后判断是否有工具行动。"""
    return "tool_adapter" if state.get("current_action") else "acceptance"


def route_after_tool_adapter(state: UnifiedAgentState) -> Literal["repair", "acceptance"]:
    """工具节点之后判断成功或进入修补。"""
    return "repair" if state.get("status") == "TOOL_FAILED" else "acceptance"


def route_after_repair(state: UnifiedAgentState) -> Literal["tool_adapter", "planner", "acceptance"]:
    """修补节点之后路由。"""
    decision = state.get("repair_decision")
    if decision == "RETRY":
        return "tool_adapter"
    if decision == "REPLAN":
        return "planner"
    return "acceptance"


def route_after_acceptance(state: UnifiedAgentState) -> Literal["executor", "repair", "answer_writer"]:
    """验收器之后决定继续执行、修补或回答。"""
    if state.get("status") == "FAILED" or state.get("verifier_result", {}).get("complete"):
        return "answer_writer"
    steps = list((state.get("plan") or {}).get("steps") or [])
    if int_value(state.get("current_step_index"), 0) < len(steps):
        return "executor"
    return "answer_writer"


def build_read_plan(task_input: dict[str, Any]) -> dict[str, Any]:
    """为纯只读任务生成免审批计划。"""
    question = text_value(task_input.get("question")) or text_value(task_input.get("goal")) or "查询学习证据"
    tool_hints = task_input.get("toolHints")
    tool_name = "retrieval_coverage_probe" if isinstance(tool_hints, list) and "retrieval_coverage_probe" in tool_hints else "rag_query_probe_non_persistent"
    return {
        "title": f"{question[:40]} 只读执行计划",
        "steps": [
            {
                "description": "读取当前用户知识库证据并生成只读回答",
                "toolName": tool_name,
                "toolType": "READ",
                "expectedOutput": "带 evidence 引用的只读回答",
            }
        ],
        "requiresPlanReview": False,
        "requiresOutputReview": False,
        "riskLevel": "LOW",
    }


def build_planning_plan(task_input: dict[str, Any]) -> dict[str, Any]:
    """为规划类任务生成需要用户确认的 PAE 计划。"""
    goal = text_value(task_input.get("goal")) or "JD/简历适配分析"
    steps: list[dict[str, Any]] = []
    if web_search_enabled(task_input):
        steps.append(
            {
                "description": "按用户授权进行联网参考，补充公司背景和技能趋势",
                "toolName": "web_search_probe",
                "toolType": "READ",
                "expectedOutput": "外部参考摘要，不写入 RAG evidence 或长期记忆",
            }
        )
    steps.append(
        {
            "description": "检索当前用户 RAG evidence，支撑 JD/简历适配判断",
            "toolName": "rag_query_probe_non_persistent",
            "toolType": "READ",
            "expectedOutput": "当前用户知识库 evidence、expandedQueries 和 diagnostics",
        }
    )
    return {
        "title": f"{goal[:40]} 计划",
        "steps": steps,
        "tools": [step["toolName"] for step in steps] + ["resume_evidence_aligner", "gap_analyzer", "evidence_quality_auditor"],
        "requiresPlanReview": True,
        "requiresOutputReview": True,
        "riskLevel": "MEDIUM" if web_search_enabled(task_input) else "LOW",
        "guardrails": [
            "只通过 Java Tool Gateway 调用工具",
            "计划审批只确认路线，不授权写操作",
            "输出后若保存草稿或记忆，必须再次进入 CRUD / MEMORY_WRITE 审批",
        ],
    }


def build_completion_criteria(task_type: str | None, plan: dict[str, Any]) -> list[str]:
    """生成验收标准。"""
    if task_type == "planning_task":
        return ["已完成计划内只读工具", "生成 supported/weak/missing 对齐", "输出 evidenceIds 或明确缺证据", "未执行未审批写操作"]
    return ["已完成只读工具", "返回回答或明确失败原因", "保留 evidence 引用结构"]


def build_action_for_step(state: UnifiedAgentState, step: dict[str, Any]) -> dict[str, Any]:
    """根据计划步骤构造工具调用参数。"""
    task_input = state.get("task_input") or {}
    tool_name = text_value(step.get("toolName"))
    if tool_name == "web_search_probe":
        goal = state.get("user_goal") or ""
        jd_text = text_value(task_input.get("jobDescription"))
        return {
            "toolName": "web_search_probe",
            "toolType": "READ",
            "arguments": {
                "query": text_value(task_input.get("webSearchQuery")) or build_web_search_query(goal, jd_text),
                "maxResults": int_value(task_input.get("webSearchMaxResults"), 5),
                "searchDepth": text_value(task_input.get("webSearchDepth")) or "basic",
                "topic": "general",
            },
        }
    if tool_name in {"rag_query_probe_non_persistent", "retrieval_coverage_probe"}:
        question = build_question_for_state(state)
        arguments: dict[str, Any] = {
            "question": question,
            "topK": int_value(task_input.get("topK"), 6 if state.get("task_type") == "planning_task" else 5),
            "candidateMultiplier": int_value(task_input.get("candidateMultiplier"), 4),
        }
        metadata_filter = task_input.get("metadataFilter")
        if isinstance(metadata_filter, dict):
            arguments["metadataFilter"] = metadata_filter
        return {"toolName": tool_name, "toolType": "READ", "arguments": arguments}
    return {"toolName": tool_name, "toolType": text_value(step.get("toolType")) or "READ", "arguments": {}}


def build_react_thought(state: UnifiedAgentState, step: dict[str, Any]) -> str:
    """生成可审计的 ReAct 思考摘要，不记录资料正文或模型密钥。"""
    description = text_value(step.get("description")) or "执行计划步骤"
    expected = text_value(step.get("expectedOutput")) or "获取可验收的工具观察"
    completed = len(state.get("observations") or [])
    return f"已完成 {completed} 个观察，下一步需要{description}，预期获得{expected}。"


def has_approved_mutation(action: dict[str, Any]) -> bool:
    """校验 mutation action 是否带有已审批执行所需的关键字段。"""
    return all(text_value(action.get(key)) for key in ["approvalId", "operationId", "idempotencyKey"])


def mutation_fields_from_action(action: dict[str, Any]) -> dict[str, Any]:
    """从 action 中提取 Java mutation gateway 需要的审批字段。"""
    fields = {}
    for key in ["approvalId", "operationId", "idempotencyKey"]:
        value = text_value(action.get(key))
        if value:
            fields[key] = value
    return fields


def build_question_for_state(state: UnifiedAgentState) -> str:
    """为 RAG 工具组合当前任务问题。"""
    task_input = state.get("task_input") or {}
    if state.get("task_type") == "planning_task":
        return build_evidence_question(
            state.get("user_goal") or "分析 JD 与简历证据差距",
            text_value(task_input.get("jobDescription")),
            text_value(task_input.get("resumeText")),
        )
    return text_value(task_input.get("question")) or text_value(task_input.get("goal")) or "查询学习证据"


def synthesize_read_final(state: UnifiedAgentState) -> dict[str, Any]:
    """从只读工具结果生成最终回答。"""
    result = latest_successful_tool_result(state)
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    evidences = data.get("evidences") if isinstance(data.get("evidences"), list) else []
    return {
        "answer": text_value(data.get("answer")) or "只读 Agent 已完成检索覆盖诊断",
        "evidenceIds": [str(item.get("evidenceId")) for item in evidences if isinstance(item, dict) and item.get("evidenceId")],
        "evidenceCount": len(evidences) if evidences else int_value(data.get("evidenceCount"), 0),
        "expandedQueries": data.get("expandedQueries") if isinstance(data.get("expandedQueries"), list) else [],
        "toolName": result.get("toolName"),
        "memoryContext": state.get("memory_context_pre") or [],
        "observedAt": utc_time_provider()["utcTime"],
        "riskLevel": "LOW",
    }


def synthesize_planning_draft(state: UnifiedAgentState, client: JavaAgentGatewayClient) -> dict[str, Any]:
    """从工具观察生成规划类草稿和待确认记忆候选。"""
    task_input = state.get("task_input") or {}
    jd_text = text_value(task_input.get("jobDescription"))
    resume_text = text_value(task_input.get("resumeText"))
    rag_result = latest_tool_result_by_name(state, "rag_query_probe_non_persistent")
    rag_data = rag_result.get("data") if isinstance(rag_result.get("data"), dict) else {}
    evidences = rag_data.get("evidences") if isinstance(rag_data.get("evidences"), list) else []
    evidence_ids = [str(item.get("evidenceId")) for item in evidences if isinstance(item, dict) and item.get("evidenceId")]
    requirements = extract_requirements(jd_text or state.get("user_goal") or "")
    alignment = build_alignment(requirements, resume_text, evidence_ids)
    gaps = build_gaps(alignment)
    risk_level = "LOW" if evidence_ids and not any(item["status"] == "missing" for item in alignment) else "MEDIUM"
    resume_template_fill = build_resume_template_fill_candidate(state, alignment, gaps, evidence_ids)
    draft = {
        "matchSummary": build_match_summary(alignment, evidence_ids),
        "alignment": alignment,
        "gaps": gaps,
        "evidenceIds": evidence_ids,
        "memoryContext": merge_memory_contexts(state),
        "webReferences": web_references_from_state(state),
        "resumeTemplateFill": resume_template_fill,
        "answer": text_value(rag_data.get("answer")),
        "expandedQueries": rag_data.get("expandedQueries") if isinstance(rag_data.get("expandedQueries"), list) else [],
        "riskLevel": risk_level,
        "verifier": {
            "completionCriteria": state.get("completion_criteria") or [],
            "toolCallCount": len(state.get("tool_calls") or []),
        },
    }
    candidates = request_memory_candidates(
        task_id=state["task_id"],
        task_input=task_input,
        draft=draft,
        final={},
        tool_observations=state.get("observations") or [],
        client=client,
    )
    if candidates:
        draft["pendingMemoryCandidates"] = candidates
        draft["memoryCandidateCount"] = len(candidates)
        draft["memoryWritePolicy"] = "候选默认不激活，需用户确认后才可写入长期记忆。"
    return draft


def build_resume_template_fill_candidate(
    state: UnifiedAgentState,
    alignment: list[dict[str, Any]],
    gaps: list[dict[str, str]],
    evidence_ids: list[str],
) -> dict[str, Any]:
    """生成简历模板填充值候选，不在 Agent 内直接读写 DOCX 文件。"""
    task_input = state.get("task_input") or {}
    tool_hints = task_input.get("toolHints")
    if not isinstance(tool_hints, list) or "resume_template_fill" not in [str(item) for item in tool_hints]:
        return {}
    content_map = build_resume_content_map(task_input, alignment, gaps, evidence_ids)
    return {
        "status": "PENDING_REVIEW",
        "toolName": "resume_template_fill",
        "contentMap": content_map,
        "requiresApproval": True,
        "approvalType": "OUTPUT",
        "message": "已生成简历模板填充值候选；Agent 不直接写 DOCX，需用户确认后由 Java/模板导出链路执行。",
    }


def latest_successful_tool_result(state: UnifiedAgentState) -> dict[str, Any]:
    """读取最近一次成功工具结果。"""
    for result in reversed(state.get("tool_results") or []):
        if result.get("status") == "SUCCEEDED":
            return result
    return {}


def latest_tool_result_by_name(state: UnifiedAgentState, tool_name: str) -> dict[str, Any]:
    """按工具名读取最近一次结果。"""
    for result in reversed(state.get("tool_results") or []):
        if result.get("toolName") == tool_name:
            return result
    return {}


def web_references_from_state(state: UnifiedAgentState) -> list[dict[str, Any]]:
    """从联网工具结果中提取外部参考。"""
    result = latest_tool_result_by_name(state, "web_search_probe")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    references = data.get("results") if isinstance(data.get("results"), list) else []
    return [item for item in references if isinstance(item, dict)]


def merge_memory_contexts(state: UnifiedAgentState) -> list[dict[str, Any]]:
    """合并两阶段记忆，按 memoryId 去重。"""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in (state.get("memory_context_pre") or []) + (state.get("memory_context_task") or []):
        memory_id = text_value(item.get("memoryId")) if isinstance(item, dict) else ""
        if memory_id and memory_id in seen:
            continue
        if memory_id:
            seen.add(memory_id)
        if isinstance(item, dict):
            merged.append(item)
    return merged


def request_memory_candidates(
    *,
    task_id: str,
    task_input: dict[str, Any],
    draft: dict[str, Any],
    final: dict[str, Any],
    tool_observations: list[dict[str, Any]],
    client: JavaAgentGatewayClient,
) -> list[dict[str, Any]]:
    """请求 Java Tool Gateway 生成记忆候选，不直接保存或激活。"""
    payload = {
        "taskId": task_id,
        "toolCallId": f"tool-call-memory-candidate-{uuid.uuid4().hex}",
        "toolName": "agent_memory_candidate_proposer",
        "arguments": {
            "taskInput": task_input,
            "draft": draft,
            "final": final,
            "toolObservations": tool_observations,
        },
    }
    try:
        result = client.execute_read_tool(payload)
    except Exception:
        return []
    if result.get("status") != "SUCCEEDED":
        return []
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def publish_post_answer_memory_candidates(
    task_id: str,
    thread_id: str,
    task_input: dict[str, Any],
    final: dict[str, Any],
    client: JavaAgentGatewayClient,
) -> None:
    """输出完成后按显式开关提炼记忆候选。"""
    if not should_run_post_answer_memory(task_input):
        return
    candidates = request_memory_candidates(
        task_id=task_id,
        task_input=task_input,
        draft={},
        final=final,
        tool_observations=[],
        client=client,
    )
    if candidates:
        client.publish_event(
            AgentTaskEvent(
                eventType="DRAFT_UPDATED",
                status="COMPLETED",
                pythonThreadId=thread_id,
                draft={
                    "message": "已生成回答后的待确认记忆候选",
                    "pendingMemoryCandidates": candidates,
                    "memoryWritePolicy": "候选不会自动激活，需用户确认后才会写入长期记忆。",
                },
            )
        )


def should_run_post_answer_memory(task_input: dict[str, Any]) -> bool:
    """判断回答后是否需要同步提炼记忆候选。"""
    goal = text_value(task_input.get("goal")) or text_value(task_input.get("question"))
    triggers = ["记住", "以后都", "以后请", "不要再", "偏好", "按这个来"]
    return bool(task_input.get("enablePostAnswerMemory")) or any(trigger in goal for trigger in triggers)


def skip_current_tool(state: UnifiedAgentState, reason: str) -> UnifiedAgentState:
    """跳过当前可降级工具，继续验收后续步骤。"""
    skipped = {
        "toolName": (state.get("current_action") or {}).get("toolName"),
        "status": "SKIPPED",
        "reason": reason,
    }
    return {
        **state,
        "repair_decision": "SKIP_TOOL",
        "status": "RUNNING",
        "current_step_index": int_value(state.get("current_step_index"), 0) + 1,
        "observations": list(state.get("observations") or []) + [skipped],
        "current_action": {},
    }


def web_search_enabled(task_input: dict[str, Any]) -> bool:
    """判断是否启用联网参考。"""
    if bool(task_input.get("enableWebSearch")):
        return True
    tool_hints = task_input.get("toolHints")
    return isinstance(tool_hints, list) and any(str(item) == "web_search_probe" for item in tool_hints)


def build_memory_aware_crud_review_request(request: AgentTaskResumeRequest) -> dict[str, Any]:
    """构造保存类 CRUD 审批，记忆保存仍作为变更工具处理。"""
    tool_name = mutation_tool_name(request.input)
    operation_type = {
        "resume_revision_save": "RESUME_REVISION_SAVE",
        "jd_learning_plan_save": "JD_PLAN_SAVE",
        "agent_task_cancel_request": "TASK_CANCEL",
        "agent_memory_candidate_save": "AGENT_MEMORY_CANDIDATE_SAVE",
    }.get(tool_name, "JD_PLAN_SAVE")
    idempotency_key = mutation_idempotency_key(request, tool_name)
    return {
        "id": f"review-crud-{request.taskId}",
        "reviewType": "CRUD",
        "proposal": {
            "title": "保存 Agent 草稿确认",
            "toolName": tool_name,
            "operationType": operation_type,
            "resourceType": "agent_memory" if tool_name == "agent_memory_candidate_save" else "agent_task_draft",
            "resourceId": request.taskId,
            "idempotencyKey": idempotency_key,
            "riskLevel": "MEDIUM",
            "undoable": True,
            "undoWindowMinutes": 30,
            "summary": "该操作属于数据库变更，需用户确认后由 Java Tool Gateway 校验并执行。",
        },
    }
