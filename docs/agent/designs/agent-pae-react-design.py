"""
================================================================================
PAE + ReAct LangGraph 节点与边设计方案（面向当前项目落地）
================================================================================

基于当前项目代码现状（read_only_graph.py / planning_graph.py / java_gateway.py /
memory_service.py / AgentToolGatewayServiceImpl.java）设计。

核心思路：
  - PAE 层（Supervisor）：Planner 生成步骤计划 → Executor 逐步执行 → Reviewer 把关 → Finalize 收尾
  - ReAct 层（Executor 内部）：Reasoner(LLM 推理+选工具) → Actor(调 Java Gateway) → Observer(消化结果)
    → Router(条件边：继续推理 or 退出)
  - 写操作仍走 Human-in-the-Loop(CRUD 审批)，ReAct 循环不跨越审批边界

================================================================================
一、State Schema 设计
================================================================================
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, StateGraph


# ─── 工具定义（ReAct 可见的工具清单） ─────────────────────────────────────────
# 每项工具需在 Java Tool Gateway 侧已注册（对应 AgentToolGatewayServiceImpl.executeReadToolData）

REACT_READ_TOOLS: list[dict[str, Any]] = [
    {
        "name": "rag_query_probe_non_persistent",
        "description": "检索用户个人知识库中的学习证据，返回回答和引用。用于查找简历证据、技能证明、项目经历。",
        "parameters": {
            "question": "string - 检索问题",
            "topK": "int - 返回条数，默认 5",
            "candidateMultiplier": "int - 候选倍数，默认 4",
            "metadataFilter": "dict - 可选元数据过滤",
        },
    },
    {
        "name": "material_evidence_reader",
        "description": "读取指定资料下已索引的 evidence 列表。",
        "parameters": {
            "materialId": "long - 资料 ID",
            "topK": "int - 返回条数，默认 20",
        },
    },
    {
        "name": "web_search_probe",
        "description": "通过 Tavily 联网搜索公司背景、技能趋势等外部信息。仅作参考，不写入知识库。",
        "parameters": {
            "query": "string - 搜索查询",
            "maxResults": "int - 最大结果数，默认 5",
            "searchDepth": "string - basic/advanced，默认 basic",
        },
    },
    {
        "name": "agent_memory_retriever",
        "description": "检索当前用户的 Agent 长期记忆，包括历史偏好、弱能力缺口、任务经验。",
        "parameters": {
            "query": "string - 记忆检索查询",
            "topK": "int - 返回条数，默认 5",
        },
    },
    {
        "name": "retrieval_coverage_probe",
        "description": "执行 RAG 检索覆盖诊断，返回 answer + evidence 分布 + 来源统计。",
        "parameters": {
            "question": "string - 诊断问题",
            "topK": "int - 返回条数",
            "candidateMultiplier": "int - 候选倍数",
        },
    },
]


# ─── PAE 层 State ────────────────────────────────────────────────────────────

class PaeAgentState(TypedDict, total=False):
    """PAE 主图 State，在现有 ReadOnlyAgentState 上扩充。"""

    # 继承自现有字段
    task_id: str
    task_type: str
    thread_id: str
    task_input: dict[str, Any]
    status: str
    error_code: str | None
    error_message: str | None

    # ── 新增：PAE 专用字段 ──
    # Planner 产出的执行计划
    plan: dict[str, Any]              # {"title", "steps": [...], "riskLevel"}
    plan_approved: bool                # 计划是否已被用户审批

    # Executor 执行进度
    current_step_index: int            # 当前执行到第几步
    step_results: list[dict[str, Any]] # 每步的执行结果

    # ReAct 循环内累积
    messages: list[dict[str, Any]]    # LLM 对话历史（含 thought/action/observation）
    observations: list[dict[str, Any]]# 所有工具观察摘要
    scratchpad: dict[str, Any]        # 中间态草稿（对齐矩阵、缺口等）

    # 记忆上下文
    memory_context: list[dict[str, Any]]

    # 最终结果
    final_result: dict[str, Any]

    # 审批
    pending_review: dict[str, Any] | None  # 当前挂起的审批请求


# ─── ReAct 子图 State ────────────────────────────────────────────────────────
# 作为 PAE Executor 内部循环的 State，不与 PAE 主 State 混用

class ReactExecutorState(TypedDict, total=False):
    """ReAct 执行器子图 State。每个 plan step 传入一份新的。"""

    # 父图传入的上下文（只读）
    task_id: str
    thread_id: str
    step_description: str             # 当前步骤的文本描述
    allowed_tools: list[str]          # 当前步骤允许的工具列表
    scratchpad: dict[str, Any]        # 父图传入的草稿上下文

    # ReAct 循环内
    messages: list[dict[str, Any]]    # 对话历史
    iteration: int                    # 当前迭代轮次
    max_iterations: int               # 最大迭代次数（默认 5）

    # 当前轮次
    thought: str                      # LLM 推理输出
    action: dict[str, Any]            # 选中的工具 {"toolName", "arguments"}
    observation: dict[str, Any]       # 工具执行结果摘要

    # 控制
    status: str                       # RUNNING / FINISH / FAILED
    step_result: dict[str, Any]       # 当前步骤最终输出
    error_code: str | None
    error_message: str | None


"""
================================================================================
二、PAE 主图节点与边设计
================================================================================

