"""复习卡片 PAE/ReAct LangGraph 的循环与人工终态测试。"""

import pytest

from app.review.generation_graph import (
    DEFAULT_REVIEW_GRAPH_MAX_ATTEMPTS,
    REVIEW_GRAPH_RECURSION_LIMIT,
    ReviewManualReviewRequired,
    configured_review_graph_max_attempts,
    run_review_generation_graph,
)


class GateError(RuntimeError):
    """模拟质量观察节点返回的逐项门禁反馈。"""

    diagnostics = ("问题不是完整疑问句", "answer 未被 evidence 支撑")


def test_quality_feedback_is_sent_to_next_actor_attempt() -> None:
    """第一次观察失败时，第二次 actor 必须收到同一轮修复诊断。"""
    calls: list[tuple[int, list[str]]] = []

    def actor(attempt: int, feedback: list[str], _previous_candidate: dict) -> dict:
        calls.append((attempt, feedback))
        return {"attempt": attempt}

    def observer(candidate: dict) -> dict:
        if candidate["attempt"] == 1:
            raise GateError("质量门禁失败")
        return {"ok": True}

    outcome = run_review_generation_graph(actor=actor, observer=observer, plan={"materialId": 12}, max_attempts=4)

    assert outcome.attempts == 2
    assert calls[0] == (1, [])
    assert calls[1][0] == 2
    assert calls[1][1] == ["问题不是完整疑问句", "answer 未被 evidence 支撑"]
    assert "第 1 次：问题不是完整疑问句" in outcome.quality_feedback


def test_repair_actor_receives_previous_candidate_for_incremental_fix() -> None:
    """第二轮必须拿到上一版候选，以便保留合格卡并只修复坏卡。"""
    previous_candidates: list[dict] = []

    def actor(attempt: int, _feedback: list[str], previous_candidate: dict) -> dict:
        previous_candidates.append(previous_candidate)
        return {"attempt": attempt, "cards": [{"question": f"第 {attempt} 版"}]}

    def observer(candidate: dict) -> dict:
        if candidate["attempt"] == 1:
            raise GateError("第一版含坏卡")
        return {"ok": True}

    run_review_generation_graph(actor=actor, observer=observer, plan={}, max_attempts=2)

    assert previous_candidates[0] == {}
    assert previous_candidates[1]["attempt"] == 1
    assert previous_candidates[1]["cards"] == [{"question": "第 1 版"}]


def test_exhausted_quality_repair_enters_manual_review() -> None:
    """连续质量失败不能无限调用模型，必须稳定进入人工处理。"""
    calls = 0

    def actor(attempt: int, _feedback: list[str], _previous_candidate: dict) -> dict:
        nonlocal calls
        calls += 1
        return {"attempt": attempt}

    def observer(_candidate: dict) -> dict:
        raise GateError("结构化原始问题覆盖不足")

    with pytest.raises(ReviewManualReviewRequired) as raised:
        run_review_generation_graph(actor=actor, observer=observer, plan={}, max_attempts=3)

    assert calls == 3
    assert raised.value.attempts == 3
    assert any("问题不是完整疑问句" in item for item in raised.value.quality_feedback)


def test_review_graph_keeps_large_recursion_limit_separate_from_model_budget() -> None:
    """999 只用于 LangGraph 递归保护，模型预算仍由独立参数控制。"""
    assert REVIEW_GRAPH_RECURSION_LIMIT == 999


def test_review_graph_uses_eight_quality_attempts_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认质量修复预算提升到 8 次，同时继续允许环境变量覆盖。"""
    monkeypatch.delenv("REVIEW_GENERATION_MAX_ATTEMPTS", raising=False)
    assert DEFAULT_REVIEW_GRAPH_MAX_ATTEMPTS == 8
    assert configured_review_graph_max_attempts() == 8


def test_review_graph_reports_real_nodes_attempts_and_terminal_progress() -> None:
    """复习图必须上报真实节点、模型轮次、修复反馈和最终保存阶段。"""
    events: list[dict] = []

    def actor(attempt: int, _feedback: list[str], _previous_candidate: dict) -> dict:
        return {"attempt": attempt}

    def observer(candidate: dict) -> dict:
        if candidate["attempt"] == 1:
            raise GateError("第一次质量门禁失败")
        return {"ok": True}

    outcome = run_review_generation_graph(
        actor=actor,
        observer=observer,
        plan={"structuredQuestionCount": 20, "maxCards": 20},
        max_attempts=3,
        on_progress=events.append,
    )

    assert outcome.attempts == 2
    assert [event["stageCode"] for event in events] == [
        "review.planner",
        "review.actor",
        "review.observer",
        "review.repair",
        "review.actor",
        "review.observer",
        "review.persist",
    ]
    assert [event["attempt"] for event in events if event["stageCode"] == "review.actor"] == [1, 2]
    assert [event["percent"] for event in events] == sorted(event["percent"] for event in events)
    assert events[-1]["percent"] == 94
