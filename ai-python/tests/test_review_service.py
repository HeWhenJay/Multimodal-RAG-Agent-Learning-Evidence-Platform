"""复习业务层的每日负担控制测试。"""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.core.result import BusinessError
from app.review.fsrs_scheduler import FsrsReviewScheduler
from app.review.repository import (
    CURRENT_REVIEW_EXTRACTORS,
    DatabaseReviewTransaction,
    MaterialSourceRecord,
    ReviewMaterialRecord,
    ReviewOverviewStats,
    ReviewSettingsRecord,
)
from app.review.service import ReviewService, material_generation_is_current, overview_response
from app.schemas.review import ReviewGradeRequest


NOW = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)


class FakeReviewTransaction:
    """只实现待复习列表所需的仓储操作。"""

    def __init__(self, reviewed_today: int) -> None:
        self.reviewed_today = reviewed_today
        self.requested_limit: int | None = None

    def get_or_create_settings(self, user_id: str, *, for_update: bool = False) -> ReviewSettingsRecord:
        return ReviewSettingsRecord(user_id, True, 0.90, 20, "09:00", "Asia/Shanghai")

    def overview_stats(self, user_id: str, **_kwargs) -> ReviewOverviewStats:
        return ReviewOverviewStats(4, self.reviewed_today, 30, 3, None)

    def list_due_cards(self, user_id: str, *, now: datetime, limit: int):
        self.requested_limit = limit
        return []


class FakeReviewRepository:
    """为服务层提供单个可观察事务。"""

    def __init__(self, transaction: FakeReviewTransaction) -> None:
        self.value = transaction

    @contextmanager
    def transaction(self):
        yield self.value


def test_due_queue_subtracts_reviews_already_completed_today() -> None:
    """每日 20 张且已完成 18 张时，本轮最多再返回 2 张。"""
    transaction = FakeReviewTransaction(reviewed_today=18)
    service = ReviewService(repository=FakeReviewRepository(transaction), now_provider=lambda: NOW)

    assert service.list_due("7", 20) == []
    assert transaction.requested_limit == 2


def test_due_queue_stops_after_daily_limit_is_reached() -> None:
    """达到每日上限后不再读取到期卡片。"""
    transaction = FakeReviewTransaction(reviewed_today=20)
    service = ReviewService(repository=FakeReviewRepository(transaction), now_provider=lambda: NOW)

    assert service.list_due("7", 20) == []
    assert transaction.requested_limit is None


class GradeTransaction:
    """提供评分边界测试所需的最小内存事务。"""

    def __init__(self, due_at: datetime) -> None:
        from app.review.repository import ReviewCardRecord

        self.lock_requested = False
        self.saved = False
        self.card = ReviewCardRecord(
            id=41,
            material_id=12,
            user_id="7",
            material_title="Kafka 高可用",
            document_type="pdf",
            question="Kafka 的副本机制是什么？",
            answer="Leader 与 Follower 副本共同构成分区副本集合。",
            hint="先回忆副本角色",
            evidence_refs_json="[]",
            fsrs_card_json=FsrsReviewScheduler().new_card_json(due_at),
            due_at=due_at,
            retrievability=0.0,
            review_count=0,
            lapse_count=0,
            active=True,
            created_at=due_at,
            updated_at=due_at,
        )

    def get_or_create_settings(self, user_id: str, *, for_update: bool = False) -> ReviewSettingsRecord:
        self.lock_requested = for_update
        return ReviewSettingsRecord(user_id, True, 0.90, 20, "09:00", "Asia/Shanghai")

    def find_card_for_update(self, card_id: int, user_id: str):
        return self.card if self.card.id == card_id and self.card.user_id == user_id else None

    def overview_stats(self, user_id: str, **_kwargs) -> ReviewOverviewStats:
        return ReviewOverviewStats(1, 0, 1, 1, None)

    def save_grade(self, card, **kwargs):
        self.saved = True
        self.card = replace(
            card,
            fsrs_card_json=kwargs["fsrs_card_json"],
            due_at=kwargs["next_due_at"],
            retrievability=kwargs["retrievability"],
            review_count=card.review_count + 1,
        )
        return self.card


