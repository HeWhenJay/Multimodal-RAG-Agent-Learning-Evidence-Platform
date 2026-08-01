"""复习卡片分组、答案揭示与生成短锁测试。"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json

from app.review.fsrs_scheduler import FsrsReviewScheduler
from app.review.generation_guard import ReviewGenerationGuard
from app.review.repository import ReviewCardRecord, ReviewOverviewStats, ReviewSettingsRecord
from app.review.service import ReviewService
from app.schemas.rag import Evidence


NOW = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)


def card(card_id: int, material_id: int, title: str) -> ReviewCardRecord:
    """构造一张含答案和真实 evidence 的持久化卡片。"""
    evidence = Evidence(
        evidenceId=f"material-{material_id}-1",
        documentId=f"material-{material_id}",
        documentTitle=title,
        title=title,
        sectionName="核心原理",
        sectionTitle="核心原理",
        snippet="这是用于定位原文的关键知识点片段。",
        source="upload",
        documentType="pdf",
        score=1.0,
        retrievalSource="summary",
    )
    return ReviewCardRecord(
        id=card_id,
        material_id=material_id,
        user_id="7",
        material_title=title,
        document_type="pdf",
        question=f"{title}的核心原理是什么？",
        answer="这是只在用户主动揭示后返回的答案。",
        hint="先回忆核心概念",
        evidence_refs_json=json.dumps([evidence.model_dump(mode="json")], ensure_ascii=False),
        fsrs_card_json=FsrsReviewScheduler().new_card_json(NOW),
        due_at=NOW,
        retrievability=0.9,
        review_count=0,
        lapse_count=0,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )


class GroupingTransaction:
    """提供分组和揭示测试所需的仓储行为。"""

    def __init__(self) -> None:
        self.cards = [card(1, 12, "Kafka 高可用"), card(2, 13, "Redis 持久化"), card(3, 12, "Kafka 高可用")]

    def get_or_create_settings(self, user_id: str) -> ReviewSettingsRecord:
        return ReviewSettingsRecord(user_id, True, 0.9, 20, "09:00", "Asia/Shanghai")

    def overview_stats(self, user_id: str, **_kwargs) -> ReviewOverviewStats:
        return ReviewOverviewStats(3, 0, 3, 2, None)

    def list_due_cards(self, user_id: str, *, now: datetime, limit: int) -> list[ReviewCardRecord]:
        return self.cards[:limit]

    def find_card(self, card_id: int, user_id: str) -> ReviewCardRecord | None:
        return next((item for item in self.cards if item.id == card_id and item.user_id == user_id), None)


class GroupingRepository:
    """复用同一个内存事务对象。"""

    def __init__(self) -> None:
        self.value = GroupingTransaction()

    @contextmanager
    def transaction(self):
        yield self.value


def test_due_cards_are_grouped_by_uploaded_material_without_answers() -> None:
    """每日队列应按资料聚合多张卡片，并在揭示前隐藏答案与原文。"""
    service = ReviewService(repository=GroupingRepository(), now_provider=lambda: NOW)

    result = service.list_due_groups("7", 20)

    assert result.totalDueCount == 3
    assert [group.materialId for group in result.groups] == [12, 13]
    assert [group.dueCardCount for group in result.groups] == [2, 1]
    assert all(card.answer is None for group in result.groups for card in group.cards)
    assert all(card.evidenceRefs == [] for group in result.groups for card in group.cards)
    assert result.groups[0].cards[0].hint == "先回忆核心概念"


def test_reveal_card_returns_answer_and_rag_evidence_for_current_user() -> None:
    """主动揭示接口应返回答案、原文片段和定位所需 evidence。"""
    service = ReviewService(repository=GroupingRepository(), now_provider=lambda: NOW)

    revealed = service.get_card(1, "7")

    assert revealed.answer == "这是只在用户主动揭示后返回的答案。"
    assert revealed.evidenceRefs[0].evidenceId == "material-12-1"


class FakeRedis:
    """实现生成锁测试所需的最小 Redis 行为。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, name: str, value: str, ex: int | None = None, nx: bool = False):
        if nx and name in self.values:
            return False
        self.values[name] = value
        return True

    def get(self, name: str):
        return self.values.get(name)

    def delete(self, *names: str):
        for name in names:
            self.values.pop(name, None)

    def eval(self, script: str, numkeys: int, *keys_and_args: str):
        key, token = keys_and_args
        if self.values.get(key) == token:
            self.values.pop(key, None)
            return 1
        return 0


class EvalFailureRedis(FakeRedis):
    """模拟 Redis 无法执行原子 Lua 解锁。"""

    def eval(self, script: str, numkeys: int, *keys_and_args: str):
        raise RuntimeError("eval unavailable")


def test_generation_guard_rejects_duplicate_work_and_releases_lock() -> None:
    """同一资料版本并发生成时只允许一个持有者，释放后可再次生成。"""
    redis = FakeRedis()
    first_guard = ReviewGenerationGuard(redis_client=redis, ttl_seconds=60)
    second_guard = ReviewGenerationGuard(redis_client=redis, ttl_seconds=60)

    first = first_guard.acquire("7:12:3")
    duplicate = second_guard.acquire("7:12:3")

    assert first is not None
    assert duplicate is None
    assert "7:12:3" not in first.key
    first.release()

    next_lease = second_guard.acquire("7:12:3")
    assert next_lease is not None
    next_lease.release()


def test_generation_guard_never_deletes_new_owner_lock_when_eval_fails() -> None:
    """原子解锁失败后必须等待 TTL，不能用 GET/DELETE 误删新持有者。"""
    redis = EvalFailureRedis()
    guard = ReviewGenerationGuard(redis_client=redis, ttl_seconds=60)
    lease = guard.acquire("7:12:3")

    assert lease is not None
    redis.values[lease.key] = "new-owner-token"
    lease.release()

    assert redis.values[lease.key] == "new-owner-token"