graph LR
    START --> memory_prefetch
    memory_prefetch --> planner
    planner --> plan_review{用户审批}
    plan_review -- APPROVED --> executor_step
    executor_step --> router_executor{还有步骤?}
    router_executor -- 是 --> executor_step
    router_executor -- 否 --> reviewer
    reviewer --> output_review{用户审批}
    output_review -- APPROVED --> crud_review{需保存?}
    crud_review -- 是 --> WAITING_CRUD_REVIEW
    crud_review -- 否 --> finalize
    finalize --> END

关键技术点：
  1. planner 节点调用 LLM 生成步骤计划（首次用 LLM 做 Agent 层决策）
  2. executor_step 内部运行 ReAct 子图（invoke subgraph）
  3. plan_review / output_review / crud_review 是 Human-in-the-Loop 断点
  4. 每次 executor_step 可跨多轮 ReAct 循环，但只有一个工具调用序列
"""


def build_pae_graph(client):
    """
    构建 PAE 主图。

    节点说明：
      memory_prefetch  — 预取用户长期记忆（复用现有逻辑）
      planner          — LLM 生成执行计划
      executor_step    — 对 plan.steps 逐步执行 ReAct 子图
      reviewer         — LLM 审查执行结果，判断是否需要修正
      finalize         — 生成最终响应 + 事件回调

    条件边：
      plan_review      — 检查 plan_approved 状态
      router_executor  — 检查 current_step_index < len(plan.steps)
      router_reviewer  — 检查是否需要修正（corrective loop）
      crud_review      — 检查是否需要保存类变更
    """
    workflow = StateGraph(PaeAgentState)

    # ── 添加节点 ──
    workflow.add_node("memory_prefetch", lambda s: _memory_prefetch(s, client))
    workflow.add_node("planner", lambda s: _planner(s, client))
    workflow.add_node("executor_step", lambda s: _executor_step(s, client))
    workflow.add_node("reviewer", lambda s: _reviewer(s, client))
    workflow.add_node("finalize", lambda s: _finalize(s, client))

    # ── 入口 ──
    workflow.set_entry_point("memory_prefetch")

    # ── 线性边 ──
    workflow.add_edge("memory_prefetch", "planner")

    # ── 条件边：planner → [plan 审批中断] 或直接进 executor ──
    # 首次 invoke 时 planner 产出 plan，graph.invoke() 返回后 Java 侧发起审批；
    # 审批通过后以相同 thread_id 再次 invoke，此时 plan_approved=True
    workflow.add_conditional_edges(
        "planner",
        lambda s: "executor_step" if s.get("plan_approved") else END,
        {
            "executor_step": "executor_step",
            # END → Python 返回 WAITING_PLAN_REVIEW，Java 发起审批
        },
    )

    # ── 条件边：executor_step → 下一步 or reviewer ──
    workflow.add_conditional_edges(
        "executor_step",
        lambda s: _route_executor(s),
        {
            "executor_step": "executor_step",  # 循环执行下一步
            "reviewer": "reviewer",            # 所有步骤完成，进入审查
        },
    )

    # ── 条件边：reviewer → 修正(回 executor) or 最终确认 or 直接 finalize ──
    workflow.add_conditional_edges(
        "reviewer",
        lambda s: _route_reviewer(s),
        {
            "executor_step": "executor_step",  # 需要修正，回到执行器
            "crud_pending": END,               # 需要 CRUD 审批，中断
            "finalize": "finalize",            # 无需保存，直接结束
        },
    )

    # ── 尾边 ──
    workflow.add_edge("finalize", END)

    return workflow.compile()


"""
================================================================================
三、ReAct 子图节点与边设计
================================================================================

graph LR
    reasoner --> actor
    actor --> observer
    observer --> router{继续?}
    router -- "ACT" --> reasoner
    router -- "FINISH" --> END(返回父图)

