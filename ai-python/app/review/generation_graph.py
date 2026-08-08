"""复习卡片多轮生成使用的独立 PAE/ReAct LangGraph。"""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Callable, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph


REVIEW_GRAPH_RECURSION_LIMIT = 999
DEFAULT_REVIEW_GRAPH_MAX_ATTEMPTS = 8
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
    curator_context: dict[str, Any]


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


Curator = Callable[[], dict[str, Any]]
Actor = Callable[[int, list[str], dict[str, Any], dict[str, Any]], dict[str, Any]]
Observer = Callable[[dict[str, Any], dict[str, Any]], Any]
ProgressCallback = Callable[[dict[str, Any]], None]


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
    curator: Curator | None = None,
    max_attempts: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> ReviewGenerationOutcome:
    """执行复习生成图，把质量门禁诊断反馈给下一次模型生成。"""
    bounded_attempts = max_attempts or configured_review_graph_max_attempts()
    bounded_attempts = max(1, min(MAX_REVIEW_GRAPH_MODEL_ATTEMPTS, int(bounded_attempts)))
    has_curator = curator is not None
    total_steps = 5 if has_curator else 4
    initial: ReviewGenerationState = {
        "plan": dict(plan),
        "attempt": 0,
        "max_attempts": bounded_attempts,
        "status": "PLANNING",
        "attempt_feedback": [],
        "repair_feedback": [],
        "feedback_history": [],
        "curator_context": {},
    }
    attempts_seen = 0

    def tracked_actor(
        attempt: int,
        feedback: list[str],
        previous_candidate: dict[str, Any],
        curator_context: dict[str, Any],
    ) -> dict[str, Any]:
        """记录递归异常发生前实际进入 actor 的尝试次数。"""
        nonlocal attempts_seen
        attempts_seen = max(attempts_seen, attempt)
        return actor(attempt, feedback, previous_candidate, curator_context)

    try:
        state = build_review_generation_graph(
            tracked_actor,
            observer,
            curator=curator,
            on_progress=on_progress,
        ).invoke(
            initial,
            {"recursion_limit": REVIEW_GRAPH_RECURSION_LIMIT},
        )
    except GraphRecursionError as exc:
        emit_progress(
            on_progress,
            stageCode="review.human_review",
            stageLabel="等待人工处理",
            message="生成图达到递归上限，已停止自动循环",
            status="NEEDS_REVIEW",
            currentStep=total_steps,
            totalSteps=total_steps,
            percent=100,
            attempt=attempts_seen,
            maxAttempts=bounded_attempts,
        )
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


def build_review_generation_graph(
    actor: Actor,
    observer: Observer,
    *,
    curator: Curator | None = None,
    on_progress: ProgressCallback | None = None,
):
    """构建知识发现、生成、观察、修复与人工终态组成的复习图。"""
    has_curator = curator is not None
    total_steps = 5 if has_curator else 4
    workflow = StateGraph(ReviewGenerationState)
    workflow.add_node(
        "planner",
        lambda state: planner_progress_node(state, on_progress, has_curator=has_curator),
    )
    if curator is not None:
        workflow.add_node(
            "curator",
            lambda state: curator_progress_node(state, curator, on_progress, total_steps=total_steps),
        )
    workflow.add_node(
        "actor",
        lambda state: actor_progress_node(
            state,
            actor,
            on_progress,
            total_steps=total_steps,
            has_curator=has_curator,
        ),
    )
    workflow.add_node(
        "observer",
        lambda state: observer_progress_node(
            state,
            observer,
            on_progress,
            total_steps=total_steps,
            has_curator=has_curator,
        ),
    )
    workflow.add_node(
        "repair",
        lambda state: repair_progress_node(
            state,
            on_progress,
            total_steps=total_steps,
            has_curator=has_curator,
        ),
    )
    workflow.add_node(
        "human_review",
        lambda state: human_review_progress_node(state, on_progress, total_steps=total_steps),
    )

    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "curator" if curator is not None else "actor")
    if curator is not None:
        workflow.add_edge("curator", "actor")
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


