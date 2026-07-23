from __future__ import annotations

import json
import os
import re
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
RESUME_REWRITE_CONTENT_FIELDS = {"summary", "skills", "project_experience", "learning_plan", "gap_summary"}
RESUME_PROJECT_EVIDENCE_KEYWORDS = ("项目", "系统", "平台", "开发", "实现", "构建", "作品", "工程", "服务")
RESUME_COURSEWORK_EVIDENCE_KEYWORDS = ("课程", "练习", "作业", "实验", "基础", "教程", "笔记", "学习", "课堂")


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
    resume_jd_profile: dict[str, Any]
    resume_evidence_bundle: dict[str, Any]
    resume_revision_advice: dict[str, Any]
    resume_patch_candidate: dict[str, Any]
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
    workflow.add_node("resume_jd_analyzer", lambda state: resume_jd_analyzer_node(state, client))
    workflow.add_node("resume_evidence_retriever", lambda state: resume_evidence_retriever_node(state, client))
    workflow.add_node("resume_evidence_summarizer", lambda state: resume_evidence_summarizer_node(state, client))
    workflow.add_node("resume_revision_advisor", lambda state: resume_revision_advisor_node(state, client))
    workflow.add_node("resume_patch_builder", resume_patch_builder_node)
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
            "resume_jd_analyzer": "resume_jd_analyzer",
            "memory_prefetch_after_planner": "memory_prefetch_after_planner",
        },
    )
    workflow.add_edge("resume_jd_analyzer", "resume_evidence_retriever")
    workflow.add_edge("resume_evidence_retriever", "resume_evidence_summarizer")
    workflow.add_edge("resume_evidence_summarizer", "resume_revision_advisor")
    workflow.add_edge("resume_revision_advisor", "resume_patch_builder")
    workflow.add_edge("resume_patch_builder", "resume_rewrite_acceptance")
    workflow.add_conditional_edges(
        "resume_rewrite_acceptance",
        route_after_resume_rewrite_acceptance,
        {
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


def resume_jd_analyzer_node(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """第一个简历子 Agent：将原始 JD 约束为可追踪的岗位要求画像。"""
    if state.get("status") == "FAILED" or not state.get("resume_rewrite_required"):
        return state
    task_input = state.get("task_input") or {}
    jd_text = text_value(task_input.get("jobDescription"))
    resume_text = text_value(task_input.get("resumeText"))
    if not jd_text:
        return {
            **state,
            "status": "FAILED",
            "error_code": "AGENT_RESUME_JD_REQUIRED",
            "error_message": "简历优化任务缺少岗位 JD，无法生成可核验的修改建议。",
        }
    if not resume_text:
        return {
            **state,
            "status": "FAILED",
            "error_code": "AGENT_RESUME_TEXT_REQUIRED",
            "error_message": "简历优化任务缺少原始简历内容，无法定位可修改字段。",
        }
    publish_progress_event(
        client,
        state,
        node="resume_jd_analyzer",
        phase="started",
        message="JD 分析子 Agent 正在提取岗位硬性要求、加分项和关键词。",
    )
    fallback_profile = build_resume_jd_profile(jd_text, task_input)
    profile = build_llm_resume_jd_profile(state, fallback_profile, client)
    publish_progress_event(
        client,
        state,
        node="resume_jd_analyzer",
        phase="finished",
        message=f"JD 分析完成，已整理 {len(resume_jd_requirements(profile))} 项可检索岗位要求。",
        extra={"jobTitle": profile.get("jobTitle"), "requirementCount": len(resume_jd_requirements(profile))},
    )
    return {
        **state,
        "resume_jd_profile": profile,
        "resume_rewrite_plan": {
            "title": "简历证据改写计划",
            "scope": "PENDING_REVIEW_RESUME_DRAFT",
            "targetRequirements": [item["requirement"] for item in resume_jd_requirements(profile)],
            "guardrails": ["不直接写 DOCX", "不直接保存业务数据", "候选片段必须进入 OUTPUT 审批"],
        },
    }


def resume_evidence_retriever_node(state: UnifiedAgentState, client: AgentGateway) -> UnifiedAgentState:
    """第二个简历子 Agent：仅通过本地 Gateway 检索当前用户的学习 evidence。"""
    if state.get("status") == "FAILED" or not state.get("resume_rewrite_required"):
        return state
    profile = state.get("resume_jd_profile") or {}
    task_input = state.get("task_input") or {}
    question = build_resume_evidence_question(profile, text_value(state.get("user_goal")))
    publish_progress_event(
        client,
        state,
        node="resume_evidence_retriever",
        phase="started",
        message="学习证据子 Agent 正在用岗位要求检索当前用户的私有资料。",
        status="WAITING_TOOL_RESULT",
    )
    retrieved = tool_adapter_node(
        {
            **state,
            "current_action": {
                "toolName": "rag_query_probe_non_persistent",
                "toolType": "READ",
                "arguments": {
                    "question": question,
                    "topK": int_value(task_input.get("topK"), 6),
                    "candidateMultiplier": int_value(task_input.get("candidateMultiplier"), 4),
                    "metadataFilter": task_input.get("metadataFilter") if isinstance(task_input.get("metadataFilter"), dict) else {},
                },
            },
        },
        client,
    )
    if retrieved.get("status") == "TOOL_FAILED":
        return {
            **retrieved,
            "status": "FAILED",
            "error_code": retrieved.get("error_code") or "AGENT_RESUME_EVIDENCE_RETRIEVAL_FAILED",
            "error_message": retrieved.get("error_message") or "学习证据检索失败，无法生成可核验的简历修改建议。",
        }
    rag_result = latest_tool_result_by_name(retrieved, "rag_query_probe_non_persistent")
    rag_data = rag_result.get("data") if isinstance(rag_result.get("data"), dict) else {}
    bundle = build_resume_evidence_bundle(profile, question, rag_data)
    publish_progress_event(
        client,
        retrieved,
        node="resume_evidence_retriever",
        phase="finished",
        message=f"已检索到 {len(bundle.get('items') or [])} 条候选学习证据，正在进行可引用摘要。",
        extra={"evidenceCount": len(bundle.get("items") or []), "expandedQueries": bundle.get("expandedQueries") or []},
    )
    return {**retrieved, "resume_evidence_bundle": bundle, "current_action": {}}


def resume_evidence_summarizer_node(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """第三个简历子 Agent 的前半段：在保留 evidence 原文引用的前提下归纳可支持事实。"""
    if state.get("status") == "FAILED" or not state.get("resume_rewrite_required"):
        return state
    profile = state.get("resume_jd_profile") or {}
    bundle = state.get("resume_evidence_bundle") or {}
    publish_progress_event(
        client,
        state,
        node="resume_evidence_summarizer",
        phase="started",
        message="学习证据子 Agent 正在归纳证据覆盖范围，并保留 evidence 引用。",
    )
    fallback = build_resume_evidence_summary(profile, bundle)
    summary = build_llm_resume_evidence_summary(state, fallback, client)
    merged_bundle = {**bundle, **summary, "items": list(bundle.get("items") or []), "evidenceIds": list(bundle.get("evidenceIds") or [])}
    publish_progress_event(
        client,
        state,
        node="resume_evidence_summarizer",
        phase="finished",
        message="学习证据摘要完成；后续修改建议只能引用本次候选 evidence。",
        extra={"evidenceCount": len(merged_bundle.get("evidenceIds") or [])},
    )
    return {**state, "resume_evidence_bundle": merged_bundle}


def resume_revision_advisor_node(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """第三个简历子 Agent：根据 JD、原简历和 evidence 生成可微调的字段级修改建议。"""
    if state.get("status") == "FAILED" or not state.get("resume_rewrite_required"):
        return state
    task_input = state.get("task_input") or {}
    profile = state.get("resume_jd_profile") or {}
    bundle = state.get("resume_evidence_bundle") or {}
    alignment = build_resume_evidence_alignment(profile, text_value(task_input.get("resumeText")), bundle)
    gaps = build_gaps(alignment)
    supported_evidence_ids = list(
        dict.fromkeys(
            evidence_id
            for item in alignment
            if item.get("status") == "supported"
            for evidence_id in item.get("evidenceIds") or []
            if text_value(evidence_id)
        )
    )
    evidence_bundle = {**bundle, "supportedEvidenceIds": supported_evidence_ids}
    evidence_bundle["projectEvidenceIds"] = project_resume_evidence_ids(evidence_bundle, supported_evidence_ids)
    content_map = build_resume_content_map(task_input, alignment, gaps, supported_evidence_ids)
    supported_requirements = [text_value(item.get("requirement")) for item in alignment if item.get("status") == "supported"]
    # 弱匹配只用于缺口分析，不能被带有 NONE 风险标记的技能补丁写成已具备能力。
    content_map["skills"] = " / ".join(supported_requirements[:8]) or "待补充岗位相关技能"
    fallback_advice = {
        "contentMap": content_map,
        "rewriteTargets": [item["requirement"] for item in resume_jd_requirements(profile)],
        "patches": build_resume_revision_patches(content_map, supported_evidence_ids),
        "gapSuggestions": build_gap_suggestions(gaps),
        "message": "已基于岗位要求和可引用学习证据生成字段级修改建议，等待用户确认。",
    }
    publish_progress_event(
        client,
        state,
        node="resume_revision_advisor",
        phase="started",
        message="简历修改建议子 Agent 正在把岗位要求和学习证据映射到原简历字段。",
    )
    advice = build_llm_resume_revision_advice({**state, "resume_evidence_bundle": evidence_bundle}, fallback_advice, client)
    publish_progress_event(
        client,
        state,
        node="resume_revision_advisor",
        phase="finished",
        message=f"已生成 {len(advice.get('patches') or [])} 条待确认字段级修改建议。",
        extra={"patchCount": len(advice.get("patches") or []), "evidenceCount": len(supported_evidence_ids)},
    )
    return {**state, "resume_evidence_bundle": evidence_bundle, "resume_revision_advice": advice}


def resume_patch_builder_node(state: UnifiedAgentState) -> UnifiedAgentState:
    """最终补丁准备节点保持确定性，只整理建议，不让模型直接改写 DOCX。"""
    if state.get("status") == "FAILED" or not state.get("resume_rewrite_required"):
        return state
    advice = state.get("resume_revision_advice") or {}
    if not isinstance(advice.get("contentMap"), dict) or not advice.get("contentMap"):
        return {
            **state,
            "status": "FAILED",
            "error_code": "AGENT_RESUME_REWRITE_EMPTY",
            "error_message": "简历修改建议没有生成可应用的字段候选。",
        }
    candidate = {
        "status": "PENDING_REVIEW",
        "toolName": "resume_patch_builder",
        "requiresApproval": True,
        "approvalType": "OUTPUT",
        "message": text_value(advice.get("message")) or "已生成待确认的简历字段补丁候选。",
        "contentMap": dict(advice.get("contentMap") or {}),
        "rewriteTargets": list(advice.get("rewriteTargets") or []),
        "patches": list(advice.get("patches") or []),
        "gapSuggestions": [item for item in advice.get("gapSuggestions") or [] if isinstance(item, dict)],
        "jdProfile": state.get("resume_jd_profile") or {},
        "evidenceBundle": state.get("resume_evidence_bundle") or {},
    }
    return {**state, "resume_patch_candidate": candidate, "resume_rewrite_draft": candidate}


def resume_rewrite_acceptance_node(state: UnifiedAgentState, client: AgentGateway | None = None) -> UnifiedAgentState:
    """验收简历证据链和字段候选，合格后直接进入 OUTPUT 审批而非重复执行 RAG。"""
    if state.get("status") == "FAILED" or not state.get("resume_rewrite_required"):
        return state
    draft = state.get("resume_patch_candidate") or state.get("resume_rewrite_draft") or {}
    if not draft.get("contentMap"):
        return {
            **state,
            "status": "FAILED",
            "error_code": "AGENT_RESUME_REWRITE_EMPTY",
            "error_message": "简历修改子图没有生成可审批候选。",
            "resume_rewrite_result": {"accepted": False},
        }
    fallback_result = {
        "accepted": True,
        "requiresOutputReview": True,
        "candidateCount": len(draft.get("patches") or []),
    }
    result = build_llm_resume_rewrite_acceptance(state, fallback_result, client)
    if not result.get("accepted"):
        return {
            **state,
            "status": "FAILED",
            "error_code": "AGENT_RESUME_REWRITE_REJECTED",
            "error_message": text_value(result.get("reason")) or "简历候选未通过证据和字段完整性验收。",
            "resume_rewrite_result": result,
        }
    accepted_state = {**state, "resume_rewrite_result": result}
    if client is None:
        return accepted_state
    draft_result = synthesize_planning_draft(accepted_state, client)
    return {
        **accepted_state,
        "draft_result": draft_result,
        "final_result": draft_result,
        "verifier_result": {"complete": True, "requiresOutputReview": True, "reason": "简历证据改写候选已完成"},
        "completion_score": 1.0,
        "status": "WAITING_OUTPUT_REVIEW",
    }


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


def route_after_resume_rewrite_decision(state: UnifiedAgentState) -> Literal["resume_jd_analyzer", "memory_prefetch_after_planner"]:
    """简历修改判定后决定是否进入简历修改子图。"""
    return "resume_jd_analyzer" if state.get("resume_rewrite_required") else "memory_prefetch_after_planner"


def route_after_resume_rewrite_acceptance(state: UnifiedAgentState) -> Literal["answer_writer"]:
    """简历证据改写验收后直接进入输出审批，避免重复调用已完成的 RAG。"""
    return "answer_writer"


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
    jd_profile = state.get("resume_jd_profile") if isinstance(state.get("resume_jd_profile"), dict) else {}
    evidence_bundle = state.get("resume_evidence_bundle") if isinstance(state.get("resume_evidence_bundle"), dict) else {}
    rag_result = latest_tool_result_by_name(state, "rag_query_probe_non_persistent")
    rag_data = rag_result.get("data") if isinstance(rag_result.get("data"), dict) else {}
    evidences = evidence_bundle.get("items") if isinstance(evidence_bundle.get("items"), list) else rag_data.get("evidences") if isinstance(rag_data.get("evidences"), list) else []
    evidence_ids = list(evidence_bundle.get("evidenceIds") or []) or [str(item.get("evidenceId")) for item in evidences if isinstance(item, dict) and item.get("evidenceId")]
    requirements = [item["requirement"] for item in resume_jd_requirements(jd_profile)] or extract_requirements(jd_text or state.get("user_goal") or "")
    alignment = build_resume_evidence_alignment(jd_profile, resume_text, evidence_bundle) if jd_profile and evidence_bundle else build_alignment(requirements, resume_text, evidence_ids)
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
        "jdProfile": jd_profile,
        "evidenceBundle": evidence_bundle,
        "revisionAdvice": state.get("resume_revision_advice") or {},
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


def build_resume_jd_profile(jd_text: str, task_input: dict[str, Any]) -> dict[str, Any]:
    """用确定性规则构造 JD 画像，保证模型不可用时仍可安全发起 RAG。"""
    requirements = extract_requirements(jd_text)
    items = [
        {
            "id": f"req-{index}",
            "requirement": requirement,
            "priority": "HIGH" if index <= 3 else "MEDIUM",
            "keywords": extract_resume_keywords(requirement),
        }
        for index, requirement in enumerate(requirements, start=1)
    ]
    job_title = text_value(task_input.get("targetJobTitle")) or text_value(task_input.get("jobTitle")) or "目标岗位"
    return {
        "jobTitle": job_title,
        "mustRequirements": items[:3],
        "preferredRequirements": items[3:],
        "summary": f"已从岗位 JD 抽取 {len(items)} 项用于学习证据检索的要求。",
    }


def resume_jd_requirements(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """规范化 JD 画像中的要求，避免子 Agent 之间传递无效或重复 requirement。"""
    requirements: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for bucket, default_priority in (("mustRequirements", "HIGH"), ("preferredRequirements", "MEDIUM")):
        values = profile.get(bucket) if isinstance(profile.get(bucket), list) else []
        for index, value in enumerate(values, start=1):
            if not isinstance(value, dict):
                continue
            requirement = text_value(value.get("requirement"))
            if not requirement:
                continue
            requirement_id = text_value(value.get("id")) or f"req-{len(requirements) + index}"
            if requirement_id in seen_ids:
                continue
            seen_ids.add(requirement_id)
            priority = text_value(value.get("priority")).upper()
            keywords = value.get("keywords") if isinstance(value.get("keywords"), list) else []
            requirements.append(
                {
                    "id": requirement_id,
                    "requirement": truncate_text(requirement, 120),
                    "priority": priority if priority in {"HIGH", "MEDIUM", "LOW"} else default_priority,
                    "keywords": [text_value(item) for item in keywords if text_value(item)][:8] or extract_resume_keywords(requirement),
                }
            )
    return requirements[:8]


def extract_resume_keywords(requirement: str) -> list[str]:
    """提取可用于 evidence 相关性判断的短关键词，不把泛化词当成支持证据。"""
    ignored = {"具备", "熟悉", "掌握", "负责", "相关", "能力", "经验", "要求", "岗位", "优先", "能够", "以及"}
    parts = re.split(r"[\s、，,；;：:（）()和]+", requirement)
    keywords = [part.strip() for part in parts if len(part.strip()) >= 2 and part.strip() not in ignored]
    return keywords[:6] or [truncate_text(requirement, 32)]


def build_resume_evidence_question(profile: dict[str, Any], goal: str) -> str:
    """将结构化 JD 要求转换为 RAG 查询，保留原始要求而不是只传自然语言总结。"""
    requirements = resume_jd_requirements(profile)
    requirement_text = "；".join(item["requirement"] for item in requirements)
    return f"请从当前用户学习资料中检索可证明以下岗位要求的具体项目、课程、作品或技能 evidence：{requirement_text or goal or '岗位相关学习证据'}"


def safe_resume_evidence_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """提取供后续子 Agent 使用的最小 evidence 字段，保留来源和分数。"""
    evidence_id = text_value(item.get("evidenceId"))
    if not evidence_id:
        return None
    try:
        score = float(item.get("score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "evidenceId": evidence_id,
        "documentTitle": truncate_text(text_value(item.get("documentTitle")) or text_value(item.get("title")) or "未命名资料", 160),
        "sectionName": truncate_text(text_value(item.get("sectionName")) or "全文", 120),
        "snippet": truncate_text(text_value(item.get("snippet")), 500),
        "source": truncate_text(text_value(item.get("source")) or "learning_material", 120),
        "score": max(0.0, min(score, 1.0)),
    }


def build_resume_evidence_bundle(profile: dict[str, Any], question: str, rag_data: dict[str, Any]) -> dict[str, Any]:
    """构造跨子 Agent 传递的 evidence 束，绝不以摘要替换原始引用字段。"""
    raw_items = rag_data.get("evidences") if isinstance(rag_data.get("evidences"), list) else []
    items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        item = safe_resume_evidence_item(raw_item)
        if item is None or item["evidenceId"] in seen_ids:
            continue
        seen_ids.add(item["evidenceId"])
        items.append(item)
    return {
        "query": question,
        "expandedQueries": rag_data.get("expandedQueries") if isinstance(rag_data.get("expandedQueries"), list) else [question],
        "items": items,
        "evidenceIds": [item["evidenceId"] for item in items],
        "retrievalDiagnostics": rag_data.get("diagnostics") if isinstance(rag_data.get("diagnostics"), dict) else {},
        "requirementEvidence": build_requirement_evidence_map(profile, items),
        "summary": "" if items else "当前学习资料未检索到可引用证据，仅可输出能力缺口与补强建议。",
    }


def build_requirement_evidence_map(profile: dict[str, Any], evidence_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """以关键词交集建立保守的 requirement-evidence 映射，避免任意 evidence 自动支持首条 JD。"""
    mapping: list[dict[str, Any]] = []
    for requirement in resume_jd_requirements(profile):
        keywords = [item.lower() for item in requirement.get("keywords") or [] if len(item) >= 2]
        matched_ids: list[str] = []
        for evidence in evidence_items:
            searchable = " ".join(
                [
                    text_value(evidence.get("documentTitle")),
                    text_value(evidence.get("sectionName")),
                    text_value(evidence.get("snippet")),
                ]
            ).lower()
            if any(keyword in searchable for keyword in keywords):
                matched_ids.append(text_value(evidence.get("evidenceId")))
        mapping.append({"requirementId": requirement["id"], "evidenceIds": matched_ids[:3]})
    return mapping


def build_resume_evidence_summary(profile: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    """为证据摘要子 Agent 提供确定性 fallback，缺证据时显式保留缺口。"""
    mapping = bundle.get("requirementEvidence") if isinstance(bundle.get("requirementEvidence"), list) else []
    covered = sum(1 for item in mapping if isinstance(item, dict) and item.get("evidenceIds"))
    requirement_count = len(resume_jd_requirements(profile))
    evidence_count = len(bundle.get("evidenceIds") or [])
    return {
        "summary": f"检索到 {evidence_count} 条候选学习证据；按保守关键词映射覆盖 {covered}/{requirement_count} 项岗位要求，未覆盖项只能作为能力缺口处理。",
        "requirementEvidence": mapping,
        "missingRequirementIds": [item["requirementId"] for item in mapping if isinstance(item, dict) and not item.get("evidenceIds")],
    }


def build_resume_evidence_alignment(profile: dict[str, Any], resume_text: str, bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """将 JD、原简历和相关 evidence 对齐，避免把无关资料误判为能力支持。"""
    mapping_by_id = {
        text_value(item.get("requirementId")): [text_value(value) for value in item.get("evidenceIds") or [] if text_value(value)]
        for item in bundle.get("requirementEvidence") or []
        if isinstance(item, dict)
    }
    normalized_resume = resume_text.lower()
    alignment: list[dict[str, Any]] = []
    for requirement in resume_jd_requirements(profile):
        evidence_ids = mapping_by_id.get(requirement["id"], [])
        keywords = [item.lower() for item in requirement.get("keywords") or [] if len(item) >= 2]
        resume_mentions = any(keyword in normalized_resume for keyword in keywords)
        if evidence_ids and resume_mentions:
            status = "supported"
        elif evidence_ids or resume_mentions:
            status = "weak"
        else:
            status = "missing"
        alignment.append(
            {
                "requirement": requirement["requirement"],
                "requirementId": requirement["id"],
                "status": status,
                "evidenceIds": evidence_ids if status != "missing" else [],
                "reason": {
                    "supported": "原简历已有表达且存在相关学习 evidence。",
                    "weak": "原简历表达或学习 evidence 仅满足其中一项，不能夸大为已完全具备。",
                    "missing": "原简历与当前学习资料均未找到可引用支撑。",
                }[status],
            }
        )
    return alignment


def build_resume_revision_patches(content_map: dict[str, str], _evidence_ids: list[str]) -> list[dict[str, Any]]:
    """将确定性回退建议规范化为字段候选，不把无精确引文的内容标为已证实。"""
    labels = {
        "summary": "个人摘要",
        "skills": "技能关键词",
        "project_experience": "项目经历",
        "learning_plan": "补强建议",
        "gap_summary": "差距摘要",
    }
    patches: list[dict[str, Any]] = []
    factual_fields = {"summary", "skills", "project_experience"}
    for field, suggested_text in content_map.items():
        if field not in RESUME_REWRITE_CONTENT_FIELDS or not text_value(suggested_text):
            continue
        # 回退分支不生成精确引文；事实字段只能保留为缺证据草稿，不能伪装成已证实能力。
        backed_ids: list[str] = []
        patches.append(
            {
                "field": field,
                "label": labels.get(field, field),
                "suggestedText": text_value(suggested_text),
                "reason": "根据岗位 JD、原简历和本次候选 evidence 生成，需用户逐条确认。",
                "evidenceIds": backed_ids,
                "evidenceQuotes": [],
                "riskFlags": ["MISSING_EVIDENCE"] if field in factual_fields else ["NONE"],
                "status": "PENDING_REVIEW",
            }
        )
    return patches


def build_gap_suggestions(gaps: list[dict[str, str]]) -> list[dict[str, str]]:
    """将能力缺口作为独立建议返回，避免被误当作可写入 DOCX 的字段补丁。"""
    return [
        {
            "skill": text_value(item.get("skill")),
            "priority": text_value(item.get("priority")) or "MEDIUM",
            "suggestion": text_value(item.get("suggestion")),
        }
        for item in gaps[:5]
        if isinstance(item, dict) and text_value(item.get("suggestion"))
    ]


def build_llm_resume_jd_profile(
    state: UnifiedAgentState,
    fallback_profile: dict[str, Any],
    client: AgentGateway | None = None,
) -> dict[str, Any]:
    """调用 JD 分析子 Agent；输出仍须回到 JD 原文可追溯的确定性结构。"""
    task_input = state.get("task_input") or {}
    prompt = {
        "node": "resume_jd_analyzer",
        "jobDescription": truncate_text(text_value(task_input.get("jobDescription")), 3200),
        "fallbackProfile": fallback_profile,
        "expectedJson": {
            "jobTitle": "岗位名称",
            "mustRequirements": [{"id": "req-1", "requirement": "字符串", "priority": "HIGH", "keywords": ["关键词"]}],
            "preferredRequirements": [{"id": "req-4", "requirement": "字符串", "priority": "MEDIUM", "keywords": ["关键词"]}],
            "summary": "中文摘要",
        },
    }
    prompt = prepare_llm_payload(state, "resume_jd_analyzer", prompt, client)
    try:
        result = get_agent_qwen_client().complete_json(
            node="resume_jd_analyzer",
            model=agent_qwen_model("resume"),
            system_prompt=resume_jd_analyzer_system_prompt(),
            user_prompt=resume_jd_analyzer_user_prompt(prompt),
        )
        profile = sanitize_resume_jd_profile(result.data, fallback_profile, text_value(task_input.get("jobDescription")))
        record_llm_diagnostic(state, "resume_jd_analyzer", result.model, "used")
        return profile
    except Exception as exc:
        record_llm_diagnostic(state, "resume_jd_analyzer", agent_qwen_model("resume"), f"fallback: {exc}")
        return fallback_profile


def build_llm_resume_evidence_summary(
    state: UnifiedAgentState,
    fallback_summary: dict[str, Any],
    client: AgentGateway | None = None,
) -> dict[str, Any]:
    """调用证据归纳子 Agent；模型只能选择本次 RAG 返回的 requirement 和 evidence ID。"""
    bundle = state.get("resume_evidence_bundle") or {}
    profile = state.get("resume_jd_profile") or {}
    prompt = {
        "node": "resume_evidence_summarizer",
        "jdProfile": profile,
        "evidenceItems": bundle.get("items") or [],
        "fallbackSummary": fallback_summary,
        "expectedJson": {
            "summary": "仅基于 evidence 片段的中文摘要",
            "requirementEvidence": [{"requirementId": "req-1", "evidenceIds": ["evidence-id"]}],
            "missingRequirementIds": ["req-2"],
        },
    }
    prompt = prepare_llm_payload(state, "resume_evidence_summarizer", prompt, client)
    try:
        result = get_agent_qwen_client().complete_json(
            node="resume_evidence_summarizer",
            model=agent_qwen_model("resume"),
            system_prompt=resume_evidence_summarizer_system_prompt(),
            user_prompt=resume_evidence_summarizer_user_prompt(prompt),
        )
        summary = sanitize_resume_evidence_summary(result.data, fallback_summary, profile, bundle)
        record_llm_diagnostic(state, "resume_evidence_summarizer", result.model, "used")
        return summary
    except Exception as exc:
        record_llm_diagnostic(state, "resume_evidence_summarizer", agent_qwen_model("resume"), f"fallback: {exc}")
        return fallback_summary


def build_llm_resume_revision_advice(
    state: UnifiedAgentState,
    fallback_advice: dict[str, Any],
    client: AgentGateway | None = None,
) -> dict[str, Any]:
    """调用可微调的修改建议子 Agent，输出仍限制为字段候选和本次 evidence 引用。"""
    task_input = state.get("task_input") or {}
    bundle = state.get("resume_evidence_bundle") or {}
    prompt = {
        "node": "resume_revision_advisor",
        "jobDescription": truncate_text(text_value(task_input.get("jobDescription")), 2200),
        "resumeText": truncate_text(text_value(task_input.get("resumeText")), 2600),
        "jdProfile": state.get("resume_jd_profile") or {},
        "evidenceBundle": {
            "items": bundle.get("items") or [],
            "requirementEvidence": bundle.get("requirementEvidence") or [],
            "projectEvidenceIds": bundle.get("projectEvidenceIds") or [],
            "summary": bundle.get("summary") or "",
        },
        "fallbackAdvice": fallback_advice,
        "expectedJson": {
            "contentMap": {"summary": "字符串", "skills": "字符串", "project_experience": "字符串", "learning_plan": "字符串", "gap_summary": "字符串"},
            "rewriteTargets": ["岗位要求"],
            "patches": [{"field": "project_experience", "suggestedText": "字符串", "reason": "中文理由", "evidenceIds": ["evidence-id"], "evidenceQuotes": [{"evidenceId": "evidence-id", "quote": "来自原始 evidence 片段的精确短语"}], "riskFlags": ["NONE"]}],
            "gapSuggestions": [{"skill": "字符串", "priority": "HIGH", "suggestion": "补强建议"}],
            "message": "中文说明",
        },
    }
    prompt = prepare_llm_payload(state, "resume_revision_advisor", prompt, client)
    try:
        result = get_agent_qwen_client().complete_json(
            node="resume_revision_advisor",
            model=agent_qwen_model("resume"),
            system_prompt=resume_revision_advisor_system_prompt(),
            user_prompt=resume_revision_advisor_user_prompt(prompt),
        )
        advice = sanitize_resume_revision_advice(result.data, fallback_advice, bundle, text_value(task_input.get("resumeText")))
        record_llm_diagnostic(state, "resume_revision_advisor", result.model, "used")
        return advice
    except Exception as exc:
        record_llm_diagnostic(state, "resume_revision_advisor", agent_qwen_model("resume"), f"fallback: {exc}")
        return fallback_advice


def build_resume_patch_review_summary(draft: dict[str, Any]) -> dict[str, Any]:
    """构造验收子 Agent 所需的最小补丁审查上下文，保留风险和 evidence 引文。"""
    patches = []
    for item in draft.get("patches") or []:
        if not isinstance(item, dict):
            continue
        patches.append(
            {
                "field": text_value(item.get("field")),
                "suggestedText": truncate_text(text_value(item.get("suggestedText")), 500),
                "reason": truncate_text(text_value(item.get("reason")), 300),
                "evidenceIds": [text_value(value) for value in item.get("evidenceIds") or [] if text_value(value)],
                "evidenceQuotes": [value for value in item.get("evidenceQuotes") or [] if isinstance(value, dict)],
                "riskFlags": [text_value(value) for value in item.get("riskFlags") or [] if text_value(value)],
                "status": text_value(item.get("status")),
            }
        )
    bundle = draft.get("evidenceBundle") if isinstance(draft.get("evidenceBundle"), dict) else {}
    return {
        "patches": patches,
        "gapSuggestions": [item for item in draft.get("gapSuggestions") or [] if isinstance(item, dict)],
        "evidenceIds": [text_value(value) for value in bundle.get("evidenceIds") or [] if text_value(value)],
        "evidenceItems": [
            {
                "evidenceId": text_value(item.get("evidenceId")),
                "documentTitle": text_value(item.get("documentTitle")),
                "sectionName": text_value(item.get("sectionName")),
                "snippet": truncate_text(text_value(item.get("snippet")), 240),
            }
            for item in bundle.get("items") or []
            if isinstance(item, dict)
        ],
    }


def build_llm_resume_rewrite_acceptance(
    state: UnifiedAgentState,
    fallback_result: dict[str, Any],
    client: AgentGateway | None = None,
) -> dict[str, Any]:
    """调用千问辅助验收简历候选，只接受结构化布尔结果。"""
    draft = state.get("resume_patch_candidate") or state.get("resume_rewrite_draft") or {}
    prompt = {
        "node": "resume_rewrite_acceptance",
        "patchReview": build_resume_patch_review_summary(draft),
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
    """合并 JD 画像、evidence 束和确定性补丁候选，形成最终可审批输出。"""
    draft = state.get("resume_patch_candidate") or state.get("resume_rewrite_draft") or {}
    if not draft:
        return {}
    merged = dict(draft)
    merged["evidenceIds"] = evidence_ids
    merged["jdProfile"] = state.get("resume_jd_profile") or {}
    merged["evidenceBundle"] = state.get("resume_evidence_bundle") or {}
    merged["revisionAdvice"] = state.get("resume_revision_advice") or {}
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


def sanitize_resume_jd_profile(candidate: dict[str, Any], fallback_profile: dict[str, Any], jd_text: str) -> dict[str, Any]:
    """只接受可回溯到原 JD 的画像细化，防止 JD 分析子 Agent 编造岗位要求。"""
    if not isinstance(candidate, dict):
        raise ValueError("JD 画像必须是对象")
    forbidden_keys = {"locationRefs", "docxPath", "xml", "styleXml", "savePath", "evidenceIds"}
    if forbidden_keys.intersection(candidate.keys()):
        raise ValueError("JD 画像包含禁止字段")
    profile = dict(fallback_profile)
    profile["jobTitle"] = truncate_text(text_value(candidate.get("jobTitle")) or text_value(profile.get("jobTitle")), 80)
    profile["summary"] = truncate_text(text_value(candidate.get("summary")) or text_value(profile.get("summary")), 400)
    for bucket, default_priority in (("mustRequirements", "HIGH"), ("preferredRequirements", "MEDIUM")):
        fallback_items = profile.get(bucket) if isinstance(profile.get(bucket), list) else []
        candidate_items = candidate.get(bucket) if isinstance(candidate.get(bucket), list) else []
        candidates_by_id = {text_value(item.get("id")): item for item in candidate_items if isinstance(item, dict) and text_value(item.get("id"))}
        cleaned: list[dict[str, Any]] = []
        for fallback in fallback_items:
            if not isinstance(fallback, dict):
                continue
            item = dict(fallback)
            proposed = candidates_by_id.get(text_value(item.get("id")))
            if proposed:
                priority = text_value(proposed.get("priority")).upper()
                if priority in {"HIGH", "MEDIUM", "LOW"}:
                    item["priority"] = priority
                keywords = [text_value(value) for value in proposed.get("keywords") or [] if text_value(value)]
                item["keywords"] = [value for value in keywords if value.lower() in jd_text.lower()][:8] or item.get("keywords") or []
            item["priority"] = text_value(item.get("priority")).upper() or default_priority
            cleaned.append(item)
        profile[bucket] = cleaned
    return profile


def sanitize_resume_evidence_summary(
    candidate: dict[str, Any],
    fallback_summary: dict[str, Any],
    profile: dict[str, Any],
    bundle: dict[str, Any],
) -> dict[str, Any]:
    """证据摘要只保留确定性相关性映射，避免模型把无关 evidence 重新绑定到 JD。"""
    if not isinstance(candidate, dict):
        raise ValueError("证据摘要必须是对象")
    valid_requirement_ids = {item["id"] for item in resume_jd_requirements(profile)}
    valid_evidence_ids = {text_value(item) for item in bundle.get("evidenceIds") or [] if text_value(item)}
    mapping_by_requirement: dict[str, list[str]] = {}
    fallback_mapping = fallback_summary.get("requirementEvidence") if isinstance(fallback_summary.get("requirementEvidence"), list) else []
    for item in fallback_mapping:
        if not isinstance(item, dict):
            continue
        requirement_id = text_value(item.get("requirementId"))
        if requirement_id in valid_requirement_ids:
            mapping_by_requirement[requirement_id] = [text_value(value) for value in item.get("evidenceIds") or [] if text_value(value) in valid_evidence_ids][:3]
    mapping = [{"requirementId": requirement["id"], "evidenceIds": mapping_by_requirement.get(requirement["id"], [])} for requirement in resume_jd_requirements(profile)]
    return {
        "summary": truncate_text(text_value(fallback_summary.get("summary")), 600),
        "requirementEvidence": mapping,
        "missingRequirementIds": [item["requirementId"] for item in mapping if not item["evidenceIds"]],
    }


def linked_resume_evidence_ids(bundle: dict[str, Any]) -> list[str]:
    """仅返回已被确定性 JD 关键词映射命中的 evidence，排除与岗位无关的资料。"""
    supported = [text_value(item) for item in bundle.get("supportedEvidenceIds") or [] if text_value(item)]
    if supported:
        return supported
    linked = {
        text_value(evidence_id)
        for item in bundle.get("requirementEvidence") or []
        if isinstance(item, dict)
        for evidence_id in item.get("evidenceIds") or []
        if text_value(evidence_id)
    }
    return [text_value(item) for item in bundle.get("evidenceIds") or [] if text_value(item) in linked]


def evidence_text_by_id(bundle: dict[str, Any]) -> dict[str, str]:
    """建立 evidence ID 到可核验原文的映射，供补丁事实校验使用。"""
    texts: dict[str, str] = {}
    for item in bundle.get("items") or []:
        if not isinstance(item, dict):
            continue
        evidence_id = text_value(item.get("evidenceId"))
        if evidence_id:
            texts[evidence_id] = " ".join(
                [
                    text_value(item.get("documentTitle")),
                    text_value(item.get("sectionName")),
                    text_value(item.get("snippet")),
                ]
            )
    return texts


def project_resume_evidence_ids(bundle: dict[str, Any], allowed_evidence_ids: list[str] | None = None) -> list[str]:
    """从 JD 相关 evidence 中识别明确可用于项目经历的资料，课程练习不能自动视为项目。"""
    allowed_ids = set(allowed_evidence_ids if allowed_evidence_ids is not None else linked_resume_evidence_ids(bundle))
    project_ids: list[str] = []
    for item in bundle.get("items") or []:
        if not isinstance(item, dict):
            continue
        evidence_id = text_value(item.get("evidenceId"))
        if not evidence_id or evidence_id not in allowed_ids:
            continue
        source_text = " ".join(
            [
                text_value(item.get("documentTitle")),
                text_value(item.get("sectionName")),
                text_value(item.get("snippet")),
            ]
        )
        # 教学资料即使包含“开发/实现”等动词，也不能自动升级为可写入简历的项目事实。
        if any(keyword in source_text for keyword in RESUME_COURSEWORK_EVIDENCE_KEYWORDS):
            continue
        if any(keyword in source_text for keyword in RESUME_PROJECT_EVIDENCE_KEYWORDS):
            project_ids.append(evidence_id)
    return list(dict.fromkeys(project_ids))


def normalize_grounding_text(value: str) -> str:
    """归一化事实校验文本，忽略大小写和空白差异。"""
    return re.sub(r"\s+", "", value or "").lower()


def extract_verified_evidence_quotes(raw_quotes: Any, evidence_texts: dict[str, str], allowed_ids: set[str]) -> list[dict[str, str]]:
    """只接受确实出现在本次 evidence 原文中的精确引文。"""
    if not isinstance(raw_quotes, list):
        return []
    quotes: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_quotes:
        if not isinstance(item, dict):
            continue
        evidence_id = text_value(item.get("evidenceId"))
        quote = truncate_text(text_value(item.get("quote")), 240)
        if evidence_id not in allowed_ids or len(quote) < 2:
            continue
        if normalize_grounding_text(quote) not in normalize_grounding_text(evidence_texts.get(evidence_id, "")):
            continue
        key = (evidence_id, quote)
        if key not in seen:
            seen.add(key)
            quotes.append({"evidenceId": evidence_id, "quote": quote})
    return quotes


def extract_numeric_claims(text: str) -> list[str]:
    """提取高风险量化声明，防止模型添加资料中不存在的年限、百分比或指标。"""
    patterns = [
        r"\d+(?:\.\d+)?\s*%",
        r"\d+(?:\.\d+)?\s*(?:年|个月|项|次|倍|万|k|K)",
        r"[一二三四五六七八九十百千万两]+(?:年|个月|项|次|倍)",
        r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:years?|months?|times?)\b",
    ]
    return [match.group(0) for pattern in patterns for match in re.finditer(pattern, text, flags=re.IGNORECASE)]


def is_factual_patch_grounded(text: str, resume_text: str, quotes: list[dict[str, str]]) -> bool:
    """要求事实型候选具备精确引文，并核对量化、技术和高风险职责词不凭空新增。"""
    if not quotes:
        return False
    source = " ".join([resume_text, *(item["quote"] for item in quotes)])
    normalized_source = normalize_grounding_text(source)
    for claim in extract_numeric_claims(text):
        if normalize_grounding_text(claim) not in normalized_source:
            return False
    technical_terms = re.findall(r"\b[A-Za-z][A-Za-z0-9+.#/-]{1,}\b", text)
    if not all(term.lower() in source.lower() for term in technical_terms):
        return False
    high_risk_terms = ["主导", "负责", "上线", "部署", "实现", "构建", "开发", "优化", "提升", "降低", "获得", "管理", "设计", "发布", "落地", "完成", "牵头", "领导"]
    return all(term not in text or term in source for term in high_risk_terms)


def sanitize_resume_revision_advice(
    candidate: dict[str, Any],
    fallback_advice: dict[str, Any],
    bundle: dict[str, Any],
    resume_text: str = "",
) -> dict[str, Any]:
    """仅保留有 JD 相关 evidence 精确引文支撑的事实型改写；其余退回安全草稿。"""
    if not isinstance(candidate, dict):
        raise ValueError("简历修改建议必须是对象")
    forbidden_keys = {"locationRefs", "docxPath", "xml", "styleXml", "savePath", "fieldId", "sourceTextHash", "status"}
    if forbidden_keys.intersection(candidate.keys()):
        raise ValueError("简历修改建议包含禁止字段")
    fallback_content = fallback_advice.get("contentMap") if isinstance(fallback_advice.get("contentMap"), dict) else {}
    content_map = {
        key: truncate_text(text_value(value), 2000)
        for key, value in fallback_content.items()
        if key in RESUME_REWRITE_CONTENT_FIELDS and text_value(value)
    }
    if not content_map:
        raise ValueError("简历修改建议缺少 contentMap")
    allowed_evidence_ids = linked_resume_evidence_ids(bundle)
    allowed_evidence_set = set(allowed_evidence_ids)
    evidence_texts = evidence_text_by_id(bundle)
    fallback_patches = [
        dict(item)
        for item in fallback_advice.get("patches") or []
        if isinstance(item, dict) and text_value(item.get("field")) in RESUME_REWRITE_CONTENT_FIELDS
    ] or build_resume_revision_patches(content_map, allowed_evidence_ids)
    fallback_by_field = {text_value(item.get("field")): item for item in fallback_patches if isinstance(item, dict)}
    patches: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    factual_fields = {"summary", "skills", "project_experience"}
    project_evidence_set = set(project_resume_evidence_ids(bundle, allowed_evidence_ids))
    raw_patches = candidate.get("patches") if isinstance(candidate.get("patches"), list) else []
    for item in raw_patches:
        if not isinstance(item, dict):
            continue
        field = text_value(item.get("field"))
        if field not in RESUME_REWRITE_CONTENT_FIELDS or field in seen_fields:
            continue
        seen_fields.add(field)
        evidence_ids = [text_value(value) for value in item.get("evidenceIds") or [] if text_value(value) in allowed_evidence_set]
        quotes = extract_verified_evidence_quotes(item.get("evidenceQuotes"), evidence_texts, set(evidence_ids))
        proposed_text = truncate_text(text_value(item.get("suggestedText")) or content_map[field], 2000)
        project_grounded = field != "project_experience" or (
            bool(evidence_ids) and set(evidence_ids).issubset(project_evidence_set)
        )
        grounded = field not in factual_fields or (
            is_factual_patch_grounded(proposed_text, resume_text, quotes) and project_grounded
        )
        if not grounded:
            fallback_patch = dict(fallback_by_field.get(field) or {})
            patches.append(
                {
                    **fallback_patch,
                    "field": field,
                    "suggestedText": content_map[field],
                    "reason": "建议缺少与 JD 相关 evidence 的精确引文或包含未被支撑的事实，已退回安全草稿。",
                    "evidenceIds": [],
                    "evidenceQuotes": [],
                    "riskFlags": ["MISSING_EVIDENCE"],
                    "status": "PENDING_REVIEW",
                }
            )
            continue
        content_map[field] = proposed_text
        risk_flags = [text_value(value) for value in item.get("riskFlags") or [] if text_value(value) in {"NONE", "MISSING_EVIDENCE", "LOW_CONFIDENCE"}]
        if field in factual_fields:
            risk_flags = ["NONE"]
        elif not risk_flags:
            risk_flags = ["NONE"] if evidence_ids else ["MISSING_EVIDENCE"]
        patches.append(
            {
                "field": field,
                "label": text_value((fallback_by_field.get(field) or {}).get("label")) or field,
                "suggestedText": proposed_text,
                "reason": truncate_text(text_value(item.get("reason")) or "根据岗位 JD、原简历和候选 evidence 生成，需用户确认。", 500),
                "evidenceIds": list(dict.fromkeys(evidence_ids)),
                "evidenceQuotes": quotes,
                "riskFlags": risk_flags,
                "status": "PENDING_REVIEW",
            }
        )
    for field, fallback_patch in fallback_by_field.items():
        if field not in seen_fields:
            patches.append(fallback_patch)
    targets = sanitize_string_list(candidate.get("rewriteTargets")) or sanitize_string_list(fallback_advice.get("rewriteTargets"))
    return {
        "contentMap": content_map,
        "rewriteTargets": targets[:8],
        "patches": patches,
        "gapSuggestions": [item for item in fallback_advice.get("gapSuggestions") or [] if isinstance(item, dict)],
        "message": truncate_text(text_value(candidate.get("message")) or text_value(fallback_advice.get("message")), 500),
    }


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


def resume_jd_analyzer_system_prompt() -> str:
    """JD 分析子 Agent 的提示词。"""
    return (
        "你是简历证据改写工作流中的 JD 分析子 Agent。你只从给定岗位 JD 提取硬性要求、加分项和关键词。"
        "不能编造 JD 未出现的资格、公司信息或项目要求；不能输出 evidence、DOCX、文件路径、样式、XML 或保存动作。"
        "保留输入中已有 requirement id，不要新建不可追溯 id。只输出合法 JSON。"
    )


def resume_jd_analyzer_user_prompt(payload: dict[str, Any]) -> str:
    """JD 分析子 Agent 的用户提示词。"""
    return "请将以下岗位 JD 归纳为可检索、可审计的岗位画像 JSON，不要输出解释文字：\n" + json_prompt(payload)


def resume_evidence_summarizer_system_prompt() -> str:
    """学习证据归纳子 Agent 的提示词。"""
    return (
        "你是学习证据归纳子 Agent。你只能根据输入 evidence 的标题、章节、片段、来源和分数概括支持范围。"
        "requirementId 和 evidenceId 必须从输入集合中选择；证据不足时必须列为缺口，不能推断学生具备未被片段支持的能力。"
        "不能输出 DOCX、排版、路径、保存操作或新的 evidence。只输出合法 JSON。"
    )


def resume_evidence_summarizer_user_prompt(payload: dict[str, Any]) -> str:
    """学习证据归纳子 Agent 的用户提示词。"""
    return "请在保留 evidence 引用的条件下生成证据覆盖摘要 JSON，不要输出解释文字：\n" + json_prompt(payload)


def resume_revision_advisor_system_prompt() -> str:
    """可微调的简历修改建议子 Agent 提示词。"""
    return (
        "你是简历修改建议子 Agent。你的任务是依据 JD、原简历和输入 evidence 生成字段级改写候选。"
        "你只能修改 summary、skills、project_experience、learning_plan、gap_summary 五类文本候选；每个事实性改写必须引用输入 evidenceId，并提供该 evidence 片段中的精确短语 evidenceQuotes。"
        "如果证据不足，只输出缺口和补强建议，不能补造项目、技能、指标、证书、实习或工作经历。"
        "不得输出 fieldId、sourceTextHash、locationRefs、DOCX、XML、样式、路径、确认状态或保存动作。只输出合法 JSON。"
    )


def resume_revision_advisor_user_prompt(payload: dict[str, Any]) -> str:
    """可微调的简历修改建议子 Agent 用户提示词。"""
    return "请生成可由用户逐条确认的简历字段修改建议 JSON，不要输出解释文字：\n" + json_prompt(payload)


def resume_rewrite_acceptance_system_prompt() -> str:
    """简历修改验收节点专用提示词。"""
    return (
        "你是简历修改子图的验收节点。你检查每个字段候选的风险标记、evidenceId、精确引文和独立 gapSuggestions 是否可进入人工 OUTPUT 审批。"
        "存在 MISSING_EVIDENCE 时只能作为缺口建议，不得认可为已具备能力。你不能批准保存、不能写 DOCX、不能新增 evidence。只输出 JSON。"
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