ReAct 循环内每轮：
  1. reasoner  — LLM 输出 thought + action（选择工具并构造参数）
  2. actor     — 调用 Java Read Tool Gateway
  3. observer  — 处理结果，追加到 messages
  4. router    — 条件判断：迭代次数限制 / LLM 决定 FINISH / 异常终止
"""


def build_react_executor_graph(client) -> StateGraph:
    """构建 ReAct 执行器子图。每个 plan step 调用一次 invoke。"""
    workflow = StateGraph(ReactExecutorState)

    workflow.add_node("reasoner", lambda s: _reasoner(s))
    workflow.add_node("actor", lambda s: _actor(s, client))
    workflow.add_node("observer", lambda s: _observer(s))

    workflow.set_entry_point("reasoner")
    workflow.add_edge("reasoner", "actor")
    workflow.add_edge("actor", "observer")

    # ── 条件边：observer → 继续推理 or 结束 ──
    workflow.add_conditional_edges(
        "observer",
        lambda s: _route_react(s),
        {
            "reasoner": "reasoner",   # 继续下一轮
            END: END,                 # 退出子图，返回父图
        },
    )

    return workflow.compile()


"""
================================================================================
四、核心节点实现逻辑
================================================================================
"""

# ── 4.1 LLM 客户端 ───────────────────────────────────────────────────────────

import json
import os
import uuid
from datetime import datetime, timezone

import httpx

from agents.gateway.java_gateway import JavaAgentGatewayClient


class AgentLLMClient:
    """Agent 层专用 LLM 客户端，复用百炼 OpenAI 兼容 API。

    与 RAG 层的 BailianChatClient 独立，允许使用不同模型（如 qwen-max 做推理，
    qwen-plus 做回答生成），且 Agent 层 prompt 风格不同。
    """

    def __init__(self):
        self.api_key = os.getenv("DASHSCOPE_API_KEY")
        self.base_url = (
            os.getenv("AGENT_LLM_BASE_URL")
            or os.getenv("RAG_LLM_BASE_URL")
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        ).rstrip("/")
        self.model = os.getenv("AGENT_LLM_MODEL") or os.getenv("RAG_LLM_MODEL") or "qwen-plus"
        self.timeout = float(os.getenv("AGENT_LLM_TIMEOUT", "45"))

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.3) -> str:
        """发送 chat completion 请求，返回模型文本输出。"""
        if not self.api_key:
            return _local_fallback_reasoning(messages)
        try:
            with httpx.Client(timeout=self.timeout) as http:
                resp = http.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model,
                        "messages": messages,
                        "temperature": temperature,
                    },
                )
                resp.raise_for_status()
                body = resp.json()
                return body["choices"][0]["message"]["content"]
        except Exception:
            return _local_fallback_reasoning(messages)


def _local_fallback_reasoning(messages: list[dict[str, str]]) -> str:
    """LLM 不可用时的降级推理：返回 FINISH 指令，不做复杂推理。"""
    return "FINISH\n当前无法调用推理模型，请直接使用默认检索工具获取结果。"


# ── 4.2 PAE 层节点 ───────────────────────────────────────────────────────────

def _memory_prefetch(state: PaeAgentState, client: JavaAgentGatewayClient) -> PaeAgentState:
    """预取用户长期记忆，复用现有 prefetch_memory_context 逻辑。"""
    from agents.read_only.read_only_graph import prefetch_memory_context, task_query

    query = task_query(state.get("task_input") or {}, state.get("task_type"))
    memory = prefetch_memory_context(
        task_id=state["task_id"],
        thread_id=state["thread_id"],
        task_input=state.get("task_input") or {},
        query=query,
        client=client,
    )
    return {**state, "memory_context": memory}


def _planner(state: PaeAgentState, client: JavaAgentGatewayClient) -> PaeAgentState:
    """
    Planner 节点：LLM 根据任务目标 + 可用工具 + 记忆上下文生成执行计划。

    这是项目中首次让 LLM 参与 Agent 层决策的节点。
    产出结构化 plan JSON，包含 steps 数组。
    """
    if state.get("status") == "FAILED":
        return state

    task_input = state.get("task_input") or {}
    goal = task_input.get("goal") or task_input.get("question") or "分析用户学习证据"
    memory = state.get("memory_context") or []

    tools_desc = "\n".join(
        f"- {tool['name']}: {tool['description']}" for tool in REACT_READ_TOOLS
    )

    llm = AgentLLMClient()
    messages = [
        {
            "role": "system",
            "content": (
                "你是学迹智配的 Agent 规划器。根据用户目标和可用工具生成执行计划。\n\n"
                f"可用只读工具：\n{tools_desc}\n\n"
                "输出严格的 JSON 格式，不要 Markdown code block：\n"
                '{"title": "计划标题", "steps": [{"description": "步骤描述", "allowedTools": ["工具名"], '
                '"expectedOutput": "期望产出"}], "riskLevel": "LOW|MEDIUM|HIGH"}\n\n"
                "规则：\n"
                "1. 步骤数不超过 5 步\n"
                "2. 每个步骤只能使用 allowedTools 列表中的工具\n"
                "3. 第一步通常是检索或记忆读取\n"
                "4. 最后一步是整合分析\n"
                "5. riskLevel：只读=LOW，涉及外部搜索=MEDIUM"
            ),
        },
        {
            "role": "user",
            "content": (
                f"用户目标：{goal}\n"
                f"当前知识：{json.dumps(task_input, ensure_ascii=False)[:500]}\n"
                f"历史记忆：{json.dumps(memory, ensure_ascii=False)[:400]}\n"
                "请生成执行计划。"
            ),
        },
    ]

    try:
        plan_text = llm.chat(messages)
        plan = _parse_json_response(plan_text)
    except Exception:
        # LLM 失败降级：使用静态计划
        plan = _fallback_plan(goal)

    # 发布计划审批事件
    if not state.get("plan_approved"):
        client.publish_event(
            _make_event(
                state,
                event_type="REVIEW_REQUESTED",
                status="WAITING_PLAN_REVIEW",
                review_request={
                    "id": f"review-plan-{state['task_id']}",
                    "reviewType": "PLAN",
                    "proposal": plan,
                },
            )
        )

    return {
        **state,
        "plan": plan,
        "current_step_index": 0,
        "step_results": [],
        "messages": [],
        "observations": [],
        "scratchpad": {},
    }


def _executor_step(state: PaeAgentState, client: JavaAgentGatewayClient) -> PaeAgentState:
    """
    Executor 单步执行节点。

    对 plan.steps[current_step_index] 启动 ReAct 子图，执行完毕后推进索引。
    这是 PAE 和 ReAct 的衔接点：PAE 调 ReAct 子图。
    """
    if state.get("status") == "FAILED":
        return state

    plan = state.get("plan") or {}
    steps = plan.get("steps") or []
    idx = state.get("current_step_index", 0)

    if idx >= len(steps):
        return state

    step = steps[idx]
    allowed_tools = step.get("allowedTools", [])

    # ── 构造 ReAct 子图初始 State ──
    react_initial: ReactExecutorState = {
        "task_id": state["task_id"],
        "thread_id": state["thread_id"],
        "step_description": step.get("description", ""),
        "allowed_tools": allowed_tools,
        "scratchpad": state.get("scratchpad") or {},
        "messages": [],
        "iteration": 0,
        "max_iterations": 5,
        "status": "RUNNING",
    }

    # ── 执行 ReAct 子图 ──
    subgraph = _cached_react_graph(client)
    react_result = subgraph.invoke(react_initial)

    # ── 收集结果 ──
    step_results = list(state.get("step_results") or [])
    step_results.append({
        "stepIndex": idx,
        "description": step.get("description"),
        "result": react_result.get("step_result") or {},
        "observations": react_result.get("messages") or [],
        "status": react_result.get("status", "FAILED"),
    })

    # 更新 scratchpad（累积中间结果）
    scratchpad = {**(state.get("scratchpad") or {})}
    step_result_data = react_result.get("step_result") or {}
    if isinstance(step_result_data, dict):
        scratchpad.update(step_result_data)

    return {
        **state,
        "current_step_index": idx + 1,
        "step_results": step_results,
        "scratchpad": scratchpad,
        "observations": (state.get("observations") or []) + (react_result.get("messages") or []),
    }


def _reviewer(state: PaeAgentState, client: JavaAgentGatewayClient) -> PaeAgentState:
    """
    Reviewer 节点：LLM 审查所有步骤结果，判断是否需要修正或生成最终输出。

    如果发现缺口 → 追加修正步骤到 plan.steps，路由回 executor_step。
    如果需要保存类变更 → 发起 CRUD 审批。
    否则 → 构造 final_result 并进入 finalize。
    """
    if state.get("status") == "FAILED":
        return state

    step_results = state.get("step_results") or []
    scratchpad = state.get("scratchpad") or {}

    llm = AgentLLMClient()
    messages = [
        {
            "role": "system",
            "content": (
                "你是学迹智配的 Agent 审查器。审查执行结果，判断是否需要修正。\n"
                "输出 JSON：{\"verdict\": \"PASS|NEED_CORRECTION\", "
                "\"correctionSteps\": [{\"description\": \"\", \"allowedTools\": []}], "
                "\"summary\": \"总结\", \"riskLevel\": \"LOW|MEDIUM\"}\n"
                "NEED_CORRECTION 仅在确实有信息缺口时使用，最多追加 2 步。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "plan": state.get("plan"),
                "stepResults": step_results,
                "scratchpad": scratchpad,
            }, ensure_ascii=False)[:2000],
        },
    ]

    try:
        review_text = llm.chat(messages)
        review = _parse_json_response(review_text)
    except Exception:
        review = {"verdict": "PASS", "correctionSteps": [], "summary": "审查完成", "riskLevel": "LOW"}

    # ── 如果需要修正：追加步骤到 plan ──
    if review.get("verdict") == "NEED_CORRECTION" and review.get("correctionSteps"):
        plan = state.get("plan") or {}
        existing_steps = list(plan.get("steps") or [])
        plan["steps"] = existing_steps + review["correctionSteps"]
        return {**state, "plan": plan, "current_step_index": len(existing_steps)}

    # ── 构造最终结果 ──
    final_result = {
        "summary": review.get("summary", "分析完成"),
        "riskLevel": review.get("riskLevel", "LOW"),
        "stepResults": step_results,
        "scratchpad": scratchpad,
        "memoryContext": state.get("memory_context"),
    }

    # ── 检查是否需要 CRUD 审批（保存类变更） ──
    if _needs_crud_review(state):
        client.publish_event(
            _make_event(
                state,
                event_type="MUTATION_PROPOSED",
                status="WAITING_CRUD_REVIEW",
                draft=final_result,
                review_request={
                    "id": f"review-crud-{state['task_id']}",
                    "reviewType": "CRUD",
                    "proposal": {"summary": "保存 Agent 分析结果", "riskLevel": "MEDIUM"},
                },
            )
        )
        return {**state, "final_result": final_result, "pending_review": {"type": "CRUD"}}

    return {**state, "final_result": final_result}


def _finalize(state: PaeAgentState, client: JavaAgentGatewayClient) -> PaeAgentState:
    """Finalize 节点：回写 TASK_COMPLETED 事件到 Java。"""
    if state.get("status") == "FAILED":
        client.publish_event(
            _make_event(state, event_type="TASK_FAILED", status="FAILED",
                        final=state.get("final_result") or {"answer": state.get("error_message")})
        )
        return state

    client.publish_event(
        _make_event(state, event_type="TASK_COMPLETED", status="COMPLETED",
                    final=state.get("final_result") or {})
    )
    return {**state, "status": "COMPLETED"}


# ── 4.3 ReAct 子图节点 ───────────────────────────────────────────────────────

def _reasoner(state: ReactExecutorState) -> ReactExecutorState:
    """
    Reasoner 节点：LLM 根据当前上下文推理并输出 thought + action。

    输出格式（ReAct 经典）：
      Thought: 我需要做什么...
      Action: tool_name
      Action Input: {"param": "value"}

    或：
      Thought: 已有足够信息...
      FINISH: 总结内容
    """
    if state.get("status") == "FAILED":
        return state

    iteration = state.get("iteration", 0)
    if iteration >= state.get("max_iterations", 5):
        return {**state, "status": "FINISH", "thought": "到达最大迭代次数"}

    # 当前步骤允许的工具
    allowed_tools = [
        t for t in REACT_READ_TOOLS if t["name"] in state.get("allowed_tools", [])
    ]
    tools_desc = "\n".join(
        f"- {t['name']}: {t['description']}\n  参数: {json.dumps(t['parameters'], ensure_ascii=False)}"
        for t in allowed_tools
    ) if allowed_tools else "- 无可用工具"

    history = _format_message_history(state.get("messages") or [])

    llm = AgentLLMClient()
    messages = [
        {
            "role": "system",
            "content": (
                "你是学迹智配的 ReAct Agent 执行器。按 Thought → Action → Observation 循环推理。\n\n"
                f"当前任务步骤：{state.get('step_description', '执行分析')}\n\n"
                f"可用工具：\n{tools_desc}\n\n"
                "输出格式（严格遵循）：\n"
                "Thought: [你的中文推理]\n"
                "Action: tool_name\n"
                "Action Input: {\"param\": \"value\"}\n\n"
                "或当任务完成时：\n"
                "Thought: [你的中文总结]\n"
                "FINISH: [最终答案或JSON结果]\n\n"
                "规则：\n"
                "1. 每条 Thought 必须基于 Observation 推理，不能凭空猜测\n"
                "2. Action 只能从可用工具列表中选择\n"
                "3. Action Input 必须是合法 JSON\n"
                "4. 得到足够信息后立即 FINISH，不要无限循环\n"
                "5. 中文输出"
            ),
        },
        {"role": "user", "content": history or "请开始执行任务步骤。"},
    ]

    try:
        response = llm.chat(messages, temperature=0.2)
    except Exception as e:
        return {**state, "status": "FAILED", "error_message": f"Reasoner 调用失败: {e}"}

    thought, action_name, action_input = _parse_react_output(response)

    if action_name == "FINISH":
        return {
            **state,
            "thought": thought,
            "status": "FINISH",
            "step_result": {"answer": action_input},
        }

    if action_name:
        return {
            **state,
            "thought": thought,
            "action": {"toolName": action_name, "arguments": action_input},
            "iteration": iteration + 1,
        }

    # 模型未输出有效 Action，强制 FINISH
    return {
        **state,
        "thought": thought or "无法确定下一步操作",
        "status": "FINISH",
        "step_result": {"answer": thought or "执行结束"},
    }


def _actor(state: ReactExecutorState, client: JavaAgentGatewayClient) -> ReactExecutorState:
    """
    Actor 节点：调用 Java Read Tool Gateway 执行 Reasoner 选择的工具。

    这是 ReAct 的 "Act" 环节。
    """
    if state.get("status") in ("FINISH", "FAILED"):
        return state

    action = state.get("action") or {}
    tool_name = action.get("toolName")
    if not tool_name:
        return {**state, "status": "FAILED", "error_message": "Actor 缺少 toolName"}

    payload = {
        "taskId": state["task_id"],
        "toolCallId": f"tool-call-react-{uuid.uuid4().hex}",
        "toolName": tool_name,
        "arguments": action.get("arguments") or {},
    }

    try:
        result = client.execute_read_tool(payload)
    except Exception as e:
        return {
            **state,
            "observation": {"error": str(e), "toolName": tool_name},
            "status": "RUNNING",
        }

    return {
        **state,
        "observation": {
            "toolName": tool_name,
            "status": result.get("status"),
            "data": result.get("data") if isinstance(result.get("data"), dict) else {},
        },
        "status": "RUNNING",
    }


def _observer(state: ReactExecutorState) -> ReactExecutorState:
    """
    Observer 节点：处理工具执行结果，追加到消息历史。

    这是 ReAct 的 "Observe" 环节。将 Observation 格式化为 LLM 可读的文本，
    附加到 messages 末尾，供下一轮 reasoner 推理。
    """
    if state.get("status") == "FINISH":
        return state

    messages = list(state.get("messages") or [])

    # 记录本轮 Thought + Action
    if state.get("thought"):
        messages.append({"role": "assistant", "content": f"Thought: {state['thought']}"})

    action = state.get("action") or {}
    if action.get("toolName"):
        messages.append({
            "role": "assistant",
            "content": f"Action: {action['toolName']}\nAction Input: {json.dumps(action.get('arguments') or {}, ensure_ascii=False)}",
        })

    # 记录 Observation（脱敏：只保留摘要，不保存完整回答/evidence）
    observation = state.get("observation") or {}
    if observation:
        obs_summary = {
            "toolName": observation.get("toolName"),
            "status": observation.get("status"),
        }
        data = observation.get("data") or {}
        # 脱敏处理
        if data.get("answer"):
            obs_summary["answerPreview"] = str(data["answer"])[:300]
            obs_summary["answerLength"] = len(str(data["answer"]))
        if isinstance(data.get("evidences"), list):
            obs_summary["evidenceCount"] = len(data["evidences"])
        if isinstance(data.get("evidenceIds"), list):
            obs_summary["evidenceIds"] = data["evidenceIds"]
        if isinstance(data.get("results"), list):
            obs_summary["webResultCount"] = len(data["results"])
        if isinstance(data.get("expandedQueries"), list):
            obs_summary["expandedQueryCount"] = len(data["expandedQueries"])
        if isinstance(data.get("candidates"), list):
            obs_summary["candidateCount"] = len(data["candidates"])

        messages.append({
            "role": "user",
            "content": f"Observation: {json.dumps(obs_summary, ensure_ascii=False)}",
        })

    return {**state, "messages": messages}


# ── 4.4 路由函数 ─────────────────────────────────────────────────────────────

def _route_executor(state: PaeAgentState) -> Literal["executor_step", "reviewer"]:
    """判断是否所有 plan 步骤已执行完毕。"""
    if state.get("status") == "FAILED":
        return "reviewer"
    plan = state.get("plan") or {}
    steps = plan.get("steps") or []
    idx = state.get("current_step_index", 0)
    return "reviewer" if idx >= len(steps) else "executor_step"


def _route_reviewer(state: PaeAgentState) -> Literal["executor_step", "crud_pending", "finalize"]:
    """判断 reviewer 输出后的路由。"""
    pending = state.get("pending_review") or {}
    if pending.get("type") == "CRUD":
        return "crud_pending"
    plan = state.get("plan") or {}
    steps = plan.get("steps") or []
    idx = state.get("current_step_index", 0)
    if idx < len(steps):
        return "executor_step"  # 有修正步骤需要执行
    return "finalize"


def _route_react(state: ReactExecutorState) -> Literal["reasoner", "__end__"]:
    """ReAct 子图条件路由：继续推理 or 退出。"""
    if state.get("status") == "FINISH":
        return END
    if state.get("status") == "FAILED":
        return END
    iteration = state.get("iteration", 0)
    if iteration >= state.get("max_iterations", 5):
        return END
    return "reasoner"


# ── 4.5 响应解析 ─────────────────────────────────────────────────────────────

def _parse_react_output(text: str) -> tuple[str, str | None, dict[str, Any] | str]:
    """
    解析 ReAct 格式的输出。

    返回：(thought, action_name_or_FINISH, action_input_or_finish_answer)
    """
    thought = ""
    action_name = None
    action_input: dict[str, Any] | str = {}

    thought_match = __import__("re").search(r"Thought:\s*(.+?)(?:\n(?:Action|FINISH):|\Z)", text, __import__("re").DOTALL)
    if thought_match:
        thought = thought_match.group(1).strip()

    finish_match = __import__("re").search(r"FINISH:\s*(.+)", text, __import__("re").DOTALL)
    if finish_match:
        return thought, "FINISH", finish_match.group(1).strip()

    action_match = __import__("re").search(r"Action:\s*(\S+)\s*\nAction Input:\s*(\{.+\})", text, __import__("re").DOTALL)
    if action_match:
        action_name = action_match.group(1).strip()
        try:
            action_input = json.loads(action_match.group(2))
        except json.JSONDecodeError:
            action_input = {"raw": action_match.group(2).strip()}

    return thought, action_name, action_input


def _parse_json_response(text: str) -> dict[str, Any]:
    """从 LLM 响应中提取 JSON。"""
    import re
    text = text.strip()
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 尝试提取 code block 中的 JSON
    match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # 尝试提取第一个 { ... } 块
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return {"raw": text}


def _format_message_history(messages: list[dict[str, Any]]) -> str:
    """将 ReAct 消息历史格式化为 LLM 可读文本。"""
    return "\n\n".join(
        f"[{msg['role']}]\n{msg['content']}" for msg in messages[-6:]  # 只保留最近 6 条
    )


# ── 4.6 辅助函数 ─────────────────────────────────────────────────────────────

_render = None  # Stitch 模板渲染器，项目启动时注入

_react_graph_cache: dict[int, StateGraph] = {}


def _cached_react_graph(client) -> StateGraph:
    """缓存 ReAct 子图编译结果（同一 client 实例复用）。"""
    key = id(client)
    if key not in _react_graph_cache:
        _react_graph_cache[key] = build_react_executor_graph(client)
    return _react_graph_cache[key]


def _fallback_plan(goal: str) -> dict[str, Any]:
    """LLM 不可用时的降级计划。"""
    return {
        "title": f"{goal[:40]} 执行计划",
        "steps": [
            {
                "description": "检索用户知识库中的相关学习证据",
                "allowedTools": ["rag_query_probe_non_persistent", "agent_memory_retriever"],
                "expectedOutput": "相关 evidence 列表",
            },
            {
                "description": "分析 evidence 覆盖情况和缺口",
                "allowedTools": ["retrieval_coverage_probe"],
                "expectedOutput": "覆盖诊断报告",
            },
        ],
        "riskLevel": "LOW",
    }


def _needs_crud_review(state: PaeAgentState) -> bool:
    """判断是否需要 CRUD 审批。"""
    task_input = state.get("task_input") or {}
    if bool(task_input.get("saveDraft")):
        return True
    tool_hints = task_input.get("toolHints")
    save_tools = {"resume_revision_save", "jd_learning_plan_save"}
    return isinstance(tool_hints, list) and any(t in save_tools for t in tool_hints)


def _make_event(
    state: PaeAgentState,
    *,
    event_type: str,
    status: str,
    draft: dict[str, Any] | None = None,
    final: dict[str, Any] | None = None,
    review_request: dict[str, Any] | None = None,
):
    """构造 AgentTaskEvent。"""
    from app.schemas.agent import AgentTaskEvent
    return AgentTaskEvent(
        eventType=event_type,
        status=status,
        pythonThreadId=state["thread_id"],
        draft=draft or {},
        final=final,
        reviewRequest=review_request,
        errorCode=state.get("error_code"),
        errorMessage=state.get("error_message"),
    )


"""
================================================================================
五、与现有系统的整合方式
================================================================================