def planner_progress_node(
    state: ReviewGenerationState,
    on_progress: ProgressCallback | None,
    *,
    has_curator: bool = False,
) -> ReviewGenerationState:
    """规划目标并上报图已开始执行。"""
    result = planner_node(state)
    plan = dict(state.get("plan") or {})
    emit_progress(
        on_progress,
        stageCode="review.planner",
        stageLabel="规划生成",
        message="正在确认资料结构、卡片目标和质量标准",
        status="RUNNING",
        currentStep=1,
        totalSteps=5 if has_curator else 4,
        percent=8 if has_curator else 18,
        attempt=0,
        maxAttempts=int(state.get("max_attempts") or 1),
        detail=(
            f"识别到 {int(plan.get('structuredQuestionCount') or 0)} 个原始问题，"
            f"本轮最多生成 {int(plan.get('maxCards') or 0)} 张卡片"
        ),
    )
    return result


def curator_progress_node(
    state: ReviewGenerationState,
    curator: Curator,
    on_progress: ProgressCallback | None,
    *,
    total_steps: int,
) -> ReviewGenerationState:
    """执行一次 LangExtract 长文知识发现，并公开候选数量和降级状态。"""
    model_name = current_model_name(state)
    emit_progress(
        on_progress,
        stageCode="review.curator",
        stageLabel="LangExtract 知识发现",
        message="正在并发扫描完整资料并定位候选知识单元",
        status="RUNNING",
        currentStep=2,
        totalSteps=total_steps,
        percent=14,
        attempt=0,
        maxAttempts=int(state.get("max_attempts") or 1),
        detail="同一轮最多并发处理 8 个文本块，多轮结果将按 topic 去重并回指 evidenceId",
    )
    result = curator_node(state, curator)
    context = dict(result.get("curator_context") or {})
    completed = context.get("status") == "COMPLETED"
    curator_model_name = str(context.get("llmModel") or model_name).strip() or model_name
    emit_progress(
        on_progress,
        stageCode="review.curator",
        stageLabel="LangExtract 知识发现" if completed else "LangExtract 降级",
        message=(
            f"知识发现完成（{curator_model_name}）：保留 {int(context.get('selectedKnowledgeUnitCount') or 0)} 个候选单元"
            if completed
            else f"LangExtract 本次未完成，已保留原有 {model_name} 生成链路继续处理"
        ),
        status="COMPLETED" if completed else "DEGRADED",
        currentStep=2,
        totalSteps=total_steps,
        percent=30,
        attempt=0,
        maxAttempts=int(state.get("max_attempts") or 1),
        detail=(
            f"原始 {int(context.get('rawCandidateCount') or 0)} 个，精确定位后 "
            f"{int(context.get('acceptedCandidateCount') or 0)} 个，模型请求 "
            f"{int(context.get('requestCount') or 0)} 次"
            if completed
            else str(context.get("error") or "知识发现不可用")[:500]
        ),
    )
    return result


def actor_progress_node(
    state: ReviewGenerationState,
    actor: Actor,
    on_progress: ProgressCallback | None,
    *,
    total_steps: int = 4,
    has_curator: bool = False,
) -> ReviewGenerationState:
    """在模型调用前上报尝试轮次，避免长请求期间界面静默。"""
    attempt = int(state.get("attempt") or 0) + 1
    max_attempts = int(state.get("max_attempts") or 1)
    feedback = unique_feedback(state.get("repair_feedback") or [])
    model_name = current_model_name(state)
    emit_progress(
        on_progress,
        stageCode="review.actor",
        stageLabel=f"{model_name} 生成",
        message=f"正在请求 {model_name} 生成第 {attempt}/{max_attempts} 版复习卡片",
        status="RUNNING",
        currentStep=3 if has_curator else 2,
        totalSteps=total_steps,
        percent=attempt_percent(attempt, max_attempts, phase=0, start_percent=32 if has_curator else 25),
        attempt=attempt,
        maxAttempts=max_attempts,
        detail=(
            f"本轮将修复：{'；'.join(feedback[:2])}"
            if feedback
            else (
                f"正在结合 {int((state.get('curator_context') or {}).get('selectedKnowledgeUnitCount') or 0)} "
                "个 LangExtract 候选生成摘要、问题、答案和提示"
                if has_curator
                else "正在基于清洗后的 evidence 生成摘要、问题、答案和提示"
            )
        ),
    )
    return actor_node(state, actor)


