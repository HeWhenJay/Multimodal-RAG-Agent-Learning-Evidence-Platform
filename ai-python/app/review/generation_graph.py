"""复习卡片多轮生成使用的独立 PAE/ReAct LangGraph。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph


REVIEW_GRAPH_RECURSION_LIMIT = 999
DEFAULT_REVIEW_GRAPH_MAX_ATTEMPTS = 6
MAX_REVIEW_GRAPH_MODEL_ATTEMPTS = 20


class ReviewGenerationState(TypedDict, total=False):
    """规划、生成、观察、修复和人工终态共享的图状态。"""

    plan: dict[str, Any]
    attempt: int
    max_attempts: int
    candidate: dict[str, Any]
    result: Any
    status: str
    attempt_feedback: list[str]
    repair_feedback: list[str]
    feedback_history: list[str]


@dataclass(frozen=True)
class ReviewGenerationOutcome:
    """成功图执行的业务结果与审计信息。"""

    result: Any
    attempts: int
    quality_feedback: tuple[str, ...]


class ReviewManualReviewRequired(RuntimeError):
    """自动修复耗尽后需要用户补充信息或人工检查。"""

    def __init__(self, message: str, *, attempts: int, quality_feedback: list[str] | tuple[str, ...]) -> None:
        super().__init__(message)
        self.attempts = max(0, int(attempts))
        self.quality_feedback = tuple(unique_feedback(quality_feedback))


Actor = Callable[[int, list[str]], dict[str, Any]]
Observer = Callable[[dict[str, Any]], Any]


def configured_review_graph_max_attempts() -> int:
    """读取真实模型尝试预算，并限制在 1 到 20 次。"""
    raw = os.getenv("REVIEW_GENERATION_MAX_ATTEMPTS", str(DEFAULT_REVIEW_GRAPH_MAX_ATTEMPTS))
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = DEFAULT_REVIEW_GRAPH_MAX_ATTEMPTS
    return max(1, min(MAX_REVIEW_GRAPH_MODEL_ATTEMPTS, parsed))


def run_review_generation_graph(
    *,
    actor: Actor,
    observer: Observer,
    plan: dict[str, Any],
    max_attempts: int | None = None,
) -> ReviewGenerationOutcome:
    """执行复习生成图，把质量门禁诊断反馈给下一次模型生成。"""
    bounded_attempts = max_attempts or configured_review_graph_max_attempts()
    bounded_attempts = max(1, min(MAX_REVIEW_GRAPH_MODEL_ATTEMPTS, int(bounded_attempts)))
    initial: ReviewGenerationState = {
        "plan": dict(plan),
        "attempt": 0,
        "max_attempts": bounded_attempts,
        "status": "PLANNING",
        "attempt_feedback": [],
        "repair_feedback": [],
        "feedback_history": [],
    }
    attempts_seen = 0

    def tracked_actor(attempt: int, feedback: list[str]) -> dict[str, Any]:
        """记录递归异常发生前实际进入 actor 的尝试次数。"""
        nonlocal attempts_seen
        attempts_seen = max(attempts_seen, attempt)
        return actor(attempt, feedback)

    try:
        state = build_review_generation_graph(tracked_actor, observer).invoke(
            initial,
            {"recursion_limit": REVIEW_GRAPH_RECURSION_LIMIT},
        )
    except GraphRecursionError as exc:
        raise ReviewManualReviewRequired(
            "复习生成图达到最大递归深度，需要人工处理",
            attempts=attempts_seen,
            quality_feedback=["LangGraph 达到 recursion_limit=999，自动循环已停止"],
        ) from exc

    attempts = int(state.get("attempt") or 0)
    history = unique_feedback(state.get("feedback_history") or [])
    if state.get("status") != "COMPLETED" or state.get("result") is None:
        raise ReviewManualReviewRequired(
            f"自动修复 {attempts} 次后仍未通过复习卡片质量门禁",
            attempts=attempts,
            quality_feedback=history or ["模型结果未达到发布条件"],
        )
    return ReviewGenerationOutcome(
        result=state["result"],
        attempts=attempts,
        quality_feedback=tuple(history),
    )


def build_review_generation_graph(actor: Actor, observer: Observer):
    """构建规划、生成、观察、修复与人工终态组成的复习图。"""
    workflow = StateGraph(ReviewGenerationState)
    workflow.add_node("planner", planner_node)
    workflow.add_node("actor", lambda state: actor_node(state, actor))
    workflow.add_node("observer", lambda state: observer_node(state, observer))
    workflow.add_node("repair", repair_node)
    workflow.add_node("human_review", human_review_node)

    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "actor")
    workflow.add_edge("actor", "observer")
    workflow.add_conditional_edges(
        "observer",
        route_after_observer,
        {"complete": END, "repair": "repair"},
    )
    workflow.add_conditional_edges(
        "repair",
        route_after_repair,
        {"actor": "actor", "human_review": "human_review"},
    )
    workflow.add_edge("human_review", END)
    return workflow.compile()


def planner_node(state: ReviewGenerationState) -> ReviewGenerationState:
    """确认本轮目标和模型调用预算。"""
    plan = dict(state.get("plan") or {})
    plan["completionCriteria"] = [
        "资料摘要通过质量校验",
        "至少一张卡片通过问题、提示和 evidence 门禁",
        "结构化原始问题达到完整覆盖要求",
    ]
    return {"plan": plan, "status": "GENERATING"}


def actor_node(state: ReviewGenerationState, actor: Actor) -> ReviewGenerationState:
    """调用一次 DeepSeek，并把请求或解析失败转换为观察反馈。"""
    attempt = int(state.get("attempt") or 0) + 1
    feedback = list(state.get("repair_feedback") or [])
    try:
        candidate = actor(attempt, feedback)
        return {
            "attempt": attempt,
            "candidate": candidate,
            "attempt_feedback": [],
            "status": "OBSERVING",
        }
    except Exception as exc:  # noqa: BLE001 - 图必须把模型错误收敛为可审计状态。
        return {
            "attempt": attempt,
            "candidate": {},
            "attempt_feedback": diagnostics_from_exception(exc),
            "status": "REJECTED",
        }


def observer_node(state: ReviewGenerationState, observer: Observer) -> ReviewGenerationState:
    """执行确定性质量门禁，成功时结束，失败时提供逐项诊断。"""
    actor_feedback = list(state.get("attempt_feedback") or [])
    if actor_feedback:
        return {"status": "REJECTED", "attempt_feedback": actor_feedback}
    try:
        result = observer(dict(state.get("candidate") or {}))
        return {"result": result, "status": "COMPLETED", "attempt_feedback": []}
    except Exception as exc:  # noqa: BLE001 - 门禁异常必须进入修复循环。
        return {
            "status": "REJECTED",
            "attempt_feedback": diagnostics_from_exception(exc),
        }


def repair_node(state: ReviewGenerationState) -> ReviewGenerationState:
    """合并当前失败原因，供下一次 Prompt 逐条修复。"""
    attempt = int(state.get("attempt") or 0)
    current = unique_feedback(state.get("attempt_feedback") or [])
    labeled = [f"第 {attempt} 次：{item}" for item in current]
    history = unique_feedback([*(state.get("feedback_history") or []), *labeled])
    status = "NEEDS_REVIEW" if attempt >= int(state.get("max_attempts") or 1) else "REPAIRING"
    return {
        "feedback_history": history,
        "repair_feedback": current,
        "status": status,
    }


def human_review_node(state: ReviewGenerationState) -> ReviewGenerationState:
    """模型尝试预算耗尽后进入稳定人工处理终态。"""
    return {"status": "NEEDS_REVIEW"}


def route_after_observer(state: ReviewGenerationState) -> str:
    """观察通过即结束，否则进入修复。"""
    return "complete" if state.get("status") == "COMPLETED" else "repair"


def route_after_repair(state: ReviewGenerationState) -> str:
    """模型预算耗尽后不再继续调用 DeepSeek。"""
    return "human_review" if state.get("status") == "NEEDS_REVIEW" else "actor"


def diagnostics_from_exception(exc: Exception) -> list[str]:
    """从质量异常提取诊断，普通异常仅暴露安全的中文类型说明。"""
    raw = getattr(exc, "diagnostics", None)
    if isinstance(raw, (list, tuple)):
        diagnostics = unique_feedback(str(item) for item in raw)
        if diagnostics:
            return diagnostics
    message = " ".join(str(exc).split()).strip()
    return [message[:500] if message else f"{type(exc).__name__} 导致本次生成失败"]


def unique_feedback(items: Any) -> list[str]:
    """按出现顺序去重并限制持久化反馈规模。"""
    result: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        normalized = " ".join(str(item).split()).strip()[:500]
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= 80:
            break
    return result
