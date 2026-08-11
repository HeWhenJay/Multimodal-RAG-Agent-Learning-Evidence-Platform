"""交互式复习分段截止时间测试。"""

import pytest

from app.review.execution_budget import ReviewExecutionBudget, ReviewExecutionTimeout


def test_request_timeout_is_capped_by_segment_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """单次模型请求必须同时受独立上限和单段剩余时间约束。"""
    budget = ReviewExecutionBudget.start(1800, 240, started_at=100.0)

    monkeypatch.setattr("app.review.execution_budget.monotonic", lambda: 101.0)
    assert budget.timeout_for_request("模型生成") == 240.0

    monkeypatch.setattr("app.review.execution_budget.monotonic", lambda: 1850.0)
    assert budget.timeout_for_request("模型生成") == 50.0


def test_cancelled_budget_blocks_follow_up_model_nodes() -> None:
    """外层段任务超时后，后续 LangExtract/修复节点不得继续发请求。"""
    budget = ReviewExecutionBudget.start(1800, 240)
    budget.cancel("用户可感知的单段预算已耗尽")

    with pytest.raises(ReviewExecutionTimeout, match="预算已耗尽"):
        budget.ensure_active("多卡合并")