def observer_progress_node(
    state: ReviewGenerationState,
    observer: Observer,
    on_progress: ProgressCallback | None,
    *,
    total_steps: int = 4,
    has_curator: bool = False,
) -> ReviewGenerationState:
    """在质量门禁前后上报校验与保存阶段。"""
    attempt = int(state.get("attempt") or 0)
    max_attempts = int(state.get("max_attempts") or 1)
    emit_progress(
        on_progress,
        stageCode="review.observer",
        stageLabel="质量校验",
        message=f"正在校验第 {attempt}/{max_attempts} 版卡片的完整性与 evidence 忠实度",
        status="RUNNING",
        currentStep=4 if has_curator else 3,
        totalSteps=total_steps,
        percent=attempt_percent(attempt, max_attempts, phase=1, start_percent=32 if has_curator else 25),
        attempt=attempt,
        maxAttempts=max_attempts,
        detail="检查摘要、卡面回忆提示、evidenceId、逐论断忠实度和结构化知识覆盖率",
    )
    result = observer_node(state, observer)
    if result.get("status") == "COMPLETED":
        emit_progress(
            on_progress,
            stageCode="review.persist",
            stageLabel="保存卡片",
            message="质量门禁已通过，正在保存卡片并初始化 FSRS",
            status="RUNNING",
            currentStep=total_steps,
            totalSteps=total_steps,
            percent=94,
            attempt=attempt,
            maxAttempts=max_attempts,
        )
    return result


def repair_progress_node(
    state: ReviewGenerationState,
    on_progress: ProgressCallback | None,
    *,
    total_steps: int = 4,
    has_curator: bool = False,
) -> ReviewGenerationState:
    """整理质量反馈并说明是否继续自动修复。"""
    result = repair_node(state)
    attempt = int(state.get("attempt") or 0)
    max_attempts = int(state.get("max_attempts") or 1)
    feedback = unique_feedback(state.get("attempt_feedback") or [])
    exhausted = result.get("status") == "NEEDS_REVIEW"
    emit_progress(
        on_progress,
        stageCode="review.repair",
        stageLabel="自动修复" if not exhausted else "自动修复已耗尽",
        message=(
            f"第 {attempt}/{max_attempts} 版未通过，正在把质量反馈送入下一轮"
            if not exhausted
            else f"第 {attempt}/{max_attempts} 版仍未通过，准备转入人工处理"
        ),
        status="RUNNING" if not exhausted else "NEEDS_REVIEW",
        currentStep=4 if has_curator else 3,
        totalSteps=total_steps,
        percent=attempt_percent(attempt, max_attempts, phase=2, start_percent=32 if has_curator else 25),
        attempt=attempt,
        maxAttempts=max_attempts,
        detail="；".join(feedback[:3]) or "模型结果未达到发布条件",
    )
    return result


def human_review_progress_node(
    state: ReviewGenerationState,
    on_progress: ProgressCallback | None,
    *,
    total_steps: int = 4,
) -> ReviewGenerationState:
    """自动预算耗尽时上报稳定人工终态。"""
    result = human_review_node(state)
    attempt = int(state.get("attempt") or 0)
    max_attempts = int(state.get("max_attempts") or 1)
    emit_progress(
        on_progress,
        stageCode="review.human_review",
        stageLabel="等待人工处理",
        message=f"自动修复 {attempt} 次后仍未通过，请补充说明后重新生成",
        status="NEEDS_REVIEW",
        currentStep=total_steps,
        totalSteps=total_steps,
        percent=100,
        attempt=attempt,
        maxAttempts=max_attempts,
    )
    return result


def attempt_percent(attempt: int, max_attempts: int, *, phase: int, start_percent: int = 25) -> int:
    """按轮次和节点位置提供不会在下一轮回退的进度。"""
    bounded_max = max(1, max_attempts)
    bounded_attempt = max(1, min(attempt, bounded_max))
    bounded_phase = max(0, min(2, phase))
    completed_units = ((bounded_attempt - 1) * 3) + bounded_phase
    bounded_start = max(0, min(80, start_percent))
    return min(90, bounded_start + round((completed_units / (bounded_max * 3)) * (90 - bounded_start)))


def emit_progress(
    callback: ProgressCallback | None,
    **event: Any,
) -> None:
    """在配置回调时同步发送一条结构化阶段事件。"""
    if callback is not None:
        callback(dict(event))