1. 入口改造（app/api/agent.py）：

   POST /internal/agent/tasks
   ├── taskType == "pure_read_query" → 继续走 read_only_graph（不变）
   ├── taskType == "planning_task"   → 走新 PAE Graph
   └── taskType == "mutation_task"   → 预留

   resume 接口同样改造，CRUD 审批通过后走 PAE 图的 mutation 分支。

2. 图选择（替换 run_read_only_agent / start_planning_agent）：

   def run_pae_agent(request, client):
       graph = build_pae_graph(client)
       initial = { ... PaeAgentState 初始值 ... }
       result = graph.invoke(initial)
       return AgentTaskStartResponse(...)

   Human-in-the-Loop 断点：
   - planner 产出 plan → graph.invoke() 返回时 status="WAITING_PLAN_REVIEW"
   - Java 侧收到 REVIEW_REQUESTED 事件，前端展示审批卡片
   - 用户审批后 Java 调 /resume，Python 以 plan_approved=True 再次 invoke
   - 同理 reviewer 产出 CRUD 审批 → WAITING_CRUD_REVIEW → 审批后再次 invoke

3. Checkpoint 支持（持久化状态）：

   LangGraph 自带 SqliteSaver / PostgresSaver checkpoint 能力，
   PAE 图可配置 checkpointer，实现：
   - 审批中断后恢复（thread_id 不变）
   - 异常重试
   - 审计追溯（每个节点执行前后的 state 快照）

