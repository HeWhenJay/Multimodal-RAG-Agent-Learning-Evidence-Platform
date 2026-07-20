from __future__ import annotations

import json
import os
import uuid
from typing import Any, Literal, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph

from agents.gateway.local_gateway import AgentGateway
from agents.llm.qwen_client import agent_qwen_model, get_agent_qwen_client
from agents.orchestration.planning_helpers import (
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
from agents.orchestration.read_only_helpers import (
    int_value,
    prefetch_memory_context,
    task_query,
    text_value,
    tool_observation_summary,
    utc_time_provider,
)
from app.schemas.agent import AgentTaskEvent, AgentTaskResumeRequest, AgentTaskStartRequest, AgentTaskStartResponse, AgentToolCallEvent


READ_EXECUTION_TOOLS = {
    "material_status_reader",
    "material_evidence_reader",
    "material_preview_reader",
    "rag_query_probe_non_persistent",
    "retrieval_coverage_probe",
    "evidence_quality_auditor",
    "resume_evidence_aligner",
    "gap_analyzer",
    "utc_time_provider",
    "web_search_probe",
    "agent_memory_retriever",
    "agent_memory_candidate_proposer",
}
PLAN_ALLOWED_TOOLS = READ_EXECUTION_TOOLS | {"resume_rewrite_subgraph"}
MUTATION_TOOLS = {
    "jd_learning_plan_save",
    "resume_revision_save",
    "agent_task_cancel_request",
    "agent_memory_candidate_save",
}
ALLOWED_INTERNAL_SUBGRAPHS = {"resume_rewrite_subgraph"}
AGENT_GRAPH_RECURSION_LIMIT = 24
AGENT_GRAPH_RECURSION_LIMIT_CODE = "AGENT_GRAPH_RECURSION_LIMIT"
DEFAULT_BEST_WINDOW_TOKENS = 18_000
DEFAULT_COMPRESSION_THRESHOLD_RATIO = 0.82
DEFAULT_MAX_CONTEXT_COMPRESSIONS = 2


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
    subgraph: str

    plan: dict[str, Any]
    plan_version: int
    plan_approved: bool
    completion_criteria: list[str]
    resume_rewrite_required: bool
    resume_rewrite_plan: dict[str, Any]
    resume_rewrite_draft: dict[str, Any]
    resume_rewrite_result: dict[str, Any]

    memory_context_pre: list[dict[str, Any]]
    memory_context_task: list[dict[str, Any]]
    restored_context: dict[str, Any]
    context_budget: dict[str, Any]
    context_summaries: list[dict[str, Any]]
    context_messages: list[dict[str, Any]]
    recalled_context_messages: list[dict[str, Any]]
    context_recall_keys: list[str]
    compression_candidate_messages: list[dict[str, Any]]
    active_summary_id: str
    compression_count: int

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
    llm_diagnostics: list[dict[str, Any]]

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


def start_unified_agent(request: AgentTaskStartRequest, client: AgentGateway) -> AgentTaskStartResponse:
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
    result = invoke_unified_graph_with_limit(state, client)
    return AgentTaskStartResponse(
        taskId=request.taskId,
        threadId=thread_id,
        accepted=True,
        status=str(result.get("status") or "FAILED"),
        errorCode=result.get("error_code"),
        errorMessage=result.get("error_message"),
    )


def resume_unified_agent(request: AgentTaskResumeRequest, client: AgentGateway) -> AgentTaskStartResponse:
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
        result = invoke_unified_graph_with_limit(state, client)
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
    result = invoke_unified_graph_with_limit(state, client)
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
    client: AgentGateway,
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


def invoke_unified_graph_with_limit(state: UnifiedAgentState, client: AgentGateway) -> UnifiedAgentState:
    """调用统一图并限制最大递归深度，防止 ReAct 循环空转。"""
    try:
        return build_unified_graph(client).invoke(state, {"recursion_limit": AGENT_GRAPH_RECURSION_LIMIT})
    except GraphRecursionError:
        failed = graph_recursion_limit_state(state)
        client.publish_event(
            AgentTaskEvent(
                eventType="TASK_FAILED",
                status="FAILED",
                pythonThreadId=str(failed.get("thread_id") or state.get("thread_id") or state.get("task_id")),
                final=failed.get("final_result") or {},
                errorCode=failed["error_code"],
                errorMessage=failed["error_message"],
            )
        )
        return failed


def graph_recursion_limit_state(state: UnifiedAgentState) -> UnifiedAgentState:
    """构造超过最大图深度后的失败状态和用户可读提示。"""
    message = (
        f"Agent 执行超过最大图深度 {AGENT_GRAPH_RECURSION_LIMIT}，已停止以避免循环。"
        "请缩小目标、减少工具步骤，或要求 Agent 重新规划。"
    )
    diagnostics = list(state.get("llm_diagnostics") or [])
    diagnostics.append(
        {
            "node": "graph",
            "provider": "langgraph",
            "model": "recursion_limit",
            "status": f"failed: exceeded {AGENT_GRAPH_RECURSION_LIMIT}",
        }
    )
    return {
        **state,
        "status": "FAILED",
        "error_code": AGENT_GRAPH_RECURSION_LIMIT_CODE,
        "error_message": message,
        "llm_diagnostics": diagnostics,
        "verifier_result": {
            "complete": False,
            "reason": message,
            "missingRequirements": state.get("completion_criteria") or [],
        },
        "completion_score": 0.0,
        "final_result": {
            "answer": message,
            "riskLevel": "HIGH",
            "diagnostics": {"llm": diagnostics},
        },
    }


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
        "subgraph": "",
        "plan_version": 1,
        "plan_approved": plan_approved,
        "current_step_index": 0,
        "tool_calls": [],
        "observations": [],
        "tool_results": [],
        "react_trace": [],
        "retry_count": 0,
        "max_retries": int_value(task_input.get("maxToolRetries"), 1),
        "llm_diagnostics": [],
        "approved_operation_ids": [],
        "idempotency_keys": [],
        "restored_context": {},
        "context_budget": {"bestWindowTokens": best_window_tokens(), "restoreSource": "postgresql"},
        "context_summaries": [],
        "context_messages": [],
        "recalled_context_messages": [],
        "context_recall_keys": [],
        "compression_candidate_messages": [],
        "active_summary_id": "",
        "compression_count": 0,
    }


def build_unified_graph(client: AgentGateway):
    """构建统一 PAE + ReAct LangGraph。"""
    workflow = StateGraph(UnifiedAgentState)
    workflow.add_node("conversation_title", lambda state: conversation_title_node(state, client))
    workflow.add_node("context_restore", lambda state: context_restore_node(state, client))
    workflow.add_node("task_router", task_router_node)
    workflow.add_node("memory_prefetch_before_planner", lambda state: memory_prefetch_before_planner(state, client))
    workflow.add_node("planner", lambda state: planner_node(state, client))
    workflow.add_node("plan_review", lambda state: plan_review_node(state, client))
    workflow.add_node("resume_rewrite_decision", resume_rewrite_decision_node)
    workflow.add_node("resume_rewrite_planner", lambda state: resume_rewrite_planner_node(state, client))
    workflow.add_node("resume_rewrite_generator", lambda state: resume_rewrite_generator_node(state, client))
    workflow.add_node("resume_rewrite_acceptance", lambda state: resume_rewrite_acceptance_node(state, client))
    workflow.add_node("memory_prefetch_after_planner", lambda state: memory_prefetch_after_planner(state, client))
    workflow.add_node("executor", lambda state: executor_node(state, client))
    workflow.add_node("tool_adapter", lambda state: tool_adapter_node(state, client))
    workflow.add_node("repair", lambda state: repair_node(state, client))
    workflow.add_node("acceptance", lambda state: acceptance_node(state, client))
    workflow.add_node("answer_writer", lambda state: answer_node(state, client))
    workflow.add_node("post_answer_memory", lambda state: post_answer_memory_node(state, client))

    workflow.set_entry_point("conversation_title")
    workflow.add_edge("conversation_title", "context_restore")
    workflow.add_edge("context_restore", "task_router")
    workflow.add_conditional_edges(
        "task_router",
        route_after_task_router,
        {
            "planner": "planner",
            "memory_prefetch_before_planner": "memory_prefetch_before_planner",
        },
    )
    workflow.add_edge("memory_prefetch_before_planner", "planner")
    workflow.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "plan_review": "plan_review",
            "resume_rewrite_decision": "resume_rewrite_decision",
            "memory_prefetch_after_planner": "memory_prefetch_after_planner",
        },
    )
    workflow.add_edge("plan_review", END)
    workflow.add_conditional_edges(
        "resume_rewrite_decision",
        route_after_resume_rewrite_decision,
        {
            "resume_rewrite_planner": "resume_rewrite_planner",
            "memory_prefetch_after_planner": "memory_prefetch_after_planner",
        },
    )
    workflow.add_edge("resume_rewrite_planner", "resume_rewrite_generator")
    workflow.add_edge("resume_rewrite_generator", "resume_rewrite_acceptance")
    workflow.add_conditional_edges(
        "resume_rewrite_acceptance",
        route_after_resume_rewrite_acceptance,
        {
            "memory_prefetch_after_planner": "memory_prefetch_after_planner",
            "answer_writer": "answer_writer",
        },
    )
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


