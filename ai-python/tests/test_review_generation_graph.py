"""复习卡片 PAE/ReAct LangGraph 的循环与人工终态测试。"""

import pytest

from app.review.generation_graph import (
    REVIEW_GRAPH_RECURSION_LIMIT,
    ReviewManualReviewRequired,
    run_review_generation_graph,
)


class GateError(RuntimeError):
    """模拟质量观察节点返回的逐项门禁反馈。"""

    diagnostics = ("问题不是完整疑问句", "answer 未被 evidence 支撑")


def test_quality_feedback_is_sent_to_next_actor_attempt() -> None:
    """第一次观察失败时，第二次 actor 必须收到同一轮修复诊断。"""
    calls: list[tuple[int, list[str]]] = []

    def actor(attempt: int, feedback: list[str]) -> dict:
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


def test_exhausted_quality_repair_enters_manual_review() -> None:
    """连续质量失败不能无限调用模型，必须稳定进入人工处理。"""
    calls = 0

    def actor(attempt: int, _feedback: list[str]) -> dict:
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