4. Java Tool Gateway 新增工具注册：

   在 AgentToolGatewayServiceImpl.executeReadToolData() 的 switch 中
   新增 ReAct 可能调用的工具（如 material_evidence_reader 等），
   保持所有只读工具通过 Java 鉴权。

5. 前端改造：

   - 新增计划审批卡片（展示 plan.steps）
   - 实时展示 ReAct 循环（Thought / Action / Observation 流式推送）
   - 工具调用过程可视化（类似 ChatGPT 的工具调用展示）


================================================================================
六、ReAct 循环的关键约束
================================================================================

1. 最大迭代次数：5 轮（防止 LLM 无限循环 + 控制成本）
2. 工具白名单：每个 plan step 通过 allowedTools 限定可调工具
3. 写操作不在 ReAct 内：变更类工具（mutation）通过 Human-in-the-Loop 审批
4. Observation 脱敏：Actor 返回的完整 answer/evidence 不存入消息历史，
   只保留摘要（长度、数量、ID 列表），防止 token 爆炸
5. 降级兜底：LLM 不可用时 a) Planner 降级为静态计划 b) Reasoner 降级为直接 FINISH
6. 子图复用：ReAct 图编译一次后缓存，每个 plan step 创建新的 subgraph.invoke()


================================================================================
七、状态流转示意图
================================================================================