def planner_node(state: ReviewGenerationState) -> ReviewGenerationState:
    """确认本轮目标和模型调用预算。"""
    plan = dict(state.get("plan") or {})
    plan["completionCriteria"] = [
        "资料摘要通过质量校验",
        "至少一张卡片通过卡面回忆提示、hint 和 evidence 门禁",
        "结构化原始问题达到完整覆盖要求",
    ]
    return {"plan": plan, "status": "GENERATING"}


def actor_node(state: ReviewGenerationState, actor: Actor) -> ReviewGenerationState:
    """调用一次复习模型，并把上一版候选交给模型做定向修复。"""
    attempt = int(state.get("attempt") or 0) + 1
    feedback = list(state.get("repair_feedback") or [])
    previous_candidate = dict(state.get("candidate") or {})
    curator_context = dict(state.get("curator_context") or {})
    try:
        candidate = actor(attempt, feedback, previous_candidate, curator_context)
        return {
            "attempt": attempt,
            "candidate": candidate,
            "attempt_feedback": [],
            "status": "OBSERVING",
        }
    except Exception as exc:  # noqa: BLE001 - 图必须把模型错误收敛为可审计状态。
        return {
            "attempt": attempt,
            "candidate": previous_candidate,
            "attempt_feedback": diagnostics_from_exception(exc),
            "status": "REJECTED",
        }


def observer_node(state: ReviewGenerationState, observer: Observer) -> ReviewGenerationState:
    """执行确定性质量门禁，成功时结束，失败时提供逐项诊断。"""
    actor_feedback = list(state.get("attempt_feedback") or [])
    if actor_feedback:
        return {"status": "REJECTED", "attempt_feedback": actor_feedback}
    try:
        result = observer(
            dict(state.get("candidate") or {}),
            dict(state.get("curator_context") or {}),
        )
        return {"result": result, "status": "COMPLETED", "attempt_feedback": []}
    except Exception as exc:  # noqa: BLE001 - 门禁异常必须进入修复循环。
        return {
            "status": "REJECTED",
            "attempt_feedback": diagnostics_from_exception(exc),
        }


def curator_node(state: ReviewGenerationState, curator: Curator) -> ReviewGenerationState:
    """调用一次候选发现；失败只标记降级，不消耗复习模型卡片修复次数。"""
    try:
        context = dict(curator() or {})
        context.setdefault("status", "COMPLETED")
    except Exception as exc:  # noqa: BLE001 - Curator 不可用时保留原有生成能力。
        context = {
            "status": "FAILED",
            "knowledgeUnits": [],
            "selectedKnowledgeUnitCount": 0,
            "error": "；".join(diagnostics_from_exception(exc)),
        }
    plan = dict(state.get("plan") or {})
    unit_count = int(context.get("selectedKnowledgeUnitCount") or 0)
    plan["langExtractStatus"] = context.get("status")
    plan["curatorKnowledgeUnitCount"] = unit_count
    if unit_count > 0:
        plan["maxCards"] = min(32, max(int(plan.get("maxCards") or 0), unit_count))
    return {"plan": plan, "curator_context": context, "status": "GENERATING"}


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


def current_model_name(state: ReviewGenerationState) -> str:
    """从生成计划读取当前实际模型，进度和降级说明不得伪装成固定供应商。"""
    model_name = str((state.get("plan") or {}).get("llmModel") or "gpt-5.6-terra").strip()
    return model_name or "gpt-5.6-terra"


def route_after_repair(state: ReviewGenerationState) -> str:
    """模型预算耗尽后不再继续调用复习模型。"""
    return "human_review" if state.get("status") == "NEEDS_REVIEW" else "actor"


def diagnostics_from_exception(exc: Exception) -> list[str]:
    """从质量异常提取诊断，普通异常仅暴露安全的中文类型说明。"""
    raw = getattr(exc, "diagnostics", None)
    if isinstance(raw, (list, tuple)):
        diagnostics = unique_feedback(str(item) for item in raw)
        if diagnostics:
            return diagnostics
    message = " ".join(str(exc).split()).strip()
    if message.lower() in {"connection error", "connection error."}:
        cause = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
        cause_message = " ".join(str(cause).split()).strip() if cause else ""
        detail = f"{type(exc).__name__}: {message}"
        if cause_message:
            detail += f"；底层原因：{type(cause).__name__}: {cause_message}"
        return [detail[:500]]
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