class GradeRepository:
    """为评分边界测试复用单个内存事务。"""

    def __init__(self, transaction: GradeTransaction) -> None:
        self.transaction_value = transaction

    @contextmanager
    def transaction(self):
        yield self.transaction_value


def test_grade_rejects_a_future_card_before_writing_state() -> None:
    """重复提交或绕过前端时，未到期卡片不能再次消费每日额度。"""
    transaction = GradeTransaction(NOW + timedelta(days=1))
    service = ReviewService(repository=GradeRepository(transaction), now_provider=lambda: NOW)

    with pytest.raises(BusinessError, match="复习卡片尚未到期"):
        service.grade(41, ReviewGradeRequest(rating=3), "7")

    assert transaction.lock_requested is True
    assert transaction.saved is False


def test_sync_candidates_include_outdated_extractor_versions() -> None:
    """Prompt 版本升级后应分批刷新旧卡片，同时继续使用参数化 SQL。"""
    class RecordingCursor:
        """记录候选查询语句和绑定参数。"""

        def execute(self, statement, params):
            self.statement = statement
            self.params = params

        def fetchall(self):
            return []

    cursor = RecordingCursor()
    transaction = DatabaseReviewTransaction(cursor, "learning_evidence")
    transaction._statement = lambda query: query  # type: ignore[method-assign]

    assert transaction.list_sync_candidates("7", 3) == []
    assert "rm.extractor NOT IN (%s, %s, %s)" in cursor.statement
    assert "learning_review_material_exclusion" in cursor.statement
    assert cursor.params == ("7", *CURRENT_REVIEW_EXTRACTORS, 3)


class DeletionTransaction:
    """提供卡片与资料持久排除测试所需的最小事务。"""

    def __init__(self, *, card_material_id: int | None = 12, material_exists: bool = True) -> None:
        self.card_material_id = card_material_id
        self.material_exists = material_exists
        self.card_calls: list[tuple[int, str]] = []
        self.material_calls: list[tuple[int, str]] = []

    def exclude_card(self, card_id: int, user_id: str) -> int | None:
        self.card_calls.append((card_id, user_id))
        return self.card_material_id

    def exclude_material(self, material_id: int, user_id: str) -> bool:
        self.material_calls.append((material_id, user_id))
        return self.material_exists


def test_delete_card_returns_idempotent_scope_and_owner() -> None:
    """卡片删除响应应保留资料定位，并只向仓储传递认证用户。"""
    transaction = DeletionTransaction(card_material_id=12)
    service = ReviewService(repository=FakeReviewRepository(transaction))

    result = service.delete_card(81, "7")

    assert result.model_dump() == {
        "scope": "CARD",
        "materialId": 12,
        "cardId": 81,
        "deleted": True,
    }
    assert transaction.card_calls == [(81, "7")]


def test_delete_card_hides_missing_or_cross_user_card() -> None:
    """不存在或越权卡片统一返回稳定业务错误。"""
    service = ReviewService(repository=FakeReviewRepository(DeletionTransaction(card_material_id=None)))

    with pytest.raises(BusinessError, match="复习卡片不存在"):
        service.delete_card(81, "7")


def test_delete_material_keeps_rag_owner_boundary() -> None:
    """资料级排除应返回 MATERIAL scope，越权资料不得暴露。"""
    transaction = DeletionTransaction(material_exists=True)
    service = ReviewService(repository=FakeReviewRepository(transaction))

    result = service.delete_material(12, "7")

    assert result.scope == "MATERIAL"
    assert result.materialId == 12
    assert result.cardId is None
    assert transaction.material_calls == [(12, "7")]

    missing_service = ReviewService(repository=FakeReviewRepository(DeletionTransaction(material_exists=False)))
    with pytest.raises(BusinessError, match="学习资料不存在"):
        missing_service.delete_material(12, "7")