def conversation_title_node(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """根据用户首句生成会话主题标题，并回写任务记录作为侧边栏展示值。

    该节点只读取当前用户首句和 fallback 标题，不打包历史消息、工具观察或恢复摘要，因此不触发上下文压缩。
    """
    task_input = state.get("task_input") or {}
    user_goal = text_value(state.get("user_goal")) or text_value(task_input.get("goal"))
    fallback_title = fallback_conversation_title(user_goal)
    prompt_goal = truncate_text(user_goal, 500)
    title = fallback_title
    try:
        result = get_agent_qwen_client().complete_json(
            node="conversation_title",
            model=agent_qwen_model("title"),
            system_prompt=conversation_title_system_prompt(),
            user_prompt=conversation_title_user_prompt(
                {
                    "goal": prompt_goal,
                    "fallbackTitle": fallback_title,
                    "inputTruncated": prompt_goal != user_goal,
                    "originalLength": len(user_goal),
                }
            ),
        )
        title = sanitize_conversation_title(text_value(result.data.get("conversationTitle")), fallback_title)
        record_llm_diagnostic(state, "conversation_title", result.model, "used")
    except Exception as exc:
        record_llm_diagnostic(state, "conversation_title", agent_qwen_model("title"), f"fallback: {exc}")
    publish_progress_event(
        client,
        state,
        node="conversation_title",
        phase="finished",
        message="Agent 已生成会话主题。",
        extra={"conversationTitle": title},
    )
    return state


def context_restore_node(state: UnifiedAgentState, client: AgentGateway) -> UnifiedAgentState:
    """从 PostgreSQL 恢复 L3 摘要段和最近原文窗口，缓存 miss 时仍可重建上下文。"""
    query = text_value(state.get("user_goal")) or task_query(state.get("task_input") or {}, state.get("task_type"))
    try:
        context = client.restore_context(
            state["task_id"],
            query=query,
            recent_limit=int(os.getenv("AGENT_CONTEXT_RECENT_MESSAGE_LIMIT", "12")),
            summary_limit=int(os.getenv("AGENT_CONTEXT_SUMMARY_LIMIT", "6")),
            best_window_tokens=best_window_tokens(),
        )
    except Exception as exc:
        record_llm_diagnostic(state, "context_restore", "python-postgresql-context", f"fallback: {exc}")
        context = {
            "messageWindow": [],
            "compressionCandidateMessages": [],
            "activeSummaries": [],
            "summarySegments": [],
            "budgetMetadata": {"restoreSource": "fallback_empty", "error": str(exc)},
        }
    messages = context.get("messageWindow") if isinstance(context.get("messageWindow"), list) else []
    compression_candidates = context.get("compressionCandidateMessages") if isinstance(context.get("compressionCandidateMessages"), list) else []
    active_summaries = context.get("activeSummaries") if isinstance(context.get("activeSummaries"), list) else []
    summary_segments = context.get("summarySegments") if isinstance(context.get("summarySegments"), list) else []
    budget = context.get("budgetMetadata") if isinstance(context.get("budgetMetadata"), dict) else {}
    active_summary_id = ""
    if active_summaries and isinstance(active_summaries[0], dict):
        active_summary_id = text_value(active_summaries[0].get("id"))
    publish_progress_event(
        client,
        state,
        node="context_restore",
        phase="finished",
        message=f"已从 PostgreSQL 恢复上下文：最近 {len(messages)} 条原文、{len(summary_segments)} 个摘要段。",
        extra={"restoreSource": budget.get("restoreSource") or "postgresql", "activeSummaryId": active_summary_id},
    )
    return {
        **state,
        "restored_context": context,
        "context_messages": messages,
        "compression_candidate_messages": compression_candidates,
        "context_summaries": summary_segments,
        "active_summary_id": active_summary_id,
        "context_budget": {
            **dict(state.get("context_budget") or {}),
            **budget,
            "bestWindowTokens": int_value(budget.get("promptTargetTokens"), best_window_tokens()),
        },
    }


def task_router_node(state: UnifiedAgentState) -> UnifiedAgentState:
    """统一任务路由器，只标记子图语义，不分流到旧图入口。"""
    task_type = state.get("task_type")
    task_input = state.get("task_input") or {}
    workspace_mode = text_value(task_input.get("workspaceMode"))
    if task_type == "planning_task":
        return {**state, "subgraph": "planning"}
    if task_type == "pure_read_query" or workspace_mode in {"read", "general"}:
        return {**state, "subgraph": "read_only"}
    return {
        **state,
        "subgraph": "planning",
        "status": "FAILED",
        "error_code": "AGENT_VALIDATION_FAILED",
        "error_message": "当前 Agent 任务类型暂不支持统一图执行",
    }


def publish_progress_event(
    client: AgentGateway | None,
    state: UnifiedAgentState,
    *,
    node: str,
    phase: str,
    message: str,
    status: str = "RUNNING",
    extra: dict[str, Any] | None = None,
) -> None:
    """回写节点级进度摘要，供前端事件流展示；不暴露隐藏推理链。"""
    if client is None:
        return
    event_type = "AGENT_NODE_STARTED" if phase == "started" else "AGENT_NODE_COMPLETED" if phase in {"finished", "completed"} else "AGENT_NODE_DELTA"
    draft: dict[str, Any] = {
        "message": message,
        "node": node,
        "phase": phase,
        "progressStatus": status,
    }
    if extra:
        draft.update(extra)
    try:
        client.publish_event(
            AgentTaskEvent(
                eventType=event_type,
                status=status,
                pythonThreadId=str(state.get("thread_id") or state.get("task_id")),
                draft=draft,
            )
        )
    except Exception:
        return


def memory_prefetch_before_planner(state: UnifiedAgentState, client: AgentGateway) -> UnifiedAgentState:
    """只读子图规划前读取偏好、历史约束和近期任务记忆。"""
    if state.get("status") == "FAILED":
        return state
    publish_progress_event(
        client,
        state,
        node="memory_prefetch_before_planner",
        phase="started",
        message="只读子图正在读取可注入记忆，用于约束计划和回答口径。",
    )
    query = task_query(state.get("task_input") or {}, state.get("task_type"))
    memory_context = prefetch_memory_context(
        task_id=state["task_id"],
        thread_id=state["thread_id"],
        task_input=state.get("task_input") or {},
        query=query,
        client=client,
    )
    return {**state, "memory_context_pre": memory_context}


def planner_node(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """PAE 规划器，输出执行计划和验收标准。"""
    if state.get("status") == "FAILED":
        return state
    publish_progress_event(
        client,
        state,
        node="planner",
        phase="started",
        message="Planner 正在生成 PAE 计划、完成标准和工具范围。",
    )
    task_input = state.get("task_input") or {}
    subgraph = state.get("subgraph")
    fallback_plan = build_planning_plan(task_input) if subgraph == "planning" else build_read_plan(task_input)
    plan = build_llm_planning_plan(state, fallback_plan, client)
    criteria = build_completion_criteria(subgraph, plan)
    publish_progress_event(
        client,
        state,
        node="planner",
        phase="finished",
        message="Planner 已生成计划草案，正在判断是否需要人工审批或进入执行链。",
        extra={
            "planTitle": plan.get("title"),
            "toolNames": plan.get("tools") or [],
            "riskLevel": plan.get("riskLevel"),
            "requiresPlanReview": plan.get("requiresPlanReview"),
        },
    )
    return {
        **state,
        "plan": plan,
        "completion_criteria": criteria,
        "current_step_index": 0,
        "current_action": {},
        "retry_count": 0,
        "repair_decision": "",
    }


def plan_review_node(state: UnifiedAgentState, client: AgentGateway) -> UnifiedAgentState:
    """发布计划审批请求，等待 Python worker 和前端恢复同一任务。"""
    plan = state.get("plan") or {}
    client.publish_event(
        AgentTaskEvent(
            eventType="REVIEW_REQUESTED",
            status="WAITING_PLAN_REVIEW",
            pythonThreadId=state["thread_id"],
            draft={
                "planSummary": plan.get("title"),
                "message": "规划器已生成执行路线，等待用户批准或要求修改。",
            },
            reviewRequest={
                "id": f"review-plan-{state['task_id']}",
                "reviewType": "PLAN",
                "proposal": plan,
            },
        )
    )
    return {**state, "status": "WAITING_PLAN_REVIEW"}


def resume_rewrite_decision_node(state: UnifiedAgentState) -> UnifiedAgentState:
    """根据 Planner 输出判断是否进入简历修改子图。"""
    if state.get("status") == "FAILED" or state.get("subgraph") != "planning":
        return state
    required = should_enter_resume_rewrite_subgraph(state)
    return {**state, "resume_rewrite_required": required}


def resume_rewrite_planner_node(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """生成简历修改子图的局部计划。"""
    if state.get("status") == "FAILED" or not state.get("resume_rewrite_required"):
        return state
    task_input = state.get("task_input") or {}
    jd_text = text_value(task_input.get("jobDescription"))
    resume_text = text_value(task_input.get("resumeText"))
    requirements = extract_requirements(jd_text or state.get("user_goal") or "")
    rewrite_plan = {
        "title": "简历修改子图计划",
        "scope": "PENDING_REVIEW_RESUME_DRAFT",
        "steps": [
            {"name": "定位岗位要求", "description": "从 JD 中抽取需要在简历中回应的能力要求"},
            {"name": "比对现有简历", "description": "判断哪些要求已有表达、哪些需要改写或补充"},
            {"name": "生成候选片段", "description": "输出摘要、技能、项目经历和缺口说明候选，不直接写 DOCX"},
        ],
        "targetRequirements": requirements,
        "hasResumeText": bool(resume_text),
        "guardrails": [
            "不直接写 DOCX",
            "不直接保存业务数据",
            "候选片段必须进入 OUTPUT 审批",
        ],
    }
    rewrite_plan = build_llm_resume_rewrite_plan(state, rewrite_plan, client)
    return {**state, "resume_rewrite_plan": rewrite_plan}


def resume_rewrite_generator_node(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """生成简历修改候选片段，供最终 OUTPUT 审批展示。"""
    if state.get("status") == "FAILED" or not state.get("resume_rewrite_required"):
        return state
    task_input = state.get("task_input") or {}
    requirements = list((state.get("resume_rewrite_plan") or {}).get("targetRequirements") or [])
    resume_text = text_value(task_input.get("resumeText"))
    provisional_alignment = build_alignment(requirements, resume_text, [])
    provisional_gaps = build_gaps(provisional_alignment)
    content_map = build_resume_content_map(task_input, provisional_alignment, provisional_gaps, [])
    draft = {
        "status": "PENDING_REVIEW",
        "toolName": "resume_rewrite_subgraph",
        "requiresApproval": True,
        "approvalType": "OUTPUT",
        "message": "Planner 检测到需要修改简历，已进入简历修改子图并生成待确认候选。",
        "contentMap": content_map,
        "rewriteTargets": requirements,
        "patches": build_resume_rewrite_patches(content_map, provisional_gaps),
    }
    draft = build_llm_resume_rewrite_draft(state, draft, client)
    return {**state, "resume_rewrite_draft": draft}


def resume_rewrite_acceptance_node(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """验收简历修改子图候选是否可并入规划草稿。"""
    if state.get("status") == "FAILED" or not state.get("resume_rewrite_required"):
        return state
    draft = state.get("resume_rewrite_draft") or {}
    if not draft.get("contentMap"):
        return {
            **state,
            "status": "FAILED",
            "error_code": "AGENT_RESUME_REWRITE_EMPTY",
            "error_message": "简历修改子图没有生成可审批候选",
            "resume_rewrite_result": {"accepted": False},
        }
    fallback_result = {
        "accepted": True,
        "requiresOutputReview": True,
        "candidateCount": len(draft.get("patches") or []),
    }
    result = build_llm_resume_rewrite_acceptance(state, fallback_result, client)
    return {**state, "resume_rewrite_result": result}


def memory_prefetch_after_planner(state: UnifiedAgentState, client: AgentGateway) -> UnifiedAgentState:
    """规划后基于计划步骤重新读取任务相关记忆。"""
    if state.get("status") == "FAILED":
        return state
    if state.get("subgraph") != "planning":
        return state
    publish_progress_event(
        client,
        state,
        node="memory_prefetch_after_planner",
        phase="started",
        message="计划已批准，正在按计划意图读取任务相关记忆。",
    )
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


def executor_node(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """ReAct 执行器，根据计划选择当前步骤的行动。"""
    if state.get("status") == "FAILED":
        return state
    steps = list((state.get("plan") or {}).get("steps") or [])
    index = int_value(state.get("current_step_index"), 0)
    if index >= len(steps):
        return {**state, "current_action": {}}
    step = steps[index] if isinstance(steps[index], dict) else {}
    fallback_action = build_action_for_step(state, step)
    action = build_llm_action_for_step(state, step, fallback_action, client)
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


def tool_adapter_node(state: UnifiedAgentState, client: AgentGateway) -> UnifiedAgentState:
    """统一工具节点，只通过 Python 本地 Gateway 调用只读或变更工具。"""
    action = state.get("current_action") or {}
    tool_name = text_value(action.get("toolName"))
    if not tool_name:
        return {**state, "status": "FAILED", "error_code": "AGENT_TOOL_UNKNOWN", "error_message": "执行器未选择工具"}
    tool_type = text_value(action.get("toolType")) or "READ"
    tool_call_id = text_value(action.get("toolCallId")) or f"tool-call-{uuid.uuid4().hex}"
    publish_progress_event(
        client,
        state,
        node="tool_adapter",
        phase="started",
        message=f"正在通过 Python 本地 Gateway 调用工具：{tool_name}。",
        status="WAITING_TOOL_RESULT",
        extra={"toolName": tool_name, "toolType": tool_type, "toolCallId": tool_call_id},
    )
    gate_error = validate_tool_action_for_adapter(tool_name, tool_type)
    if gate_error:
        result = {
            "taskId": state["task_id"],
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "status": "FAILED",
            "errorCode": gate_error["errorCode"],
            "errorMessage": gate_error["errorMessage"],
            "retryable": False,
        }
        status = "FAILED"
        client.publish_event(
            AgentTaskEvent(
                eventType="TOOL_CALL_COMPLETED",
                status="FAILED",
                pythonThreadId=state["thread_id"],
                toolCall=AgentToolCallEvent(
                    id=tool_call_id,
                    toolName=tool_name,
                    toolType=tool_type,
                    status=status,
                    response=tool_observation_summary(result),
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
                "retryable": False,
            },
            "status": "TOOL_FAILED",
            "error_code": str(result.get("errorCode")),
            "error_message": str(result.get("errorMessage")),
            "tool_results": list(state.get("tool_results") or []) + [result],
        }
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
                "errorMessage": f"本地工具调用失败：{exc}",
                "retryable": True,
            }
    status = str(result.get("status") or "FAILED")
    client.publish_event(
        AgentTaskEvent(
            eventType="TOOL_CALL_COMPLETED",
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


def repair_node(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """修补节点，判断重试、跳过、重规划或汇报无法完成。"""
    llm_state = apply_llm_repair_decision(state, client)
    if llm_state is not state:
        return llm_state
    failure = state.get("failure_reason") or {}
    tool_name = text_value(failure.get("toolName"))
    error_code = text_value(failure.get("errorCode"))
    retry_count = int_value(state.get("retry_count"), 0)
    max_retries = int_value(state.get("max_retries"), 1)
    hard_stop_codes = {
        "AGENT_RESOURCE_FORBIDDEN",
        "AGENT_MEMORY_FORBIDDEN",
        "AGENT_MEMORY_SCOPE_ESCALATION",
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


def acceptance_node(state: UnifiedAgentState, client: AgentGateway) -> UnifiedAgentState:
    """验收器，判断计划是否完成、是否回到执行器或进入回答节点。"""
    publish_progress_event(
        client,
        state,
        node="acceptance",
        phase="started",
        message="验收器正在检查计划步骤、工具观察和完成标准。",
        extra={"currentStepIndex": state.get("current_step_index"), "toolCallCount": len(state.get("tool_calls") or [])},
    )
    state = apply_llm_acceptance_result(state, client)
    if state.get("status") == "TOOL_FAILED":
        return {
            **state,
            "verifier_result": {
                "complete": False,
                "requiresRepair": True,
                "reason": state.get("error_message") or "验收节点要求进入修补",
            },
            "completion_score": 0.0,
        }
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
    elif current_index < len(steps):
        if should_skip_empty_executor_action(state, current_index):
            return skip_current_step_without_action(state, "执行器判断当前步骤无需调用工具，已跳过以避免空转。")
        return {**state, "current_step_index": current_index, "current_action": {}, "verifier_result": {"complete": False}}
    if current_index < len(steps):
        return {**state, "current_step_index": current_index, "current_action": {}, "verifier_result": {"complete": False}}
    if state.get("subgraph") == "planning":
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


def answer_node(state: UnifiedAgentState, client: AgentGateway) -> UnifiedAgentState:
    """回答节点，组织中文结果并回写任务投影。"""
    state = apply_llm_answer_writer(state, client)
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


def post_answer_memory_node(state: UnifiedAgentState, client: AgentGateway) -> UnifiedAgentState:
    """回答后整理长期记忆候选；默认只在显式开启或用户表达记住时运行。"""
    task_input = state.get("task_input") or {}
    if state.get("status") != "COMPLETED" or not should_run_post_answer_memory(task_input):
        return state
    context_budget_guard(state, "post_answer_memory", client)
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


def route_after_task_router(state: UnifiedAgentState) -> Literal["planner", "memory_prefetch_before_planner"]:
    """任务路由后决定首个业务节点，规划类任务先生成计划再中断审批。"""
    if state.get("subgraph") == "planning":
        return "planner"
    return "memory_prefetch_before_planner"


def route_after_planner(state: UnifiedAgentState) -> Literal["plan_review", "resume_rewrite_decision", "memory_prefetch_after_planner"]:
    """规划器之后判断是否需要计划审批。"""
    if state.get("status") == "FAILED":
        return "memory_prefetch_after_planner"
    if state.get("subgraph") == "planning" and not state.get("plan_approved"):
        return "plan_review"
    if state.get("subgraph") == "planning":
        return "resume_rewrite_decision"
    return "memory_prefetch_after_planner"


def route_after_resume_rewrite_decision(state: UnifiedAgentState) -> Literal["resume_rewrite_planner", "memory_prefetch_after_planner"]:
    """简历修改判定后决定是否进入简历修改子图。"""
    return "resume_rewrite_planner" if state.get("resume_rewrite_required") else "memory_prefetch_after_planner"


def route_after_resume_rewrite_acceptance(state: UnifiedAgentState) -> Literal["memory_prefetch_after_planner", "answer_writer"]:
    """简历修改子图验收后回到 Planning 主执行链或直接汇报失败。"""
    return "answer_writer" if state.get("status") == "FAILED" else "memory_prefetch_after_planner"


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
    if state.get("status") == "TOOL_FAILED":
        return "repair"
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


def build_llm_planning_plan(
    state: UnifiedAgentState,
    fallback_plan: dict[str, Any],
    client: AgentGateway | None = None,
) -> dict[str, Any]:
    """调用千问辅助生成计划，失败或越权时回退确定性计划。"""
    task_input = state.get("task_input") or {}
    prompt = {
        "node": "planner",
        "taskType": state.get("task_type"),
        "subgraph": state.get("subgraph"),
        "goal": state.get("user_goal"),
        "allowedTools": sorted(PLAN_ALLOWED_TOOLS),
        "allowedSubgraphs": sorted(ALLOWED_INTERNAL_SUBGRAPHS),
        "taskInputSummary": summarize_task_input(task_input),
        "fallbackPlan": fallback_plan,
        "expectedJson": {
            "title": "字符串",
            "steps": [{"description": "字符串", "toolName": "allowedTools 中的工具", "toolType": "READ 或 INTERNAL_SUBGRAPH", "expectedOutput": "字符串"}],
            "tools": ["allowedTools 中的工具"],
            "internalSubgraphs": ["resume_rewrite_subgraph"],
            "resumeRewriteIntent": False,
            "requiresPlanReview": True,
            "requiresOutputReview": True,
            "riskLevel": "LOW/MEDIUM/HIGH",
            "guardrails": ["字符串"],
        },
    }
    prompt = prepare_llm_payload(state, "planner", prompt, client)
    try:
        result = get_agent_qwen_client().complete_json(
            node="planner",
            model=agent_qwen_model("planner"),
            system_prompt=planner_system_prompt(),
            user_prompt=planner_user_prompt(prompt),
        )
        plan = sanitize_plan(result.data, state, fallback_plan)
        record_llm_diagnostic(state, "planner", result.model, "used")
        return plan
    except Exception as exc:
        record_llm_diagnostic(state, "planner", agent_qwen_model("planner"), f"fallback: {exc}")
        return fallback_plan


def prepare_llm_payload(
    state: UnifiedAgentState,
    node: str,
    payload: dict[str, Any],
    client: AgentGateway | None = None,
) -> dict[str, Any]:
    """统一为长 prompt 节点注入恢复上下文和预算诊断，必要时先压缩早期上下文。"""
    guarded = context_budget_guard(state, node, client)
    guarded = maybe_recall_summary_context(guarded, node, client)
    restored_context = {
        "activeSummaryId": guarded.get("active_summary_id"),
        "summarySegments": compact_summary_segments(guarded.get("context_summaries") or []),
        "recentMessages": compact_recent_messages(guarded.get("context_messages") or []),
        "recalledMessages": compact_recent_messages(guarded.get("recalled_context_messages") or []),
        "budget": guarded.get("context_budget") or {},
        "restorePolicy": "L1 当前 prompt 窗口；L2 Redis 仅短期热态缓存；L3 PostgreSQL 消息和摘要段是恢复权威来源。",
    }
    return {**payload, "restoredContext": restored_context}


def maybe_recall_summary_context(
    state: UnifiedAgentState,
    node: str,
    client: AgentGateway | None,
) -> UnifiedAgentState:
    """按摘要段回捞覆盖范围附近少量原文，只从 Python 持久化层访问。"""
    if client is None:
        return state
    summaries = [item for item in list(state.get("context_summaries") or []) if isinstance(item, dict)]
    if not summaries or state.get("recalled_context_messages"):
        return state
    recall_keys = list(state.get("context_recall_keys") or [])
    recall_key = f"summary:{node}"
    if recall_key in recall_keys or len(recall_keys) >= 1:
        return state
    summary = choose_summary_for_recall(state, summaries)
    if not summary:
        return state
    summary_id = text_value(summary.get("id") or summary.get("summaryId"))
    params = {
        "summaryId": summary_id,
        "coveredMessageStartId": text_value(summary.get("coveredMessageStartId")),
        "coveredMessageEndId": text_value(summary.get("coveredMessageEndId")),
        "before": 1,
        "after": 1,
        "limit": 6,
    }
    params = {key: value for key, value in params.items() if value != ""}
    state["context_recall_keys"] = recall_keys + [recall_key]
    try:
        recalled = client.recall_context_messages(text_value(state.get("task_id")), params)
        if recalled:
            state["recalled_context_messages"] = [item for item in recalled if isinstance(item, dict)][:6]
            record_llm_diagnostic(state, "context_recall", "python-postgresql", f"recalled:{node}")
        else:
            record_llm_diagnostic(state, "context_recall", "python-postgresql", f"empty:{node}")
    except Exception as exc:
        record_llm_diagnostic(state, "context_recall", "python-postgresql", f"recall_failed:{node}:{exc}")
    return state


def choose_summary_for_recall(state: UnifiedAgentState, summaries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """优先选择与当前目标有关键词命中的摘要段，否则回退第一段。"""
    terms = recall_query_terms(text_value(state.get("user_goal")))
    if terms:
        for summary in summaries:
            haystack = summary_recall_text(summary)
            if any(term in haystack for term in terms):
                return summary
    return summaries[0] if summaries else None


def recall_query_terms(query: str) -> list[str]:
    """提取用于摘要段回捞的轻量关键词。"""
    normalized = "".join(ch if ("\u4e00" <= ch <= "\u9fff") or ch.isalnum() else " " for ch in query.lower())
    return [item for item in normalized.split() if len(item) >= 2][:12]


def summary_recall_text(summary: dict[str, Any]) -> str:
    """把摘要段关键字段拼成关键词匹配文本。"""
    parts = [text_value(summary.get("summaryText"))]
    for key in ["keyFacts", "evidenceRefs"]:
        value = summary.get(key)
        if isinstance(value, list):
            parts.extend(json.dumps(item, ensure_ascii=False, default=str) for item in value if isinstance(item, dict))
    parts.append(json.dumps(summary.get("summary") or {}, ensure_ascii=False, default=str) if isinstance(summary.get("summary"), dict) else "")
    return " ".join(parts).lower()


def context_budget_guard(
    state: UnifiedAgentState,
    node: str,
    client: AgentGateway | None,
) -> UnifiedAgentState:
    """检查上下文估算 token，超过 best window 时压缩早期窗口并保存摘要段。"""
    budget = dict(state.get("context_budget") or {})
    best = int_value(budget.get("bestWindowTokens"), best_window_tokens())
    threshold = int(best * compression_threshold_ratio())
    current = estimate_state_tokens(state)
    candidates = [item for item in list(state.get("compression_candidate_messages") or []) if isinstance(item, dict)]
    max_compressions = max_context_compressions()
    budget.update(
        {
            "lastNode": node,
            "estimatedTokens": current,
            "compressionThresholdTokens": threshold,
            "compressionCandidateCount": len(candidates),
            "maxCompressionsPerInvocation": max_compressions,
        }
    )
    state["context_budget"] = budget
    if current <= threshold or not candidates or int_value(state.get("compression_count"), 0) >= max_compressions:
        return state
    summary = build_conversation_compression(state, node)
    state["compression_count"] = int_value(state.get("compression_count"), 0) + 1
    state["context_budget"] = {
        **budget,
        "compressed": True,
        "compressionNode": node,
        "postCompressionEstimatedTokens": estimate_tokens(summary.get("summaryText")),
        "remainingCompressionCandidateCount": len(candidates),
    }
    if client is not None:
        try:
            saved = client.save_context_summary(state["task_id"], summary)
            persisted_summary = {**summary, **saved}
            state["context_summaries"] = [persisted_summary] + list(state.get("context_summaries") or [])
            state["active_summary_id"] = text_value(saved.get("id")) or text_value(summary.get("summaryId")) or text_value(state.get("active_summary_id"))
            state["compression_candidate_messages"] = []
            state["context_budget"] = {**dict(state.get("context_budget") or {}), "remainingCompressionCandidateCount": 0}
            record_llm_diagnostic(state, "conversation_compression", text_value(summary.get("compressionModel")), "saved")
            client.publish_event(
                AgentTaskEvent(
                    eventType="CONTEXT_COMPRESSED",
                    status="RUNNING",
                    pythonThreadId=str(state.get("thread_id") or state.get("task_id")),
                    draft={
                        "message": "早期上下文已压缩并保存为可恢复摘要段。",
                        "node": "conversation_compression",
                        "phase": "finished",
                        "summaryId": state["active_summary_id"],
                        "coveredMessageCount": summary.get("coveredMessageCount"),
                    },
                )
            )
        except Exception as exc:
            record_llm_diagnostic(state, "conversation_compression", text_value(summary.get("compressionModel")), f"save_failed: {exc}")
    else:
        state["context_budget"] = {**dict(state.get("context_budget") or {}), "persistenceStatus": "SKIPPED_NO_GATEWAY"}
    return state


def build_conversation_compression(state: UnifiedAgentState, node: str) -> dict[str, Any]:
    """生成可持久恢复的上下文压缩摘要，LLM 不可用时使用确定性 fallback。"""
    messages = list(state.get("compression_candidate_messages") or [])
    if not messages:
        messages = list(state.get("context_messages") or [])
    summaries = list(state.get("context_summaries") or [])
    raw_text = "\n".join(compact_message_text(item) for item in messages if isinstance(item, dict))
    fallback = deterministic_context_summary(state, node, messages, summaries)
    model = agent_qwen_model("compression")
    try:
        result = get_agent_qwen_client().complete_json(
            node="conversation_compression",
            model=model,
            system_prompt=conversation_compression_system_prompt(),
            user_prompt=conversation_compression_user_prompt(
                {
                    "goal": state.get("user_goal"),
                    "node": node,
                    "compressionCandidateMessages": compact_compression_candidate_messages(messages),
                    "recentMessages": compact_recent_messages(state.get("context_messages") or []),
                    "existingSummaries": compact_summary_segments(summaries),
                    "toolFindings": summarize_observations(state.get("observations") or []),
                    "expectedJson": fallback["summary"],
                }
            ),
        )
        summary_body = sanitize_context_summary(result.data, fallback["summary"])
        record_llm_diagnostic(state, "conversation_compression", result.model, "used")
        model = result.model
    except Exception as exc:
        summary_body = fallback["summary"]
        record_llm_diagnostic(state, "conversation_compression", model, f"fallback: {exc}")
    summary_body = constrain_context_summary_to_messages(summary_body, messages)
    covered = summary_body.get("coveredMessageRange") if isinstance(summary_body.get("coveredMessageRange"), dict) else {}
    summary_text = text_value(summary_body.get("rollingSummary")) or fallback["summaryText"]
    key_facts = summary_body.get("keyFacts") if isinstance(summary_body.get("keyFacts"), list) else []
    evidence_refs = summary_body.get("evidenceRefs") if isinstance(summary_body.get("evidenceRefs"), list) else []
    loss_risk = text_value(summary_body.get("lossRisk")) or "LOW"
    status = "HIGH_LOSS_RISK" if loss_risk.upper() == "HIGH" else "ACTIVE"
    return {
        "summaryId": f"agent-summary-{uuid.uuid4().hex}",
        "summaryType": "CONTEXT_COMPRESSION",
        "coveredMessageStartId": text_value(covered.get("startId")) or first_message_id(messages),
        "coveredMessageEndId": text_value(covered.get("endId")) or last_message_id(messages),
        "coveredMessageCount": len(messages),
        "rawTokenEstimate": estimate_tokens(raw_text),
        "compressedTokenEstimate": estimate_tokens(summary_text),
        "summary": summary_body,
        "summaryText": summary_text,
        "keyFacts": normalize_object_list(key_facts),
        "evidenceRefs": normalize_object_list(evidence_refs),
        "compressionModel": model,
        "compressionPromptVersion": "agent-context-compression-v1",
        "compressionVersion": 1,
        "status": status,
        "diagnostics": {
            "triggerNode": node,
            "lossRisk": loss_risk,
            "bestWindowTokens": best_window_tokens(),
            "candidateSource": "compression_candidate_messages",
            "redisPolicy": "Redis TTL 不影响恢复；摘要和消息原文已落 PostgreSQL。",
        },
    }


def deterministic_context_summary(
    state: UnifiedAgentState,
    node: str,
    messages: list[dict[str, Any]],
    summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    """无模型 Key 时生成确定性摘要，保证测试和离线开发可运行。"""
    recent_text = [compact_message_text(item) for item in messages[-8:] if isinstance(item, dict)]
    rolling = "；".join(item for item in recent_text if item)[:1200] or text_value(state.get("user_goal")) or "Agent 会话上下文摘要"
    evidence_refs = collect_evidence_refs_from_state(state)
    last_ids = [text_value(item.get("id")) for item in messages[-6:] if isinstance(item, dict) and item.get("id")]
    summary = {
        "rollingSummary": rolling,
        "keyFacts": [{"text": text_value(state.get("user_goal")) or "用户正在继续同一 Agent 会话", "source": "task_input"}],
        "openQuestions": [],
        "decisions": [],
        "userPreferences": [],
        "taskState": {"node": node, "status": state.get("status"), "subgraph": state.get("subgraph")},
        "toolFindings": summarize_observations(state.get("observations") or []),
        "evidenceRefs": evidence_refs,
        "lastRawMessageIds": last_ids,
        "coveredMessageRange": {"startId": first_message_id(messages), "endId": last_message_id(messages)},
        "compressionVersion": 1,
        "confidence": 0.62 if summaries else 0.58,
        "lossRisk": "MEDIUM" if estimate_tokens(json.dumps(messages, ensure_ascii=False, default=str)) > best_window_tokens() else "LOW",
    }
    return {"summary": summary, "summaryText": rolling}


def best_window_tokens() -> int:
    """读取 prompt 最佳使用窗口，不吃满模型上下文。"""
    return max(4_000, int(os.getenv("AGENT_CONTEXT_BEST_WINDOW_TOKENS", str(DEFAULT_BEST_WINDOW_TOKENS))))


def compression_threshold_ratio() -> float:
    """读取压缩触发比例，默认在 best window 约 82% 触发。"""
    try:
        return min(0.95, max(0.5, float(os.getenv("AGENT_CONTEXT_COMPRESSION_THRESHOLD_RATIO", str(DEFAULT_COMPRESSION_THRESHOLD_RATIO)))))
    except ValueError:
        return DEFAULT_COMPRESSION_THRESHOLD_RATIO


def max_context_compressions() -> int:
    """限制单次图执行内的压缩段数，避免异常状态反复压缩。"""
    try:
        return max(1, min(4, int(os.getenv("AGENT_CONTEXT_MAX_COMPRESSIONS", str(DEFAULT_MAX_CONTEXT_COMPRESSIONS)))))
    except ValueError:
        return DEFAULT_MAX_CONTEXT_COMPRESSIONS


def estimate_state_tokens(state: UnifiedAgentState) -> int:
    """粗略估算当前 prompt 相关状态 token。"""
    payload = {
        "goal": state.get("user_goal"),
        "taskInput": summarize_task_input(state.get("task_input") or {}),
        "contextMessages": compact_recent_messages(state.get("context_messages") or []),
        "compressionCandidateMessages": compact_compression_candidate_messages(state.get("compression_candidate_messages") or []),
        "contextSummaries": compact_summary_segments(state.get("context_summaries") or []),
        "plan": state.get("plan") or {},
        "observations": summarize_observations(state.get("observations") or []),
        "draft": summarize_result(state.get("draft_result") or {}),
        "final": summarize_result(state.get("final_result") or {}),
    }
    return estimate_tokens(json.dumps(payload, ensure_ascii=False, default=str))


def estimate_tokens(value: Any) -> int:
    """中文场景用字符数近似 token，作为预算保护的保守估计。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return 0 if not text else max(1, len(text) // 2)


def compact_recent_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """压缩最近原文窗口，避免把完整 payload 注入 LLM。"""
    compacted = []
    for item in messages[-12:]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "id": item.get("id"),
                "role": item.get("role"),
                "messageType": item.get("messageType"),
                "content": truncate_text(text_value(item.get("content")), 800),
                "createdAt": item.get("createdAt"),
            }
        )
    return compacted


def compact_compression_candidate_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """压缩候选窗口不进入业务 prompt，只供 conversation_compression 使用。"""
    compacted = []
    for item in messages[:40]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "id": item.get("id"),
                "role": item.get("role"),
                "messageType": item.get("messageType"),
                "content": truncate_text(text_value(item.get("content")), 1000),
                "createdAt": item.get("createdAt"),
            }
        )
    return compacted


def compact_summary_segments(summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """压缩摘要段，只保留恢复和检索所需字段。"""
    compacted = []
    for item in summaries[:8]:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "id": item.get("id") or item.get("summaryId"),
                "status": item.get("status"),
                "summaryText": truncate_text(text_value(item.get("summaryText")), 1200),
                "keyFacts": item.get("keyFacts") if isinstance(item.get("keyFacts"), list) else [],
                "evidenceRefs": item.get("evidenceRefs") if isinstance(item.get("evidenceRefs"), list) else [],
                "coveredMessageStartId": item.get("coveredMessageStartId"),
                "coveredMessageEndId": item.get("coveredMessageEndId"),
            }
        )
    return compacted


def compact_message_text(message: dict[str, Any]) -> str:
    """把消息转换成摘要输入文本。"""
    role = text_value(message.get("role"))
    message_type = text_value(message.get("messageType"))
    content = truncate_text(text_value(message.get("content")), 600)
    return f"{role}/{message_type}: {content}".strip()


def first_message_id(messages: list[dict[str, Any]]) -> str:
    """获取窗口第一条消息 ID。"""
    for item in messages:
        if isinstance(item, dict) and item.get("id"):
            return text_value(item.get("id"))
    return ""


def last_message_id(messages: list[dict[str, Any]]) -> str:
    """获取窗口最后一条消息 ID。"""
    for item in reversed(messages):
        if isinstance(item, dict) and item.get("id"):
            return text_value(item.get("id"))
    return ""


def normalize_object_list(items: Any) -> list[dict[str, Any]]:
    """只保留对象数组，避免摘要 JSON 结构失控。"""
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def sanitize_context_summary(candidate: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """校验压缩摘要结构，缺字段时回填确定性摘要。"""
    result = dict(fallback)
    if not isinstance(candidate, dict):
        return result
    for key in [
        "rollingSummary",
        "keyFacts",
        "openQuestions",
        "decisions",
        "userPreferences",
        "taskState",
        "toolFindings",
        "evidenceRefs",
        "lastRawMessageIds",
        "coveredMessageRange",
        "compressionVersion",
        "confidence",
        "lossRisk",
    ]:
        if key in candidate:
            result[key] = candidate[key]
    if not text_value(result.get("rollingSummary")):
        result["rollingSummary"] = fallback.get("rollingSummary") or "Agent 上下文摘要"
    return result


def constrain_context_summary_to_messages(summary: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any]:
    """把摘要覆盖范围限制在本次压缩候选窗口内，避免模型幻觉消息 ID。"""
    result = dict(summary) if isinstance(summary, dict) else {}
    ordered_ids = [text_value(item.get("id")) for item in messages if isinstance(item, dict) and text_value(item.get("id"))]
    if not ordered_ids:
        result["coveredMessageRange"] = {"startId": "", "endId": ""}
        result["lastRawMessageIds"] = []
        return result

    index = {message_id: pos for pos, message_id in enumerate(ordered_ids)}
    covered = result.get("coveredMessageRange") if isinstance(result.get("coveredMessageRange"), dict) else {}
    start_id = text_value(covered.get("startId"))
    end_id = text_value(covered.get("endId"))
    if start_id not in index or end_id not in index or index[start_id] > index[end_id]:
        start_id = ordered_ids[0]
        end_id = ordered_ids[-1]
    result["coveredMessageRange"] = {"startId": start_id, "endId": end_id}

    raw_ids = result.get("lastRawMessageIds") if isinstance(result.get("lastRawMessageIds"), list) else []
    filtered_ids = []
    for item in raw_ids:
        message_id = text_value(item)
        if message_id in index and message_id not in filtered_ids:
            filtered_ids.append(message_id)
    result["lastRawMessageIds"] = filtered_ids or ordered_ids[-6:]
    return result


def collect_evidence_refs_from_state(state: UnifiedAgentState) -> list[dict[str, Any]]:
    """从工具结果中收集 evidence 引用，不保存正文片段。"""
    refs: list[dict[str, Any]] = []
    for result in state.get("tool_results") or []:
        data = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else {}
        evidences = data.get("evidences") if isinstance(data.get("evidences"), list) else []
        for evidence in evidences[:8]:
            if isinstance(evidence, dict) and evidence.get("evidenceId"):
                refs.append({"type": "rag_evidence", "id": str(evidence.get("evidenceId")), "title": text_value(evidence.get("title"))})
    return refs


def conversation_compression_system_prompt() -> str:
    """上下文压缩系统提示。"""
    return (
        "你是 Agent 上下文压缩器。先保留关键事实，再压缩摘要，输出唯一 JSON。"
        "不要丢失用户硬约束、审批决策、工具发现、evidence 引用和当前任务状态。"
        "不得编造资料正文或新的 evidence。"
    )


def conversation_compression_user_prompt(payload: dict[str, Any]) -> str:
    """上下文压缩用户提示。"""
    return json.dumps(payload, ensure_ascii=False, default=str)


def build_planning_plan(task_input: dict[str, Any]) -> dict[str, Any]:
    """为规划类任务生成需要用户确认的 PAE 计划。"""
    goal = text_value(task_input.get("goal")) or "JD/简历适配分析"
    resume_rewrite_intent = detect_resume_rewrite_intent(task_input)
    use_web_search = web_search_enabled(task_input)
    steps: list[dict[str, Any]] = []
    if use_web_search:
        steps.append(
            {
                "description": "优先联网查询外部资料，获取最新背景、技能趋势或学习资源",
                "toolName": "web_search_probe",
                "toolType": "READ",
                "expectedOutput": "外部参考摘要，不写入 RAG evidence 或长期记忆；失败时降级到本地 RAG",
            }
        )
    steps.append(
        {
            "description": "检索当前用户 RAG evidence，作为本地证据补充或联网不可用时的降级依据",
            "toolName": "rag_query_probe_non_persistent",
            "toolType": "READ",
            "expectedOutput": "当前用户知识库 evidence、expandedQueries 和 diagnostics",
        }
    )
    return {
        "title": f"{goal[:40]} 计划",
        "steps": steps,
        "tools": [step["toolName"] for step in steps] + ["resume_evidence_aligner", "gap_analyzer", "evidence_quality_auditor"],
        "internalSubgraphs": ["resume_rewrite_subgraph"] if resume_rewrite_intent else [],
        "resumeRewriteIntent": resume_rewrite_intent,
        "requiresPlanReview": True,
        "requiresOutputReview": True,
        "riskLevel": "MEDIUM" if use_web_search else "LOW",
        "guardrails": [
            "只通过 Python 本地 Gateway 调用工具",
            "自由探索默认优先使用 web_search_probe；RAG 只作为本地 evidence 补充或联网失败降级",
            "计划审批只确认路线，不授权写操作",
            "输出后若保存草稿或记忆，必须再次进入 CRUD / MEMORY_WRITE 审批",
        ],
    }


def build_completion_criteria(subgraph: str | None, plan: dict[str, Any]) -> list[str]:
    """生成验收标准。"""
    if subgraph == "planning":
        return ["已完成计划内只读工具", "生成 supported/weak/missing 对齐", "输出 evidenceIds 或明确缺证据", "未执行未审批写操作"]
    return ["已完成只读工具", "返回回答或明确失败原因", "保留 evidence 引用结构"]


def build_action_for_step(state: UnifiedAgentState, step: dict[str, Any]) -> dict[str, Any]:
    """根据计划步骤构造工具调用参数。"""
    task_input = state.get("task_input") or {}
    tool_name = text_value(step.get("toolName"))
    if text_value(step.get("toolType")) == "INTERNAL_SUBGRAPH" or tool_name == "resume_rewrite_subgraph":
        return {}
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
            "topK": int_value(task_input.get("topK"), 6 if state.get("subgraph") == "planning" else 5),
            "candidateMultiplier": int_value(task_input.get("candidateMultiplier"), 4),
        }
        metadata_filter = task_input.get("metadataFilter")
        if isinstance(metadata_filter, dict):
            arguments["metadataFilter"] = metadata_filter
        return {"toolName": tool_name, "toolType": "READ", "arguments": arguments}
    return {"toolName": tool_name, "toolType": text_value(step.get("toolType")) or "READ", "arguments": {}}


def build_llm_action_for_step(
    state: UnifiedAgentState,
    step: dict[str, Any],
    fallback_action: dict[str, Any],
    client: AgentGateway | None = None,
) -> dict[str, Any]:
    """调用千问辅助选择下一步只读行动，非法工具或 mutation 自动回退。"""
    if not fallback_action:
        return fallback_action
    prompt = {
        "node": "executor",
        "goal": state.get("user_goal"),
        "step": step,
        "allowedTools": sorted(READ_EXECUTION_TOOLS),
        "forbiddenMutationTools": sorted(MUTATION_TOOLS),
        "observations": summarize_observations(state.get("observations") or []),
        "fallbackAction": fallback_action,
        "expectedJson": {
            "toolName": "allowedTools 中的只读工具，或空字符串表示无需工具",
            "toolType": "READ",
            "arguments": {},
            "reason": "简短中文理由",
        },
    }
    prompt = prepare_llm_payload(state, "executor", prompt, client)
    try:
        result = get_agent_qwen_client().complete_json(
            node="executor",
            model=agent_qwen_model("executor"),
            system_prompt=executor_system_prompt(),
            user_prompt=executor_user_prompt(prompt),
        )
        action = sanitize_action(result.data, fallback_action)
        record_llm_diagnostic(state, "executor", result.model, "used")
        return action
    except Exception as exc:
        record_llm_diagnostic(state, "executor", agent_qwen_model("executor"), f"fallback: {exc}")
        return fallback_action


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
    """从 action 中提取本地 mutation gateway 需要的审批字段。"""
    fields = {}
    for key in ["approvalId", "operationId", "idempotencyKey"]:
        value = text_value(action.get(key))
        if value:
            fields[key] = value
    return fields


def validate_tool_action_for_adapter(tool_name: str, tool_type: str) -> dict[str, str] | None:
    """工具节点兜底校验工具白名单，防止绕过上游 sanitizer。"""
    if tool_type == "MUTATION":
        if tool_name not in MUTATION_TOOLS:
            return {
                "errorCode": "AGENT_TOOL_FORBIDDEN",
                "errorMessage": f"工具节点拒绝未授权变更工具：{tool_name}",
            }
        return None
    if tool_type != "READ":
        return {
            "errorCode": "AGENT_TOOL_FORBIDDEN",
            "errorMessage": f"工具节点拒绝未授权工具类型：{tool_type}",
        }
    if tool_name not in READ_EXECUTION_TOOLS:
        return {
            "errorCode": "AGENT_TOOL_FORBIDDEN",
            "errorMessage": f"工具节点拒绝未授权只读工具：{tool_name}",
        }
    return None


def build_question_for_state(state: UnifiedAgentState) -> str:
    """为 RAG 工具组合当前任务问题。"""
    task_input = state.get("task_input") or {}
    if state.get("subgraph") == "planning":
        return build_evidence_question(
            state.get("user_goal") or "分析 JD 与简历证据差距",
            text_value(task_input.get("jobDescription")),
            text_value(task_input.get("resumeText")),
        )
    return text_value(task_input.get("question")) or text_value(task_input.get("goal")) or "查询学习证据"


def apply_llm_repair_decision(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """调用千问辅助修补决策；权限类错误仍由确定性规则硬停止。"""
    if state.get("status") != "TOOL_FAILED":
        return state
    failure = state.get("failure_reason") or {}
    hard_stop_codes = {
        "AGENT_RESOURCE_FORBIDDEN",
        "AGENT_MEMORY_FORBIDDEN",
        "AGENT_MEMORY_SCOPE_ESCALATION",
        "AGENT_TOOL_FORBIDDEN",
    }
    if text_value(failure.get("errorCode")) in hard_stop_codes:
        return state
    prompt = {
        "node": "repair",
        "goal": state.get("user_goal"),
        "failure": failure,
        "retryCount": int_value(state.get("retry_count"), 0),
        "maxRetries": int_value(state.get("max_retries"), 1),
        "allowedDecisions": ["RETRY", "SKIP_TOOL", "REPLAN", "REPORT_UNABLE"],
        "currentAction": state.get("current_action") or {},
        "expectedJson": {"decision": "RETRY/SKIP_TOOL/REPLAN/REPORT_UNABLE", "reason": "中文原因"},
    }
    prompt = prepare_llm_payload(state, "repair", prompt, client)
    try:
        result = get_agent_qwen_client().complete_json(
            node="repair",
            model=agent_qwen_model("repair"),
            system_prompt=repair_system_prompt(),
            user_prompt=repair_user_prompt(prompt),
        )
        decision = text_value(result.data.get("decision"))
        record_llm_diagnostic(state, "repair", result.model, "used")
        if decision == "SKIP_TOOL":
            return skip_current_tool(state, text_value(result.data.get("reason")) or "LLM 判断该工具可降级跳过")
        if decision == "RETRY" and bool(failure.get("retryable")) and int_value(state.get("retry_count"), 0) < int_value(state.get("max_retries"), 1):
            return {**state, "repair_decision": "RETRY", "retry_count": int_value(state.get("retry_count"), 0) + 1, "status": "RUNNING"}
        if decision == "REPLAN" and text_value(failure.get("errorCode")) == "AGENT_TOOL_UNKNOWN":
            return {**state, "repair_decision": "REPLAN", "plan_approved": False, "status": "RUNNING"}
        if decision == "REPORT_UNABLE":
            return {**state, "repair_decision": "REPORT_UNABLE", "status": "FAILED"}
    except Exception as exc:
        record_llm_diagnostic(state, "repair", agent_qwen_model("repair"), f"fallback: {exc}")
    return state


def apply_llm_acceptance_result(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """调用千问辅助验收；只接受继续、修补或完成三类安全决策。"""
    if state.get("status") not in {"RUNNING", "FAILED"}:
        return state
    steps = list((state.get("plan") or {}).get("steps") or [])
    current_index = int_value(state.get("current_step_index"), 0)
    prompt = {
        "node": "acceptance",
        "goal": state.get("user_goal"),
        "status": state.get("status"),
        "completionCriteria": state.get("completion_criteria") or [],
        "stepCount": len(steps),
        "currentStepIndex": current_index,
        "toolCalls": state.get("tool_calls") or [],
        "observations": summarize_observations(state.get("observations") or []),
        "expectedJson": {
            "decision": "CONTINUE/REPAIR/COMPLETE",
            "complete": False,
            "requiresOutputReview": False,
            "missingRequirements": ["字符串"],
            "reason": "中文原因",
        },
    }
    prompt = prepare_llm_payload(state, "acceptance", prompt, client)
    try:
        result = get_agent_qwen_client().complete_json(
            node="acceptance",
            model=agent_qwen_model("acceptance"),
            system_prompt=acceptance_system_prompt(),
            user_prompt=acceptance_user_prompt(prompt),
        )
        decision = text_value(result.data.get("decision"))
        record_llm_diagnostic(state, "acceptance", result.model, "used")
        if decision == "REPAIR" and state.get("status") == "RUNNING":
            return {**state, "status": "TOOL_FAILED", "repair_decision": "REPLAN"}
        if decision == "CONTINUE" and current_index < len(steps):
            return state
        if decision == "COMPLETE" and current_index >= len(steps):
            return state
    except Exception as exc:
        record_llm_diagnostic(state, "acceptance", agent_qwen_model("acceptance"), f"fallback: {exc}")
    return state


def apply_llm_answer_writer(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """调用千问辅助输出摘要，只允许改写已有 summary/answer，不新增事实。"""
    if state.get("status") not in {"WAITING_OUTPUT_REVIEW", "COMPLETED", "FAILED"}:
        return state
    source = state.get("draft_result") or state.get("final_result") or {}
    prompt = {
        "node": "answer_writer",
        "status": state.get("status"),
        "source": summarize_result(source),
        "evidenceIds": source.get("evidenceIds") if isinstance(source, dict) else [],
        "expectedJson": {"answer": "中文输出摘要", "reviewMessage": "等待审批时的说明", "riskLevel": "LOW/MEDIUM/HIGH"},
    }
    prompt = prepare_llm_payload(state, "answer_writer", prompt, client)
    try:
        result = get_agent_qwen_client().complete_json(
            node="answer_writer",
            model=agent_qwen_model("answer"),
            system_prompt=answer_writer_system_prompt(),
            user_prompt=answer_writer_user_prompt(prompt),
        )
        record_llm_diagnostic(state, "answer_writer", result.model, "used")
        answer = text_value(result.data.get("answer"))
        review_message = text_value(result.data.get("reviewMessage"))
        if state.get("status") == "WAITING_OUTPUT_REVIEW" and review_message and isinstance(state.get("draft_result"), dict):
            draft = dict(state.get("draft_result") or {})
            draft["message"] = review_message
            return {**state, "draft_result": draft}
        if answer and isinstance(state.get("final_result"), dict):
            final = dict(state.get("final_result") or {})
            final["answer"] = answer
            return {**state, "final_result": final}
    except Exception as exc:
        record_llm_diagnostic(state, "answer_writer", agent_qwen_model("answer"), f"fallback: {exc}")
    return state


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
        "diagnostics": {"llm": state.get("llm_diagnostics") or []},
    }


def synthesize_planning_draft(state: UnifiedAgentState, client: AgentGateway) -> dict[str, Any]:
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
    resume_rewrite = build_resume_rewrite_output(state, alignment, gaps, evidence_ids)
    draft = {
        "matchSummary": build_match_summary(alignment, evidence_ids),
        "alignment": alignment,
        "gaps": gaps,
        "evidenceIds": evidence_ids,
        "memoryContext": merge_memory_contexts(state),
        "webReferences": web_references_from_state(state),
        "resumeRewrite": resume_rewrite,
        "resumeTemplateFill": resume_template_fill,
        "answer": text_value(rag_data.get("answer")),
        "expandedQueries": rag_data.get("expandedQueries") if isinstance(rag_data.get("expandedQueries"), list) else [],
        "riskLevel": risk_level,
        "diagnostics": {"llm": state.get("llm_diagnostics") or []},
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


def detect_resume_rewrite_intent(task_input: dict[str, Any]) -> bool:
    """检测用户目标或工具意图中是否包含简历修改需求。"""
    tool_hints = task_input.get("toolHints")
    if isinstance(tool_hints, list) and any(str(item) in {"resume_rewrite_subgraph", "resume_template_fill", "resume_revision_save"} for item in tool_hints):
        return True
    if bool(task_input.get("resumeRewriteRequested")):
        return True
    goal = text_value(task_input.get("goal"))
    keywords = ["修改简历", "优化简历", "改简历", "简历改写", "简历润色", "生成简历", "投递简历"]
    return any(keyword in goal for keyword in keywords)


def should_enter_resume_rewrite_subgraph(state: UnifiedAgentState) -> bool:
    """按 Planner 输出和任务输入决定是否进入简历修改子图。"""
    plan = state.get("plan") or {}
    if bool(plan.get("resumeRewriteIntent")):
        return True
    task_input = state.get("task_input") or {}
    return detect_resume_rewrite_intent(task_input)


def build_resume_rewrite_patches(content_map: dict[str, str], gaps: list[dict[str, str]]) -> list[dict[str, Any]]:
    """把简历内容候选转换成前端可展示的修改片段。"""
    labels = {
        "summary": "个人摘要",
        "skills": "技能关键词",
        "project_experience": "项目经历",
        "learning_plan": "补强建议",
        "gap_summary": "差距摘要",
    }
    patches = []
    for field, value in content_map.items():
        patches.append(
            {
                "field": field,
                "label": labels.get(field, field),
                "suggestedText": value,
                "reason": "根据 JD 要求、简历摘要和当前 evidence 自动生成，需用户确认后才可保存。",
                "status": "PENDING_REVIEW",
            }
        )
    if gaps:
        patches.append(
            {
                "field": "priority_gaps",
                "label": "优先补强项",
                "suggestedText": "；".join(item["suggestion"] for item in gaps[:3]),
                "reason": "这些要求在当前简历或 evidence 中支撑较弱。",
                "status": "PENDING_REVIEW",
            }
        )
    return patches


def build_llm_resume_rewrite_plan(
    state: UnifiedAgentState,
    fallback_plan: dict[str, Any],
    client: AgentGateway | None = None,
) -> dict[str, Any]:
    """调用千问辅助生成简历改写局部计划。"""
    task_input = state.get("task_input") or {}
    prompt = {
        "node": "resume_rewrite_planner",
        "goal": state.get("user_goal"),
        "jobDescription": truncate_text(text_value(task_input.get("jobDescription")), 900),
        "resumeText": truncate_text(text_value(task_input.get("resumeText")), 700),
        "fallbackPlan": fallback_plan,
        "expectedJson": {
            "title": "简历修改子图计划",
            "scope": "PENDING_REVIEW_RESUME_DRAFT",
            "steps": [{"name": "字符串", "description": "字符串"}],
            "targetRequirements": ["字符串"],
            "hasResumeText": True,
            "guardrails": ["不直接写 DOCX", "不直接保存业务数据", "候选片段必须进入 OUTPUT 审批"],
        },
    }
    prompt = prepare_llm_payload(state, "resume_rewrite_planner", prompt, client)
    try:
        result = get_agent_qwen_client().complete_json(
            node="resume_rewrite_planner",
            model=agent_qwen_model("resume"),
            system_prompt=resume_rewrite_planner_system_prompt(),
            user_prompt=resume_rewrite_planner_user_prompt(prompt),
        )
        plan = sanitize_resume_rewrite_plan(result.data, fallback_plan)
        record_llm_diagnostic(state, "resume_rewrite_planner", result.model, "used")
        return plan
    except Exception as exc:
        record_llm_diagnostic(state, "resume_rewrite_planner", agent_qwen_model("resume"), f"fallback: {exc}")
        return fallback_plan


def build_llm_resume_rewrite_draft(
    state: UnifiedAgentState,
    fallback_draft: dict[str, Any],
    client: AgentGateway | None = None,
) -> dict[str, Any]:
    """调用千问辅助生成简历改写候选，仍保持 PENDING_REVIEW。"""
    task_input = state.get("task_input") or {}
    prompt = {
        "node": "resume_rewrite_generator",
        "goal": state.get("user_goal"),
        "jobDescription": truncate_text(text_value(task_input.get("jobDescription")), 900),
        "resumeText": truncate_text(text_value(task_input.get("resumeText")), 700),
        "rewritePlan": state.get("resume_rewrite_plan") or {},
        "fallbackDraft": fallback_draft,
        "expectedJson": {
            "contentMap": {
                "summary": "个人摘要候选",
                "skills": "技能关键词候选",
                "project_experience": "项目经历候选",
                "learning_plan": "补强建议候选",
                "gap_summary": "差距摘要候选",
            },
            "rewriteTargets": ["字符串"],
            "message": "中文说明",
        },
    }
    prompt = prepare_llm_payload(state, "resume_rewrite_generator", prompt, client)
    try:
        result = get_agent_qwen_client().complete_json(
            node="resume_rewrite_generator",
            model=agent_qwen_model("resume"),
            system_prompt=resume_rewrite_generator_system_prompt(),
            user_prompt=resume_rewrite_generator_user_prompt(prompt),
        )
        draft = sanitize_resume_rewrite_draft(result.data, fallback_draft)
        record_llm_diagnostic(state, "resume_rewrite_generator", result.model, "used")
        return draft
    except Exception as exc:
        record_llm_diagnostic(state, "resume_rewrite_generator", agent_qwen_model("resume"), f"fallback: {exc}")
        return fallback_draft


def build_llm_resume_rewrite_acceptance(
    state: UnifiedAgentState,
    fallback_result: dict[str, Any],
    client: AgentGateway | None = None,
) -> dict[str, Any]:
    """调用千问辅助验收简历候选，只接受结构化布尔结果。"""
    prompt = {
        "node": "resume_rewrite_acceptance",
        "draftSummary": summarize_result(state.get("resume_rewrite_draft") or {}),
        "expectedJson": {"accepted": True, "requiresOutputReview": True, "reason": "中文原因"},
    }
    prompt = prepare_llm_payload(state, "resume_rewrite_acceptance", prompt, client)
    try:
        result = get_agent_qwen_client().complete_json(
            node="resume_rewrite_acceptance",
            model=agent_qwen_model("acceptance"),
            system_prompt=resume_rewrite_acceptance_system_prompt(),
            user_prompt=resume_rewrite_acceptance_user_prompt(prompt),
        )
        if isinstance(result.data.get("accepted"), bool):
            record_llm_diagnostic(state, "resume_rewrite_acceptance", result.model, "used")
            return {
                **fallback_result,
                "accepted": bool(result.data.get("accepted")),
                "requiresOutputReview": True,
                "reason": text_value(result.data.get("reason")),
            }
    except Exception as exc:
        record_llm_diagnostic(state, "resume_rewrite_acceptance", agent_qwen_model("acceptance"), f"fallback: {exc}")
    return fallback_result


def build_resume_rewrite_output(
    state: UnifiedAgentState,
    alignment: list[dict[str, Any]],
    gaps: list[dict[str, str]],
    evidence_ids: list[str],
) -> dict[str, Any]:
    """合并简历修改子图结果和工具 evidence，形成最终可审批输出。"""
    draft = state.get("resume_rewrite_draft") or {}
    if not draft:
        return {}
    content_map = build_resume_content_map(state.get("task_input") or {}, alignment, gaps, evidence_ids)
    merged = dict(draft)
    merged["contentMap"] = {**content_map, **(draft.get("contentMap") if isinstance(draft.get("contentMap"), dict) else {})}
    merged["evidenceIds"] = evidence_ids
    merged["subgraphPlan"] = state.get("resume_rewrite_plan") or {}
    merged["subgraphResult"] = state.get("resume_rewrite_result") or {}
    return merged


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
        "message": "已生成简历模板填充值候选；Agent 不直接写 DOCX，需用户确认后由受控模板导出链路执行。",
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
    client: AgentGateway,
) -> list[dict[str, Any]]:
    """请求 Python 本地 Gateway 生成记忆候选，不直接保存或激活。"""
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
    client: AgentGateway,
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


def skip_current_step_without_action(state: UnifiedAgentState, reason: str) -> UnifiedAgentState:
    """执行器未选择工具时推进当前步骤，避免空 action 造成图循环。"""
    steps = list((state.get("plan") or {}).get("steps") or [])
    current_index = int_value(state.get("current_step_index"), 0)
    step = steps[current_index] if current_index < len(steps) and isinstance(steps[current_index], dict) else {}
    skipped = {
        "toolName": text_value(step.get("toolName")) or "NO_ACTION",
        "status": "SKIPPED",
        "reason": reason,
        "stepIndex": current_index,
    }
    return {
        **state,
        "status": "RUNNING",
        "current_step_index": current_index + 1,
        "current_action": {},
        "observations": list(state.get("observations") or []) + [skipped],
        "verifier_result": {"complete": False, "skippedStep": True, "reason": reason},
    }


def should_skip_empty_executor_action(state: UnifiedAgentState, current_index: int) -> bool:
    """只跳过刚由 executor 生成的空 action，修补节点推进后的空 action 继续执行下一步。"""
    if state.get("repair_decision") == "SKIP_TOOL":
        return False
    trace = list(state.get("react_trace") or [])
    if not trace:
        return False
    latest = trace[-1] if isinstance(trace[-1], dict) else {}
    action = latest.get("action") if isinstance(latest.get("action"), dict) else {}
    return int_value(action.get("stepIndex"), -1) == current_index and not text_value(action.get("toolName"))


def web_search_enabled(task_input: dict[str, Any]) -> bool:
    """判断是否启用联网参考。"""
    if text_value(task_input.get("workspaceMode")) == "free_explore":
        return True
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
            "summary": "该操作属于数据库变更，需用户确认后由 Python 本地 Gateway 校验并执行。",
        },
    }


def sanitize_plan(candidate: dict[str, Any], state: UnifiedAgentState, fallback_plan: dict[str, Any]) -> dict[str, Any]:
    """校验 LLM 计划，非法工具、子图或审批边界不合格则回退。"""
    steps_raw = candidate.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ValueError("计划缺少 steps")
    steps: list[dict[str, Any]] = []
    for item in steps_raw[:6]:
        if not isinstance(item, dict):
            continue
        tool_name = text_value(item.get("toolName"))
        tool_type = text_value(item.get("toolType")) or "READ"
        if tool_name not in PLAN_ALLOWED_TOOLS:
            raise ValueError(f"计划包含未授权工具 {tool_name}")
        if tool_name in MUTATION_TOOLS or tool_type == "MUTATION":
            raise ValueError("PLAN 审批不能授权写操作")
        if tool_type == "INTERNAL_SUBGRAPH" and tool_name not in ALLOWED_INTERNAL_SUBGRAPHS:
            raise ValueError("计划包含未授权内部子图")
        steps.append(
            {
                "description": text_value(item.get("description")) or text_value(item.get("name")) or "执行计划步骤",
                "toolName": tool_name,
                "toolType": "INTERNAL_SUBGRAPH" if tool_name in ALLOWED_INTERNAL_SUBGRAPHS else "READ",
                "expectedOutput": text_value(item.get("expectedOutput")) or "结构化观察结果",
            }
        )
    if not steps:
        raise ValueError("计划没有可执行步骤")
    internal_subgraphs = [str(item) for item in candidate.get("internalSubgraphs") or [] if str(item) in ALLOWED_INTERNAL_SUBGRAPHS]
    if candidate.get("resumeRewriteIntent") and "resume_rewrite_subgraph" not in internal_subgraphs:
        internal_subgraphs.append("resume_rewrite_subgraph")
    task_input = state.get("task_input") or {}
    if detect_resume_rewrite_intent(task_input) and "resume_rewrite_subgraph" not in internal_subgraphs:
        internal_subgraphs.append("resume_rewrite_subgraph")
    sanitized_steps = [step for step in steps if step["toolName"] != "resume_rewrite_subgraph"]
    if web_search_enabled(task_input):
        fallback_steps = [step for step in fallback_plan.get("steps", []) if isinstance(step, dict)]
        web_step = next((step for step in fallback_steps if step.get("toolName") == "web_search_probe"), None)
        rag_step = next((step for step in fallback_steps if step.get("toolName") == "rag_query_probe_non_persistent"), None)
        if web_step and not any(step["toolName"] == "web_search_probe" for step in sanitized_steps):
            sanitized_steps = [web_step, *sanitized_steps]
        if rag_step and not any(step["toolName"] == "rag_query_probe_non_persistent" for step in sanitized_steps):
            sanitized_steps = [*sanitized_steps, rag_step]
        web_steps = [step for step in sanitized_steps if step["toolName"] == "web_search_probe"]
        rag_steps = [step for step in sanitized_steps if step["toolName"] == "rag_query_probe_non_persistent"]
        other_steps = [step for step in sanitized_steps if step["toolName"] not in {"web_search_probe", "rag_query_probe_non_persistent"}]
        if web_steps:
            sanitized_steps = [web_steps[0], *(rag_steps[:1]), *other_steps]
    plan = {
        **fallback_plan,
        "title": text_value(candidate.get("title")) or fallback_plan.get("title"),
        "steps": sanitized_steps or fallback_plan.get("steps", []),
        "tools": sorted({step["toolName"] for step in sanitized_steps}),
        "internalSubgraphs": internal_subgraphs,
        "resumeRewriteIntent": bool(candidate.get("resumeRewriteIntent")) or bool(internal_subgraphs),
        "requiresPlanReview": bool(fallback_plan.get("requiresPlanReview")),
        "requiresOutputReview": bool(fallback_plan.get("requiresOutputReview")),
        "riskLevel": sanitize_risk_level(candidate.get("riskLevel"), fallback_plan.get("riskLevel")),
        "guardrails": sanitize_string_list(candidate.get("guardrails")) or fallback_plan.get("guardrails", []),
    }
    if state.get("subgraph") != "planning":
        plan["requiresPlanReview"] = False
        plan["requiresOutputReview"] = False
        plan["resumeRewriteIntent"] = False
        plan["internalSubgraphs"] = []
    return plan


def sanitize_action(candidate: dict[str, Any], fallback_action: dict[str, Any]) -> dict[str, Any]:
    """校验 LLM action，只允许只读工具。"""
    tool_name = text_value(candidate.get("toolName"))
    if not tool_name:
        return {}
    tool_type = text_value(candidate.get("toolType")) or "READ"
    if tool_name not in READ_EXECUTION_TOOLS:
        raise ValueError(f"执行器选择未授权工具 {tool_name}")
    if tool_name in MUTATION_TOOLS or tool_type == "MUTATION":
        raise ValueError("执行器不允许选择 mutation 工具")
    arguments = candidate.get("arguments") if isinstance(candidate.get("arguments"), dict) else fallback_action.get("arguments") or {}
    return {"toolName": tool_name, "toolType": "READ", "arguments": arguments}


def sanitize_resume_rewrite_plan(candidate: dict[str, Any], fallback_plan: dict[str, Any]) -> dict[str, Any]:
    """校验简历改写计划，防止模型输出文件路径或保存动作。"""
    forbidden_keys = {"locationRefs", "docxPath", "xml", "styleXml", "savePath"}
    if forbidden_keys.intersection(candidate.keys()):
        raise ValueError("简历改写计划包含禁止字段")
    plan = dict(fallback_plan)
    plan["title"] = text_value(candidate.get("title")) or plan.get("title")
    plan["scope"] = "PENDING_REVIEW_RESUME_DRAFT"
    requirements = sanitize_string_list(candidate.get("targetRequirements"))
    if requirements:
        plan["targetRequirements"] = requirements[:8]
    steps = candidate.get("steps")
    if isinstance(steps, list) and steps:
        plan["steps"] = [
            {"name": text_value(item.get("name")) or f"步骤 {index}", "description": text_value(item.get("description")) or "生成待确认候选"}
            for index, item in enumerate(steps[:5], start=1)
            if isinstance(item, dict)
        ] or plan.get("steps", [])
    plan["guardrails"] = ["不直接写 DOCX", "不直接保存业务数据", "候选片段必须进入 OUTPUT 审批"]
    return plan


def sanitize_resume_rewrite_draft(candidate: dict[str, Any], fallback_draft: dict[str, Any]) -> dict[str, Any]:
    """校验简历改写候选，固定为待审批草稿。"""
    content_map = candidate.get("contentMap")
    if not isinstance(content_map, dict):
        raise ValueError("简历候选缺少 contentMap")
    allowed_fields = {"summary", "skills", "project_experience", "learning_plan", "gap_summary"}
    cleaned = {key: text_value(value) for key, value in content_map.items() if key in allowed_fields and text_value(value)}
    if not cleaned:
        raise ValueError("简历候选 contentMap 为空")
    draft = dict(fallback_draft)
    draft["status"] = "PENDING_REVIEW"
    draft["toolName"] = "resume_rewrite_subgraph"
    draft["requiresApproval"] = True
    draft["approvalType"] = "OUTPUT"
    draft["message"] = text_value(candidate.get("message")) or fallback_draft.get("message")
    draft["contentMap"] = {**(fallback_draft.get("contentMap") if isinstance(fallback_draft.get("contentMap"), dict) else {}), **cleaned}
    targets = sanitize_string_list(candidate.get("rewriteTargets"))
    if targets:
        draft["rewriteTargets"] = targets[:8]
    draft["patches"] = build_resume_rewrite_patches(draft["contentMap"], [])
    return draft


def sanitize_risk_level(value: Any, fallback: Any) -> str:
    """读取风险等级。"""
    risk = text_value(value).upper()
    if risk in {"LOW", "MEDIUM", "HIGH"}:
        return risk
    return text_value(fallback).upper() if text_value(fallback).upper() in {"LOW", "MEDIUM", "HIGH"} else "MEDIUM"


def sanitize_string_list(value: Any) -> list[str]:
    """清洗模型返回的字符串列表。"""
    if not isinstance(value, list):
        return []
    return [text_value(item) for item in value if text_value(item)]


def summarize_task_input(task_input: dict[str, Any]) -> dict[str, Any]:
    """构造不含敏感长正文的任务输入摘要。"""
    return {
        "goal": truncate_text(text_value(task_input.get("goal") or task_input.get("question")), 240),
        "workspaceMode": text_value(task_input.get("workspaceMode")),
        "hasJobDescription": bool(text_value(task_input.get("jobDescription"))),
        "hasResumeText": bool(text_value(task_input.get("resumeText"))),
        "toolHints": task_input.get("toolHints") if isinstance(task_input.get("toolHints"), list) else [],
        "enableWebSearch": bool(task_input.get("enableWebSearch")),
        "saveDraft": bool(task_input.get("saveDraft")),
    }


def summarize_observations(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """压缩工具观察，避免把正文送入修补和验收提示。"""
    compact = []
    for item in observations[-5:]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                "toolName": item.get("toolName"),
                "status": item.get("status"),
                "evidenceCount": item.get("evidenceCount"),
                "errorCode": item.get("errorCode"),
                "diagnosticKeys": item.get("diagnosticKeys"),
            }
        )
    return compact


def summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    """压缩草稿或最终输出，供 answer_writer 使用。"""
    return {
        "matchSummary": truncate_text(text_value(result.get("matchSummary")), 300),
        "answer": truncate_text(text_value(result.get("answer")), 300),
        "evidenceIds": result.get("evidenceIds") if isinstance(result.get("evidenceIds"), list) else [],
        "riskLevel": result.get("riskLevel"),
        "hasResumeRewrite": bool(result.get("resumeRewrite")),
        "alignmentCount": len(result.get("alignment")) if isinstance(result.get("alignment"), list) else 0,
        "gapCount": len(result.get("gaps")) if isinstance(result.get("gaps"), list) else 0,
    }


def truncate_text(value: str, limit: int) -> str:
    """截断提示词中的长文本。"""
    return value[:limit] if len(value) > limit else value


def json_prompt(payload: dict[str, Any]) -> str:
    """把提示词输入序列化为中文 JSON。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


def fallback_conversation_title(goal: str) -> str:
    """在 LLM 不可用时用首句生成可读标题。"""
    first_line = " ".join((goal or "Agent 会话").strip().split())
    if not first_line:
        return "Agent 会话"
    for delimiter in ["。", "？", "?", "！", "!", "\n"]:
        if delimiter in first_line:
            first_line = first_line.split(delimiter)[0]
            break
    return truncate_text(first_line, 24) or "Agent 会话"


def sanitize_conversation_title(title: str, fallback: str) -> str:
    """清洗模型生成的会话标题，避免标点、换行和过长文本进入侧边栏。"""
    normalized = " ".join((title or "").strip().split())
    normalized = normalized.strip(" \t\r\n，。！？；：、,.!?;:\"'“”‘’《》【】[]()（）")
    if not normalized:
        return fallback
    return truncate_text(normalized, 24)


def record_llm_diagnostic(state: UnifiedAgentState, node: str, model: str, status: str) -> None:
    """记录非敏感 LLM 诊断信息到状态对象。"""
    diagnostics = state.setdefault("llm_diagnostics", [])
    diagnostics.append({"node": node, "provider": "dashscope", "model": model, "status": status})


def conversation_title_system_prompt() -> str:
    """Conversation Title 节点专用提示词。"""
    return (
        "你是学迹智配 Agent 的会话主题命名节点。你的唯一任务是根据用户第一句话生成一个中文短标题。"
        "标题必须概括用户目标，8 到 20 个中文字符为宜，不要使用标点、引号、换行或表情。"
        "不得编造用户没有提到的公司、岗位、技术或结论。只输出合法 JSON。"
    )


def conversation_title_user_prompt(payload: dict[str, Any]) -> str:
    """Conversation Title 节点用户提示词。"""
    expected = {"conversationTitle": "8到20字中文主题标题"}
    return "请根据以下用户目标生成会话标题 JSON，不要输出解释文字：\n" + json_prompt({**payload, "expectedJson": expected})


def planner_system_prompt() -> str:
    """Planner 节点专用提示词。"""
    return (
        "你是学迹智配 Agent 的 LangGraph 规划节点。你只生成可审批计划，不执行工具、不保存数据、不生成最终答案。"
        "所有工具必须通过 Python 本地 Gateway。PLAN 审批只确认路线，不授权写操作。任何保存、修改、删除、写入记忆或导出文件"
        "都必须在 OUTPUT 审批后再进入 CRUD 审批。只允许从 allowedTools 和 allowedSubgraphs 选择。"
        "若 taskInputSummary.workspaceMode=free_explore，必须把 web_search_probe 作为第一步，把 rag_query_probe_non_persistent 作为第二步补充或降级路径。"
        "若目标涉及优化简历、修改简历、生成投递简历、简历改写，必须设置 resumeRewriteIntent=true 并加入 internalSubgraphs=[\"resume_rewrite_subgraph\"]。"
        "只输出合法 JSON。"
    )


def planner_user_prompt(payload: dict[str, Any]) -> str:
    """Planner 节点用户提示词。"""
    return "请根据以下任务上下文生成可审批计划 JSON，不要输出解释文字：\n" + json_prompt(payload)


def executor_system_prompt() -> str:
    """Executor 节点专用提示词。"""
    return (
        "你是学迹智配 Agent 的 ReAct 执行节点。你只能根据已批准计划选择下一步只读工具或判断无需工具。"
        "不能发明工具名，不能选择 mutation 工具，不能绕过 Python 本地 Gateway。只输出 JSON action。"
    )


def executor_user_prompt(payload: dict[str, Any]) -> str:
    """Executor 节点用户提示词。"""
    return "请根据当前计划步骤和工具观察选择下一步只读 action JSON；如无需工具，toolName 置空：\n" + json_prompt(payload)


def repair_system_prompt() -> str:
    """Repair 节点专用提示词。"""
    return (
        "你是学迹智配 Agent 的修补节点。你根据工具错误码、retryable、重试次数和任务目标决定 RETRY、SKIP_TOOL、REPLAN 或 REPORT_UNABLE。"
        "权限、内部令牌、跨用户资源错误必须硬停止。web_search_probe 不可用时优先降级到本地 RAG。只输出 JSON。"
    )


def repair_user_prompt(payload: dict[str, Any]) -> str:
    """Repair 节点用户提示词。"""
    return "请根据失败摘要输出修补决策 JSON，只能使用 allowedDecisions 中的值：\n" + json_prompt(payload)


def acceptance_system_prompt() -> str:
    """Acceptance 节点专用提示词。"""
    return (
        "你是学迹智配 Agent 的验收节点。你检查计划步骤、工具观察、completion criteria、evidenceIds、riskLevel 和审批要求，"
        "判断继续执行、修补、输出审批或完成。不能虚构 evidence。只输出 JSON。"
    )


def acceptance_user_prompt(payload: dict[str, Any]) -> str:
    """Acceptance 节点用户提示词。"""
    return "请检查任务是否满足完成标准并输出验收 JSON，不得新增 evidence：\n" + json_prompt(payload)


def resume_rewrite_planner_system_prompt() -> str:
    """简历修改规划节点专用提示词。"""
    return (
        "你是简历修改子图的规划节点。你根据 JD、简历摘要、用户目标和证据状态确定简历改写范围。"
        "你不能写 DOCX、不能输出 XML/样式/路径/locationRefs、不能保存数据。只生成待确认候选的计划。只输出 JSON。"
    )


def resume_rewrite_planner_user_prompt(payload: dict[str, Any]) -> str:
    """简历修改规划节点用户提示词。"""
    return "请基于 JD、简历摘要和目标生成简历改写局部计划 JSON：\n" + json_prompt(payload)


def resume_rewrite_generator_system_prompt() -> str:
    """简历修改候选生成节点专用提示词。"""
    return (
        "你是简历修改子图的候选生成节点。你生成个人摘要、技能关键词、项目经历、补强建议和差距摘要候选。"
        "所有内容必须基于简历摘要、JD 和 evidence 状态；证据不足时明确标记缺口。不能写 DOCX，不能保存数据。只输出 JSON。"
    )


def resume_rewrite_generator_user_prompt(payload: dict[str, Any]) -> str:
    """简历修改候选生成节点用户提示词。"""
    return "请生成待审批的简历候选 JSON，只能填写 contentMap 和 rewriteTargets 等允许字段：\n" + json_prompt(payload)


def resume_rewrite_acceptance_system_prompt() -> str:
    """简历修改验收节点专用提示词。"""
    return (
        "你是简历修改子图的验收节点。你只检查候选是否包含可审批的个人摘要、技能、项目经历、补强建议或差距摘要。"
        "你不能批准保存、不能写 DOCX、不能新增 evidence。只输出 JSON。"
    )


def resume_rewrite_acceptance_user_prompt(payload: dict[str, Any]) -> str:
    """简历修改验收节点用户提示词。"""
    return "请检查简历候选是否可进入 OUTPUT 审批，并输出验收 JSON：\n" + json_prompt(payload)


def answer_writer_system_prompt() -> str:
    """回答节点专用提示词。"""
    return (
        "你是学迹智配 Agent 的输出节点。你根据已验证 draft/final 和审批状态生成中文输出摘要。"
        "必须保留 evidence 引用，不得新增事实。等待审批时只生成审批说明，不伪装任务完成。只输出 JSON。"
    )


def answer_writer_user_prompt(payload: dict[str, Any]) -> str:
    """回答节点用户提示词。"""
    return "请基于已验证结果生成中文输出 JSON；等待审批时只写审批说明：\n" + json_prompt(payload)
