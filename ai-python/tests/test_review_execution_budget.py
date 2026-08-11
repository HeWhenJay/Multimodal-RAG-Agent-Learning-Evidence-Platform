"""交互式复习分段截止时间测试。"""

import pytest

from app.review.execution_budget import (
    ReviewExecutionBudget,
    ReviewExecutionTimeout,
    configured_segment_cockpit_request_retries,
)
from app.review.service import configured_review_segment_request_timeout_seconds


def test_interactive_segment_timeout_and_retry_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """交互式分段默认使用 180 秒单请求并保留一次 Cockpit 重试。"""
    monkeypatch.delenv("REVIEW_SEGMENT_REQUEST_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("REVIEW_SEGMENT_COCKPIT_REQUEST_RETRIES", raising=False)

    assert configured_review_segment_request_timeout_seconds() == 180.0
    assert configured_segment_cockpit_request_retries() == 1


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