┌──────────────────────────────────────────────────────────────────┐
│                        PAE 主图                                   │
│                                                                  │
│  memory_prefetch ──→ planner ──→ [plan_approved?]                │
│                                       │ YES                      │
│                                       ▼                          │
│                              ┌─→ executor_step ──┐               │
│                              │        │            │              │
│                              │   ┌────▼────┐       │              │
│                              │   │ ReAct 子图│      │              │
│                              │   │          │      │              │
│                              │   │ reasoner │      │              │
│                              │   │    │     │      │              │
│                              │   │    ▼     │      │              │
│                              │   │  actor   │      │              │
│                              │   │    │     │      │              │
│                              │   │    ▼     │      │              │
│                              │   │ observer │      │              │
│                              │   │    │     │      │              │
│                              │   │    ▼     │      │              │
│                              │   │ router──┼──────┤              │
│                              │   └─────────┘      │              │
│                              │        │            │              │
│                              └──[还有步骤?]────────┘              │
│                                       │ 否                       │
│                                       ▼                          │
│                                  reviewer                        │
│                                  /   │   \                       │
│                         修正回exec  CRUD审批  finalize → END     │
└──────────────────────────────────────────────────────────────────┘


================================================================================
八、与当前实现的对比
================================================================================

当前                              →  设计目标
─────────────────────────────────────────────────────
工具选择：硬编码 if toolHints     →  LLM 推理选择（Reasoner）
执行序列：固定顺序 RAG→覆盖→候选  →  LLM 动态规划（Planner）
循环能力：无                      →  ReAct 闭环（最多 5 轮）
步骤审查：无                      →  Reviewer 自我修正
错误恢复：状态短路                 →  条件边路由 + 修正步骤
Planning 图：手工状态机           →  统一 PAE LangGraph
LLM 参与度：仅 RAG 回答生成      →  Agent 层全流程参与

================================================================================
"""
