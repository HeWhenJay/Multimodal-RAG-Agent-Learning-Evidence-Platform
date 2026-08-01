"""对 py-fsrs 6.x 的稳定业务适配。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging

from fsrs import Card, Rating, Scheduler


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReviewSchedule:
    """一次 FSRS 评分产生的可持久化结果。"""

    card_json: str
    review_log_json: str
    due_at: datetime
    interval_days: float
    retrievability: float
    state_rebuilt: bool = False


class FsrsReviewScheduler:
    """把四档中文评分映射到 FSRS，并统一使用 UTC 时间。"""

    def __init__(self, desired_retention: float = 0.90, *, enable_fuzzing: bool = False) -> None:
        self.desired_retention = desired_retention
        self._scheduler = Scheduler(
            desired_retention=desired_retention,
            learning_steps=(),
            relearning_steps=(),
            maximum_interval=3650,
            enable_fuzzing=enable_fuzzing,
        )

    def new_card_json(self, created_at: datetime | None = None) -> str:
        """创建立即到期的新卡状态，首次进入待复习队列。"""
        return Card(due=as_utc(created_at or datetime.now(timezone.utc))).to_json()

    def review(
        self,
        card_json: str,
        *,
        rating: int,
        reviewed_at: datetime | None = None,
        duration_ms: int | None = None,
        fallback_created_at: datetime | None = None,
    ) -> ReviewSchedule:
        """恢复卡片状态并完成一次排程；损坏状态会重建后继续评分。"""
        review_time = as_utc(reviewed_at or datetime.now(timezone.utc))
        card, rebuilt = self.restore_card(card_json, fallback_created_at or review_time)
        updated_card, review_log = self._scheduler.review_card(
            card,
            rating=rating_value(rating),
            review_datetime=review_time,
            review_duration=duration_ms,
        )
        due_at = as_utc(updated_card.due)
        interval_days = max(0.0, (due_at - review_time).total_seconds() / 86_400)
        # 返回下次到期时的预计可提取率，便于解释目标记忆率，而不是刚评分后的 100%。
        retrievability = self._scheduler.get_card_retrievability(updated_card, due_at)
        return ReviewSchedule(
            card_json=updated_card.to_json(),
            review_log_json=review_log.to_json(),
            due_at=due_at,
            interval_days=round(interval_days, 6),
            retrievability=bounded_probability(retrievability),
            state_rebuilt=rebuilt,
        )

    def retrievability(self, card_json: str, at: datetime | None = None) -> float:
        """计算指定时刻的预计可提取率，异常状态按零返回。"""
        try:
            card = Card.from_json(card_json)
            value = self._scheduler.get_card_retrievability(card, as_utc(at or datetime.now(timezone.utc)))
            return bounded_probability(value)
        except Exception:
            return 0.0

    @staticmethod
    def restore_card(card_json: str, fallback_created_at: datetime) -> tuple[Card, bool]:
        """恢复序列化状态，字段损坏时创建一张立即可复习的新卡。"""
        try:
            return Card.from_json(card_json), False
        except Exception:
            logger.warning("复习卡片 FSRS 状态损坏，已按创建时间重建初始状态")
            return Card(due=as_utc(fallback_created_at)), True


def rating_value(rating: int) -> Rating:
    """把公开接口的 1-4 评分映射为 FSRS Rating。"""
    values = {
        1: Rating.Again,
        2: Rating.Hard,
        3: Rating.Good,
        4: Rating.Easy,
    }
    try:
        return values[rating]
    except KeyError as exc:
        raise ValueError("复习评分必须是 1 到 4") from exc


def as_utc(value: datetime) -> datetime:
    """把数据库或调用方时间统一转换为带时区 UTC。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def bounded_probability(value: float) -> float:
    """限制浮点误差，保持公开概率位于 0-1。"""
    return round(max(0.0, min(1.0, float(value))), 6)
