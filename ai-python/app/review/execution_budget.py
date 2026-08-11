"""复习模型调用的单段截止时间与协作式取消。"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from threading import Event, Lock
from time import monotonic


class ReviewExecutionTimeout(TimeoutError):
    """复习分段达到总预算，后续节点不得继续发起模型请求。"""


@dataclass
class ReviewExecutionBudget:
    """在同一分段的 LangExtract、生成和修复节点之间共享截止时间。"""

    started_at: float
    deadline: float
    request_timeout_seconds: float
    cancel_event: Event = field(default_factory=Event, repr=False)
    _cancel_reason: str | None = field(default=None, init=False, repr=False)
    _cancel_lock: Lock = field(default_factory=Lock, init=False, repr=False)

    @classmethod
    def start(
        cls,
        total_timeout_seconds: float,
        request_timeout_seconds: float,
        *,
        started_at: float | None = None,
    ) -> "ReviewExecutionBudget":
        """创建从提交时开始计时的单段预算，排队时间也纳入总预算。"""
        started = monotonic() if started_at is None else float(started_at)
        total = max(0.05, float(total_timeout_seconds))
        request = max(0.05, float(request_timeout_seconds))
        return cls(started, started + total, request)

    @property
    def cancelled(self) -> bool:
        """返回外层任务是否已经发出取消信号。"""
        return self.cancel_event.is_set()

    @property
    def cancel_reason(self) -> str | None:
        """返回取消原因，供进度诊断使用。"""
        with self._cancel_lock:
            return self._cancel_reason

    def elapsed_seconds(self) -> float:
        """返回当前分段已经消耗的秒数。"""
        return max(0.0, monotonic() - self.started_at)

    def remaining_seconds(self) -> float:
        """返回截止时间前的剩余秒数。"""
        if self.cancelled:
            return 0.0
        return max(0.0, self.deadline - monotonic())

    def cancel(self, reason: str) -> None:
        """发送线程安全的取消信号；已经运行的请求会在硬超时后回收。"""
        with self._cancel_lock:
            self._cancel_reason = str(reason or "分段预算已耗尽")[:240]
        self.cancel_event.set()

    def ensure_active(self, stage: str) -> None:
        """在每个模型节点和请求前检查预算，避免超时后继续排队。"""
        remaining = self.remaining_seconds()
        if self.cancelled or remaining <= 0:
            reason = self.cancel_reason or "分段总执行预算已耗尽"
            raise ReviewExecutionTimeout(
                f"{stage}已停止：{reason}；已耗时 {int(self.elapsed_seconds())} 秒"
            )

    def timeout_for_request(self, stage: str) -> float:
        """返回不超过单段剩余预算的单次模型请求超时。"""
        self.ensure_active(stage)
        remaining = self.remaining_seconds()
        # OpenAI/httpx 接受很小的正数；保留 10ms 让截止时间附近的请求也能快速退出。
        return max(0.01, min(self.request_timeout_seconds, remaining))

    def diagnostics(self) -> str:
        """生成不包含提示词和密钥的预算诊断文本。"""
        return (
            f"已耗时 {int(self.elapsed_seconds())} 秒，剩余 {int(self.remaining_seconds())} 秒，"
            f"单次模型请求上限 {int(self.request_timeout_seconds)} 秒"
        )


def configured_segment_cockpit_request_retries() -> int:
    """读取交互式分段的 Cockpit 重试次数，默认快速切换到降级端点。"""
    try:
        value = int(os.getenv("REVIEW_SEGMENT_COCKPIT_REQUEST_RETRIES", "0"))
    except (TypeError, ValueError):
        value = 0
    return max(0, min(3, value))


def configured_segment_generation_attempts() -> int:
    """读取交互式分段的质量修复轮数，避免失败模型无限循环。"""
    try:
        value = int(os.getenv("REVIEW_SEGMENT_MAX_GENERATION_ATTEMPTS", "3"))
    except (TypeError, ValueError):
        value = 3
    return max(1, min(20, value))


def configured_segment_merge_rounds() -> int:
    """读取交互式分段的多卡合并轮数，失败时交给人工决策。"""
    try:
        value = int(os.getenv("REVIEW_SEGMENT_MAX_MERGE_ROUNDS", "2"))
    except (TypeError, ValueError):
        value = 2
    return max(1, min(12, value))
