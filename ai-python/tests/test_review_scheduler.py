"""FSRS 复习排程适配测试。"""

from datetime import datetime, timezone
import json

from app.review.fsrs_scheduler import FsrsReviewScheduler


NOW = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)


def test_fsrs_four_ratings_produce_increasing_memory_stability_without_minute_reminders() -> None:
    """四档评分应写入不同记忆强度，并避免 1 分钟、10 分钟连续提醒。"""
    scheduler = FsrsReviewScheduler(enable_fuzzing=False)
    results = [
        scheduler.review(
            scheduler.new_card_json(NOW),
            rating=rating,
            reviewed_at=NOW,
            duration_ms=1_000,
        )
        for rating in range(1, 5)
    ]
    intervals = [result.interval_days for result in results]
    stabilities = [json.loads(result.card_json)["stability"] for result in results]

    assert intervals == sorted(intervals)
    assert intervals[0] >= 1
    assert stabilities == sorted(stabilities)
    assert len(set(stabilities)) == 4
    assert all(0.90 <= result.retrievability <= 0.93 for result in results[1:])


def test_corrupt_fsrs_state_is_rebuilt_and_review_continues() -> None:
    """历史状态损坏时不能阻断用户评分。"""
    scheduler = FsrsReviewScheduler(enable_fuzzing=False)

    result = scheduler.review(
        "{broken-json",
        rating=3,
        reviewed_at=NOW,
        fallback_created_at=NOW,
    )

    assert result.state_rebuilt is True
    assert result.due_at > NOW
    assert '"rating": 3' in result.review_log_json
