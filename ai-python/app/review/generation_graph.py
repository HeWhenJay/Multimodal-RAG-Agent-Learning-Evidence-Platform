"""复习卡片多轮生成使用的独立 PAE/ReAct LangGraph。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
from typing import Any, Callable, TypedDict

from langgraph.errors import GraphRecursionError
from langgraph.graph import END, StateGraph


REVIEW_GRAPH_RECURSION_LIMIT = 999
DEFAULT_REVIEW_GRAPH_MAX_ATTEMPTS = 8
MAX_REVIEW_GRAPH_MODEL_ATTEMPTS = 20
DEFAULT_REVIEW_GRAPH_MAX_MERGE_ROUNDS = 4
MAX_REVIEW_GRAPH_MERGE_ROUNDS = 12


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
    merge_plan: dict[str, Any]
    merge_round: int
    max_merge_rounds: int
    candidate_fingerprints: list[str]
    last_valid_candidate: dict[str, Any]
    last_valid_result: Any


@dataclass(frozen=True)
class ReviewGenerationOutcome:
    """成功图执行的业务结果与审计信息。"""

    result: Any
    attempts: int
    quality_feedback: tuple[str, ...]


class ReviewManualReviewRequired(RuntimeError):
    """自动修复耗尽后需要用户补充信息或人工检查。"""

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        quality_feedback: list[str] | tuple[str, ...],
        last_valid_candidate: dict[str, Any] | None = None,
        last_valid_result: Any = None,
    ) -> None:
        super().__init__(message)
        self.attempts = max(0, int(attempts))
        self.quality_feedback = tuple(unique_feedback(quality_feedback))
        self.last_valid_candidate = dict(last_valid_candidate or {})
        self.last_valid_result = last_valid_result


Curator = Callable[[], dict[str, Any]]
Actor = Callable[[int, list[str], dict[str, Any], dict[str, Any]], dict[str, Any]]
Observer = Callable[[dict[str, Any], dict[str, Any]], Any]
MultiCardObserver = Callable[[dict[str, Any], dict[str, Any], int], dict[str, Any]]
MergeRepair = Callable[[dict[str, Any], dict[str, Any], dict[str, Any], int], dict[str, Any]]
ProgressCallback = Callable[[dict[str, Any]], None]


def configured_review_graph_max_attempts() -> int:
    """读取真实模型尝试预算，并限制在 1 到 20 次。"""
    raw = os.getenv("REVIEW_GENERATION_MAX_ATTEMPTS", str(DEFAULT_REVIEW_GRAPH_MAX_ATTEMPTS))
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = DEFAULT_REVIEW_GRAPH_MAX_ATTEMPTS
    return max(1, min(MAX_REVIEW_GRAPH_MODEL_ATTEMPTS, parsed))


def configured_review_graph_max_merge_rounds() -> int:
    """读取多卡片合并预算，并限制在 1 到 12 轮。"""
    raw = os.getenv(
        "REVIEW_GENERATION_MAX_MERGE_ROUNDS",
        str(DEFAULT_REVIEW_GRAPH_MAX_MERGE_ROUNDS),
    )
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = DEFAULT_REVIEW_GRAPH_MAX_MERGE_ROUNDS
    return max(1, min(MAX_REVIEW_GRAPH_MERGE_ROUNDS, parsed))


def run_review_generation_graph(
    *,
    actor: Actor,
    observer: Observer,
    plan: dict[str, Any],
    curator: Curator | None = None,
    multi_card_observer: MultiCardObserver | None = None,
    merge_repair: MergeRepair | None = None,
    max_attempts: int | None = None,
    max_merge_rounds: int | None = None,
    on_progress: ProgressCallback | None = None,
) -> ReviewGenerationOutcome:
    """执行复习生成图，把质量门禁诊断反馈给下一次模型生成。"""
    bounded_attempts = max_attempts or configured_review_graph_max_attempts()
    bounded_attempts = max(1, min(MAX_REVIEW_GRAPH_MODEL_ATTEMPTS, int(bounded_attempts)))
    bounded_merge_rounds = max_merge_rounds or configured_review_graph_max_merge_rounds()
    bounded_merge_rounds = max(1, min(MAX_REVIEW_GRAPH_MERGE_ROUNDS, int(bounded_merge_rounds)))
    has_curator = curator is not None
    total_steps = 7 if has_curator else 6
    initial: ReviewGenerationState = {
        "plan": dict(plan),
        "attempt": 0,
        "max_attempts": bounded_attempts,
        "status": "PLANNING",
        "attempt_feedback": [],
        "repair_feedback": [],
        "feedback_history": [],
        "curator_context": {},
        "merge_plan": {"passed": True, "mergeGroups": []},
        "merge_round": 0,
        "max_merge_rounds": bounded_merge_rounds,
        "candidate_fingerprints": [],
        "last_valid_candidate": {},
        "last_valid_result": None,
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
            multi_card_observer=multi_card_observer,
            merge_repair=merge_repair,
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
            mergeRound=0,
            maxMergeRounds=bounded_merge_rounds,
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
            failure_message(state, attempts),
            attempts=attempts,
            quality_feedback=history or ["模型结果未达到发布条件"],
            last_valid_candidate=dict(state.get("last_valid_candidate") or {}),
            last_valid_result=state.get("last_valid_result"),
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
    multi_card_observer: MultiCardObserver | None = None,
    merge_repair: MergeRepair | None = None,
    on_progress: ProgressCallback | None = None,
):
    """构建单卡门禁、多卡审查和定向合并循环组成的复习图。"""
    has_curator = curator is not None
    total_steps = 7 if has_curator else 6
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
        "multi_card_observer",
        lambda state: multi_card_observer_progress_node(
            state,
            multi_card_observer,
            on_progress,
            total_steps=total_steps,
            has_curator=has_curator,
        ),
    )
    workflow.add_node(
        "merge_repair",
        lambda state: merge_repair_progress_node(
            state,
            merge_repair,
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
        {"multi_card_observer": "multi_card_observer", "repair": "repair"},
    )
    workflow.add_conditional_edges(
        "multi_card_observer",
        route_after_multi_card_observer,
        {"complete": END, "merge_repair": "merge_repair", "human_review": "human_review"},
    )
    workflow.add_conditional_edges(
        "merge_repair",
        route_after_merge_repair,
        {"observer": "observer", "human_review": "human_review"},
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
        totalSteps=7 if has_curator else 6,
        percent=8 if has_curator else 18,
        attempt=0,
        maxAttempts=int(state.get("max_attempts") or 1),
        detail=(
            f"识别到 {int(plan.get('structuredQuestionCount') or 0)} 个原始问题，"
            "所有通过 evidence 门禁的独立知识点均可生成卡片，不设数量上限"
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
    """上报单卡结构、忠实度和覆盖门禁。"""
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
        percent=(
            90
            if int(state.get("merge_round") or 0) > 0
            else attempt_percent(attempt, max_attempts, phase=1, start_percent=32 if has_curator else 25)
        ),
        attempt=attempt,
        maxAttempts=max_attempts,
        detail="检查摘要、卡面回忆提示、evidenceId、逐论断忠实度和结构化知识覆盖率",
    )
    return observer_node(state, observer)


def multi_card_observer_progress_node(
    state: ReviewGenerationState,
    multi_card_observer: MultiCardObserver | None,
    on_progress: ProgressCallback | None,
    *,
    total_steps: int = 6,
    has_curator: bool = False,
) -> ReviewGenerationState:
    """审查整组卡片粒度，并在通过后进入保存阶段。"""
    attempt = int(state.get("attempt") or 0)
    max_attempts = int(state.get("max_attempts") or 1)
    merge_round = int(state.get("merge_round") or 0)
    max_merge_rounds = int(state.get("max_merge_rounds") or 1)
    emit_progress(
        on_progress,
        stageCode="review.multi_card_observer",
        stageLabel="多卡片复查",
        message=f"正在复查整组卡片的拆分与合并边界（合并轮次 {merge_round}/{max_merge_rounds}）",
        status="RUNNING",
        currentStep=5 if has_curator else 4,
        totalSteps=total_steps,
        percent=90,
        attempt=attempt,
        maxAttempts=max_attempts,
        mergeRound=merge_round,
        maxMergeRounds=max_merge_rounds,
        detail="检查并列定义、类型、策略和组成项是否被机械拆散，同时保护独立原理、故障场景与解决方案",
    )
    result = multi_card_observer_node(state, multi_card_observer)
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
            mergeRound=merge_round,
            maxMergeRounds=max_merge_rounds,
        )
    return result


def merge_repair_progress_node(
    state: ReviewGenerationState,
    merge_repair: MergeRepair | None,
    on_progress: ProgressCallback | None,
    *,
    total_steps: int = 6,
    has_curator: bool = False,
) -> ReviewGenerationState:
    """只修复合并计划点名的卡片组，并上报独立合并轮次。"""
    next_round = int(state.get("merge_round") or 0) + 1
    max_merge_rounds = int(state.get("max_merge_rounds") or 1)
    emit_progress(
        on_progress,
        stageCode="review.merge_repair",
        stageLabel="定向合并" if next_round <= max_merge_rounds else "合并修复已耗尽",
        message=(
            f"正在执行第 {next_round}/{max_merge_rounds} 轮定向合并"
            if next_round <= max_merge_rounds
            else "多卡片仍需合并，已达到自动合并轮次上限"
        ),
        status="RUNNING" if next_round <= max_merge_rounds else "NEEDS_REVIEW",
        currentStep=6 if has_curator else 5,
        totalSteps=total_steps,
        percent=90,
        attempt=int(state.get("attempt") or 0),
        maxAttempts=int(state.get("max_attempts") or 1),
        mergeRound=min(next_round, max_merge_rounds),
        maxMergeRounds=max_merge_rounds,
        detail=merge_plan_detail(state.get("merge_plan")),
    )
    return merge_repair_node(state, merge_repair)


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
        "多卡片粒度复查通过，或定向合并在安全预算内收敛",
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
    """执行确定性单卡门禁，成功后保留完整有效候选供多卡复查。"""
    actor_feedback = list(state.get("attempt_feedback") or [])
    if actor_feedback:
        return {"status": "REJECTED", "attempt_feedback": actor_feedback}
    try:
        result = observer(
            dict(state.get("candidate") or {}),
            dict(state.get("curator_context") or {}),
        )
        candidate = dict(state.get("candidate") or {})
        fingerprints = list(state.get("candidate_fingerprints") or [])
        fingerprint = candidate_fingerprint(candidate)
        if not fingerprints or fingerprints[-1] != fingerprint:
            fingerprints.append(fingerprint)
        return {
            "result": result,
            "last_valid_result": result,
            "last_valid_candidate": candidate,
            "candidate_fingerprints": fingerprints,
            "status": "SINGLE_CARD_COMPLETED",
            "attempt_feedback": [],
        }
    except Exception as exc:  # noqa: BLE001 - 门禁异常必须进入修复循环。
        return {
            "status": "REJECTED",
            "attempt_feedback": diagnostics_from_exception(exc),
        }


def multi_card_observer_node(
    state: ReviewGenerationState,
    multi_card_observer: MultiCardObserver | None,
) -> ReviewGenerationState:
    """只生成结构化合并计划；没有合并组时发布最后一次完整有效结果。"""
    candidate = dict(state.get("candidate") or {})
    cards = candidate.get("cards")
    if multi_card_observer is None or not isinstance(cards, list) or len(cards) <= 1:
        return {
            "merge_plan": {"passed": True, "mergeGroups": []},
            "result": state.get("last_valid_result"),
            "status": "COMPLETED",
        }
    try:
        raw_plan = multi_card_observer(
            candidate,
            dict(state.get("curator_context") or {}),
            int(state.get("merge_round") or 0),
        )
        plan = normalize_merge_plan(raw_plan, candidate)
        if not plan["mergeGroups"]:
            return {
                "merge_plan": plan,
                "result": state.get("last_valid_result"),
                "status": "COMPLETED",
            }
        return {"merge_plan": plan, "status": "MERGE_REQUIRED"}
    except Exception as exc:  # noqa: BLE001 - 多卡审查异常必须安全停止，不能跳过合并门禁。
        diagnostics = [f"多卡片复查失败：{item}" for item in diagnostics_from_exception(exc)]
        return merge_failure_state(state, diagnostics)


def merge_repair_node(
    state: ReviewGenerationState,
    merge_repair: MergeRepair | None,
) -> ReviewGenerationState:
    """应用点名合并组并执行确定性并集、指纹与只读卡片校验。"""
    merge_round = int(state.get("merge_round") or 0)
    max_merge_rounds = int(state.get("max_merge_rounds") or 1)
    if merge_round >= max_merge_rounds:
        return merge_failure_state(
            state,
            [f"多卡片合并达到最大 {max_merge_rounds} 轮，最后一版完整有效候选未发布并保留旧卡片"],
        )
    if merge_repair is None:
        return merge_failure_state(state, ["多卡片复查要求合并，但未配置 merge repair"])
    candidate = dict(state.get("candidate") or {})
    plan = dict(state.get("merge_plan") or {})
    next_round = merge_round + 1
    try:
        repair_payload = merge_repair(
            candidate,
            plan,
            dict(state.get("curator_context") or {}),
            next_round,
        )
        merged_candidate = apply_merge_repairs(candidate, plan, repair_payload)
        fingerprint = candidate_fingerprint(merged_candidate)
        fingerprints = list(state.get("candidate_fingerprints") or [])
        if fingerprints and fingerprints[-1] == fingerprint:
            return merge_failure_state(
                state,
                [f"第 {next_round} 轮合并后候选指纹连续无变化，判定为不收敛"],
                merge_round=next_round,
            )
        fingerprints.append(fingerprint)
        history = append_merge_history(state, next_round, plan)
        return {
            "candidate": merged_candidate,
            "candidate_fingerprints": fingerprints,
            "merge_round": next_round,
            "merge_plan": {"passed": True, "mergeGroups": []},
            "attempt_feedback": [],
            "feedback_history": history,
            "status": "OBSERVING",
        }
    except Exception as exc:  # noqa: BLE001 - 非法合并结果不能污染最后一次完整有效候选。
        diagnostics = [f"第 {next_round} 轮合并失败：{item}" for item in diagnostics_from_exception(exc)]
        return merge_failure_state(state, diagnostics, merge_round=next_round)


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
        # 仅记录候选数量供进度展示，不把候选数转换成卡片截断上限。
        plan["curatorKnowledgeUnitCount"] = unit_count
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
    """单卡门禁通过后进入多卡复查，失败才消耗 actor 质量尝试。"""
    return "multi_card_observer" if state.get("status") == "SINGLE_CARD_COMPLETED" else "repair"


def route_after_multi_card_observer(state: ReviewGenerationState) -> str:
    """多卡片复查通过即保存，需要合并或安全失败时进入对应分支。"""
    if state.get("status") == "COMPLETED":
        return "complete"
    return "human_review" if state.get("status") == "NEEDS_REVIEW" else "merge_repair"


def route_after_merge_repair(state: ReviewGenerationState) -> str:
    """合并后重新执行单卡门禁，预算或指纹失败时转人工状态。"""
    return "human_review" if state.get("status") == "NEEDS_REVIEW" else "observer"


def current_model_name(state: ReviewGenerationState) -> str:
    """从生成计划读取当前实际模型，进度和降级说明不得伪装成固定供应商。"""
    model_name = str((state.get("plan") or {}).get("llmModel") or "gpt-5.6-terra").strip()
    return model_name or "gpt-5.6-terra"


def route_after_repair(state: ReviewGenerationState) -> str:
    """模型预算耗尽后不再继续调用复习模型。"""
    return "human_review" if state.get("status") == "NEEDS_REVIEW" else "actor"


def failure_message(state: ReviewGenerationState, attempts: int) -> str:
    """区分 actor 质量耗尽与多卡合并不收敛，同时沿用人工复查语义。"""
    merge_round = int(state.get("merge_round") or 0)
    if state.get("last_valid_candidate") and state.get("status") == "NEEDS_REVIEW":
        return (
            f"复习卡片已通过单卡门禁，但多卡片合并在 {merge_round} 轮后未收敛，"
            "需要人工处理；最后一次完整有效候选和旧卡片均已保留"
        )
    return f"自动修复 {attempts} 次后仍未通过复习卡片质量门禁"


def candidate_fingerprint(candidate: dict[str, Any]) -> str:
    """用问题集合、知识单元与 evidence 集合生成候选收敛指纹。"""
    cards = candidate.get("cards")
    card_items = cards if isinstance(cards, list) else []
    identity = {
        "questions": sorted(
            {
                normalized_contract_key(card.get("question"))
                for card in card_items
                if isinstance(card, dict) and normalized_contract_key(card.get("question"))
            }
        ),
        "knowledgeUnitIds": sorted(
            {
                str(item).strip()
                for card in card_items
                if isinstance(card, dict)
                for item in list_field(card, "knowledgeUnitIds")
                if str(item).strip()
            }
        ),
        "evidenceIds": sorted(
            {
                str(item).strip()
                for card in card_items
                if isinstance(card, dict)
                for item in list_field(card, "evidenceIds")
                if str(item).strip()
            }
        ),
    }
    serialized = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def normalize_merge_plan(raw_plan: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """校验多卡 observer 只返回完整、无重叠且可执行的结构化合并组。"""
    if not isinstance(raw_plan, dict):
        raise ValueError("多卡片 observer 必须返回 JSON 对象")
    raw_groups = raw_plan.get("mergeGroups")
    if raw_groups is None and raw_plan.get("passed") is True:
        raw_groups = []
    if not isinstance(raw_groups, list):
        raise ValueError("多卡片 observer 的 mergeGroups 必须是数组")
    cards = candidate.get("cards")
    if not isinstance(cards, list):
        raise ValueError("候选 cards 不是数组")
    groups: list[dict[str, Any]] = []
    claimed_indexes: set[int] = set()
    for group_index, raw_group in enumerate(raw_groups, start=1):
        if not isinstance(raw_group, dict):
            raise ValueError(f"合并计划 {group_index} 不是 JSON 对象")
        indexes = normalize_card_indexes(raw_group.get("cardIndexes"), len(cards), group_index)
        overlap = claimed_indexes.intersection(indexes)
        if overlap:
            raise ValueError(f"合并计划的 cardIndexes 相互重叠：{sorted(overlap)}")
        claimed_indexes.update(indexes)
        selected = [cards[index - 1] for index in indexes]
        if any(not isinstance(card, dict) for card in selected):
            raise ValueError(f"合并计划 {group_index} 点名了非对象卡片")
        group = {
            "cardIndexes": indexes,
            "parentTopic": required_text(raw_group, "parentTopic", group_index),
            "reason": required_text(raw_group, "reason", group_index),
            "targetQuestion": required_text(raw_group, "targetQuestion", group_index),
            "hintTopics": required_text_list(raw_group, "hintTopics", group_index),
            "mustPreserveKnowledgeUnitIds": normalized_string_list(
                raw_group.get("mustPreserveKnowledgeUnitIds")
            ),
            "mustPreserveEvidenceIds": normalized_string_list(raw_group.get("mustPreserveEvidenceIds")),
            "mustPreserveClaims": required_text_list(raw_group, "mustPreserveClaims", group_index),
        }
        expected_knowledge_ids = card_field_union(selected, "knowledgeUnitIds")
        expected_evidence_ids = card_field_union(selected, "evidenceIds")
        if set(group["mustPreserveKnowledgeUnitIds"]) != expected_knowledge_ids:
            raise ValueError(f"合并计划 {group_index} 的 mustPreserveKnowledgeUnitIds 与原卡并集不一致")
        if set(group["mustPreserveEvidenceIds"]) != expected_evidence_ids:
            raise ValueError(f"合并计划 {group_index} 的 mustPreserveEvidenceIds 与原卡并集不一致")
        expected_claims = card_claims(selected)
        planned_claim_keys = {normalized_contract_key(item) for item in group["mustPreserveClaims"]}
        missing_claims = [claim for claim in expected_claims if normalized_contract_key(claim) not in planned_claim_keys]
        if missing_claims:
            raise ValueError(f"合并计划 {group_index} 未完整列出原答案论断")
        group["mustPreserveSourceQuestions"] = card_source_questions(selected)
        group["mustPreserveCoveredSourceQuestionKeys"] = sorted(card_source_question_keys(selected))
        groups.append(group)
    return {"passed": not groups, "mergeGroups": groups}


def apply_merge_repairs(
    candidate: dict[str, Any],
    plan: dict[str, Any],
    repair_payload: dict[str, Any],
) -> dict[str, Any]:
    """用合并组替换点名卡片，并确定性证明未点名卡片内容没有变化。"""
    if not isinstance(repair_payload, dict):
        raise ValueError("merge repair 必须返回 JSON 对象")
    planned_groups = plan.get("mergeGroups")
    repaired_groups = repair_payload.get("mergedGroups")
    if not isinstance(planned_groups, list) or not planned_groups:
        raise ValueError("当前没有可执行的合并计划")
    if not isinstance(repaired_groups, list) or len(repaired_groups) != len(planned_groups):
        raise ValueError("merge repair 必须逐组合并全部点名卡片")
    repaired_by_indexes: dict[tuple[int, ...], dict[str, Any]] = {}
    for raw_group in repaired_groups:
        if not isinstance(raw_group, dict) or not isinstance(raw_group.get("card"), dict):
            raise ValueError("mergedGroups 每项必须包含 cardIndexes 和 card")
        key = tuple(normalized_int_list(raw_group.get("cardIndexes")))
        if key in repaired_by_indexes:
            raise ValueError("merge repair 返回了重复合并组")
        repaired_by_indexes[key] = dict(raw_group["card"])
    original_cards = candidate.get("cards")
    if not isinstance(original_cards, list):
        raise ValueError("候选 cards 不是数组")
    replacement_by_first: dict[int, dict[str, Any]] = {}
    removed_indexes: set[int] = set()
    for group_index, group in enumerate(planned_groups, start=1):
        indexes = list(group["cardIndexes"])
        key = tuple(indexes)
        if key not in repaired_by_indexes:
            raise ValueError(f"merge repair 缺少合并计划 {group_index} 的结果")
        original_group = [original_cards[index - 1] for index in indexes]
        merged_card = repaired_by_indexes[key]
        validate_merged_card(merged_card, original_group, group, group_index)
        replacement_by_first[indexes[0]] = merged_card
        removed_indexes.update(indexes[1:])
    merged_cards: list[dict[str, Any]] = []
    untouched_before: list[dict[str, Any]] = []
    untouched_after: list[dict[str, Any]] = []
    for index, original in enumerate(original_cards, start=1):
        if index in replacement_by_first:
            merged_cards.append(replacement_by_first[index])
            continue
        if index in removed_indexes:
            continue
        merged_cards.append(original)
        untouched_before.append(original)
        untouched_after.append(merged_cards[-1])
    if untouched_after != untouched_before:
        raise ValueError("merge repair 修改了未被点名的卡片")
    result = dict(candidate)
    result["cards"] = merged_cards
    return result


def validate_merged_card(
    merged_card: dict[str, Any],
    original_group: list[dict[str, Any]],
    plan_group: dict[str, Any],
    group_index: int,
) -> None:
    """确认合并卡没有丢失知识单元、原问题覆盖、evidence 或答案论断。"""
    if normalized_contract_key(merged_card.get("question")) != normalized_contract_key(
        plan_group.get("targetQuestion")
    ):
        raise ValueError(f"合并结果 {group_index} 未使用计划中的 targetQuestion")
    expected_knowledge_ids = card_field_union(original_group, "knowledgeUnitIds")
    expected_evidence_ids = card_field_union(original_group, "evidenceIds")
    if set(normalized_string_list(merged_card.get("knowledgeUnitIds"))) != expected_knowledge_ids:
        raise ValueError(f"合并结果 {group_index} 的 knowledgeUnitIds 并集发生变化")
    if set(normalized_string_list(merged_card.get("evidenceIds"))) != expected_evidence_ids:
        raise ValueError(f"合并结果 {group_index} 的 evidenceIds 并集发生变化")
    expected_question_keys = card_source_question_keys(original_group)
    if not expected_question_keys.issubset(card_source_question_keys([merged_card])):
        raise ValueError(f"合并结果 {group_index} 丢失原始问题覆盖")
    answer_key = normalized_contract_key(merged_card.get("answer"))
    missing_claims = [
        claim
        for claim in plan_group.get("mustPreserveClaims") or []
        if normalized_contract_key(claim) not in answer_key
    ]
    if missing_claims:
        raise ValueError(f"合并结果 {group_index} 丢失原答案论断")
    if not str(merged_card.get("hint") or "").strip():
        raise ValueError(f"合并结果 {group_index} 缺少不泄露答案的 hint")


def merge_failure_state(
    state: ReviewGenerationState,
    diagnostics: list[str],
    *,
    merge_round: int | None = None,
) -> ReviewGenerationState:
    """记录合并失败并保留最后一次通过单卡门禁的候选与结果。"""
    history = unique_feedback([*(state.get("feedback_history") or []), *diagnostics])
    result: ReviewGenerationState = {
        "feedback_history": history,
        "attempt_feedback": unique_feedback(diagnostics),
        "candidate": dict(state.get("last_valid_candidate") or state.get("candidate") or {}),
        "result": state.get("last_valid_result"),
        "status": "NEEDS_REVIEW",
    }
    if merge_round is not None:
        result["merge_round"] = merge_round
    return result


def append_merge_history(
    state: ReviewGenerationState,
    merge_round: int,
    plan: dict[str, Any],
) -> list[str]:
    """把每轮合并原因写入统一质量审计历史。"""
    reasons = [
        f"第 {merge_round} 合并轮：{str(group.get('reason') or '').strip()}"
        for group in plan.get("mergeGroups") or []
        if isinstance(group, dict) and str(group.get("reason") or "").strip()
    ]
    return unique_feedback([*(state.get("feedback_history") or []), *reasons])


def merge_plan_detail(plan: object) -> str:
    """生成不暴露答案正文的合并进度摘要。"""
    groups = plan.get("mergeGroups") if isinstance(plan, dict) else []
    if not isinstance(groups, list) or not groups:
        return "等待多卡片复查提供结构化合并计划"
    topics = [str(group.get("parentTopic") or "").strip() for group in groups if isinstance(group, dict)]
    return f"本轮合并 {len(groups)} 组：{'、'.join(item for item in topics if item)[:300]}"


def normalize_card_indexes(value: object, card_count: int, group_index: int) -> list[int]:
    """校验一组至少包含两张、使用 1 基索引且不重复的卡片。"""
    indexes = normalized_int_list(value)
    if len(indexes) < 2 or len(set(indexes)) != len(indexes):
        raise ValueError(f"合并计划 {group_index} 的 cardIndexes 至少包含两个不重复索引")
    if indexes != sorted(indexes) or any(index < 1 or index > card_count for index in indexes):
        raise ValueError(f"合并计划 {group_index} 的 cardIndexes 必须升序且位于候选范围内")
    return indexes


def normalized_int_list(value: object) -> list[int]:
    """把 JSON 数组转为整数索引，拒绝布尔值和非法文本。"""
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        if isinstance(item, bool):
            return []
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            return []
        if str(parsed) != str(item).strip() and not isinstance(item, int):
            return []
        result.append(parsed)
    return result


def required_text(raw: dict[str, Any], field: str, group_index: int) -> str:
    """读取合并计划必填短文本。"""
    value = " ".join(str(raw.get(field) or "").split()).strip()
    if not value:
        raise ValueError(f"合并计划 {group_index} 缺少 {field}")
    return value[:1200]


def required_text_list(raw: dict[str, Any], field: str, group_index: int) -> list[str]:
    """读取合并计划必填文本数组。"""
    values = normalized_string_list(raw.get(field), maximum_length=1200)
    if not values:
        raise ValueError(f"合并计划 {group_index} 缺少 {field}")
    return values


def normalized_string_list(value: object, *, maximum_length: int = 200) -> list[str]:
    """按出现顺序清洗字符串数组并去重。"""
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = " ".join(str(item).split()).strip()[:maximum_length]
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def list_field(card: dict[str, Any], field: str) -> list[Any]:
    """安全读取卡片数组字段。"""
    value = card.get(field)
    return value if isinstance(value, list) else []


def card_field_union(cards: list[dict[str, Any]], field: str) -> set[str]:
    """计算卡片稳定标识数组字段的非空并集。"""
    return {
        str(item).strip()
        for card in cards
        for item in list_field(card, field)
        if str(item).strip()
    }


def card_source_questions(cards: list[dict[str, Any]]) -> list[str]:
    """兼容 sourceQuestion 与 sourceQuestions，收集原始问题文本并去重。"""
    result: list[str] = []
    seen: set[str] = set()
    for card in cards:
        raw_values: list[Any] = []
        source_question = card.get("sourceQuestion")
        if source_question:
            raw_values.append(source_question)
        raw_values.extend(list_field(card, "sourceQuestions"))
        for raw in raw_values:
            value = raw.get("question") if isinstance(raw, dict) else raw
            text = " ".join(str(value or "").split()).strip()[:180]
            key = normalized_contract_key(text)
            if text and key and key not in seen:
                seen.add(key)
                result.append(text)
    return result


def card_source_question_keys(cards: list[dict[str, Any]]) -> set[str]:
    """兼容显式覆盖键和旧 sourceQuestion，形成原始问题覆盖集合。"""
    keys = {
        normalized_contract_key(item)
        for card in cards
        for item in list_field(card, "coveredSourceQuestionKeys")
        if normalized_contract_key(item)
    }
    keys.update(normalized_contract_key(item) for item in card_source_questions(cards))
    return {item for item in keys if item}


def card_claims(cards: list[dict[str, Any]]) -> list[str]:
    """把原卡答案切成必须逐项保留的稳定论断。"""
    result: list[str] = []
    seen: set[str] = set()
    for card in cards:
        answer = str(card.get("answer") or "").strip()
        for raw in re.split(r"[。！？!?；;\n]+", answer):
            claim = re.sub(r"^\s*(?:[-*+>] |\d+[.)]\s*)", "", raw).strip()
            key = normalized_contract_key(claim)
            if claim and key and key not in seen:
                seen.add(key)
                result.append(claim[:1200])
    return result


def normalized_contract_key(value: object) -> str:
    """生成跨节点稳定比较键。"""
    return re.sub(r"[\W_]+", "", str(value or ""), flags=re.UNICODE).lower()


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