def test_batch_delete_deduplicates_and_uses_one_transaction() -> None:
    """批量卡片和资料删除应去重排序，并返回实际命中的 ID。"""
    transaction = DeletionTransaction(card_material_id=12, material_exists=True)
    repository = FakeReviewRepository(transaction)
    service = ReviewService(repository=repository)

    card_result = service.delete_cards([82, 81, 82], "7")
    material_result = service.delete_materials([13, 12, 13], "7")

    assert card_result.requestedCount == 2
    assert card_result.cardIds == [81, 82]
    assert transaction.card_calls == [(81, "7"), (82, "7")]
    assert material_result.requestedCount == 2
    assert material_result.materialIds == [12, 13]
    assert transaction.material_calls == [(12, "7"), (13, "7")]


def test_current_index_with_legacy_extractor_still_requires_regeneration() -> None:
    """索引版本未变但 Prompt 已升级时，旧卡片不能被幂等检查直接跳过。"""
    base = dict(
        material_id=12,
        title="Kafka 高可用",
        document_type="pdf",
        material_status="READY",
        is_learning_content=True,
        category="技术原理",
        status="GENERATED",
        reason="已生成",
        card_count=3,
        index_request_version=1,
        synced_index_request_version=1,
        updated_at=NOW,
    )

    assert material_generation_is_current(ReviewMaterialRecord(**base, extractor="local"), 1) is False
    assert material_generation_is_current(
        ReviewMaterialRecord(**base, extractor=CURRENT_REVIEW_EXTRACTORS[0]),
        1,
    ) is True


def test_overview_exposes_only_cards_actionable_within_daily_limit() -> None:
    """顶部提醒数量必须扣除今日已完成额度，避免继续提醒不可操作卡片。"""
    settings = ReviewSettingsRecord("7", True, 0.90, 20, "09:00", "Asia/Shanghai")

    result = overview_response(ReviewOverviewStats(10, 18, 30, 3, NOW), settings)

    assert result.dueCount == 10
    assert result.actionableDueCount == 2


def test_stale_generation_is_discarded_after_material_index_version_changes() -> None:
    """模型提炼期间索引版本变化时，旧结果不能覆盖新资料状态。"""
    class StaleGenerationCursor:
        """返回更新后的资料版本，并记录是否执行过复习结果写入。"""

        def __init__(self) -> None:
            self.statements: list[str] = []
            self.current_statement = ""

        def execute(self, statement, _params) -> None:
            self.current_statement = str(statement)
            self.statements.append(self.current_statement)

        def fetchone(self):
            if "SELECT index_request_version" in self.current_statement:
                return {"index_request_version": 2}
            return {
                "material_id": 12,
                "title": "Kafka 高可用",
                "document_type": "pdf",
                "material_status": "READY",
                "index_request_version": 2,
                "synced_index_request_version": None,
                "is_learning_content": None,
                "category": None,
                "review_status": "PENDING",
                "reason": None,
                "extractor": None,
                "card_count": 0,
                "review_updated_at": NOW,
            }

    cursor = StaleGenerationCursor()
    transaction = DatabaseReviewTransaction(cursor, "learning_evidence")
    transaction._statement = lambda query: query  # type: ignore[method-assign]
    material = MaterialSourceRecord(12, "Kafka 高可用", "7", "pdf", "READY", None, 1, NOW)

    result = transaction.save_generation(
        material,
        is_learning_content=True,
        category="技术原理",
        status="GENERATED",
        reason="模型完成提炼",
        extractor="model:review-card-v2",
        cards=[],
    )

    assert result.index_request_version == 2
    assert result.status == "PENDING"
    assert not any("INSERT INTO" in statement for statement in cursor.statements)
