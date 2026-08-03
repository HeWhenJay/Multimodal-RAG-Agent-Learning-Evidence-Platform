"""复习业务层的每日负担控制测试。"""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.result import BusinessError
from app.review.fsrs_scheduler import FsrsReviewScheduler
from app.review.knowledge_extractor import ReviewExtractionError
from app.review.generation_graph import ReviewManualReviewRequired
from app.review.repository import (
    CURRENT_REVIEW_EXTRACTORS,
    DatabaseReviewTransaction,
    MaterialSourceRecord,
    ReviewMaterialRecord,
    ReviewOverviewStats,
    ReviewSettingsRecord,
)
from app.review.service import ReviewService, material_generation_is_current, overview_response
from app.schemas.rag import Evidence
from app.schemas.review import ReviewGradeRequest


NOW = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)


class FakeReviewTransaction:
    """只实现文档额度队列测试所需的仓储操作。"""

    def __init__(self, reviewed_today: int, started_due_materials: int = 0) -> None:
        self.reviewed_today = reviewed_today
        self.started_due_materials = started_due_materials
        self.requested_limit: int | None = None

    def get_or_create_settings(self, user_id: str, *, for_update: bool = False) -> ReviewSettingsRecord:
        return ReviewSettingsRecord(user_id, True, 0.90, 20, "09:00", "Asia/Shanghai")

    def overview_stats(self, user_id: str, **_kwargs) -> ReviewOverviewStats:
        return ReviewOverviewStats(
            4,
            self.reviewed_today,
            30,
            3,
            None,
            due_material_count=3,
            started_due_material_count=self.started_due_materials,
        )

    def list_due_group_cards(
        self,
        user_id: str,
        *,
        now: datetime,
        today_start: datetime,
        tomorrow_start: datetime,
        limit: int,
    ):
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
    """每日 20 份资料且已复习 18 份时，本轮最多选择 2 份资料。"""
    transaction = FakeReviewTransaction(reviewed_today=18)
    service = ReviewService(repository=FakeReviewRepository(transaction), now_provider=lambda: NOW)

    assert service.list_due("7", 20) == []
    assert transaction.requested_limit == 2


def test_due_queue_stops_after_daily_limit_is_reached() -> None:
    """达到每日文档上限且没有已开始资料时不再读取新资料。"""
    transaction = FakeReviewTransaction(reviewed_today=20)
    service = ReviewService(repository=FakeReviewRepository(transaction), now_provider=lambda: NOW)

    assert service.list_due("7", 20) == []
    assert transaction.requested_limit is None


def test_due_queue_keeps_a_started_document_when_new_document_quota_is_full() -> None:
    """文档额度用尽后，仍需读取当天已开始资料的剩余到期卡片。"""
    transaction = FakeReviewTransaction(reviewed_today=20, started_due_materials=1)
    service = ReviewService(repository=FakeReviewRepository(transaction), now_provider=lambda: NOW)

    assert service.list_due("7", 20) == []
    assert transaction.requested_limit == 1


class GradeTransaction:
    """提供评分边界测试所需的最小内存事务。"""

    def __init__(self, due_at: datetime) -> None:
        from app.review.repository import ReviewCardRecord

        self.lock_requested = False
        self.saved = False
        self.material_reviewed_today = False
        self.reviewed_today = 0
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
        return ReviewOverviewStats(1, self.reviewed_today, 1, 1, None)

    def has_material_reviewed_today(self, material_id: int, user_id: str, **_kwargs) -> bool:
        """返回测试资料是否已经占用当天文档额度。"""
        return self.material_reviewed_today

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


def test_grade_allows_remaining_cards_from_a_document_already_started_today() -> None:
    """同一文档当天已开始复习后，即使文档额度已满也允许完成该文档卡片。"""
    transaction = GradeTransaction(NOW)
    transaction.reviewed_today = 20
    transaction.material_reviewed_today = True
    service = ReviewService(repository=GradeRepository(transaction), now_provider=lambda: NOW)

    service.grade(41, ReviewGradeRequest(rating=3), "7")

    assert transaction.saved is True


def test_grade_rejects_a_new_document_after_daily_document_limit() -> None:
    """当天文档额度已满时，新文档卡片不能绕过服务端限制。"""
    transaction = GradeTransaction(NOW)
    transaction.reviewed_today = 20
    service = ReviewService(repository=GradeRepository(transaction), now_provider=lambda: NOW)

    with pytest.raises(BusinessError, match="今日复习文档上限已达到"):
        service.grade(41, ReviewGradeRequest(rating=3), "7")

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
    assert "rm.extractor NOT IN (%s, %s)" in cursor.statement
    assert "INTERVAL '5 minutes'" in cursor.statement
    assert "learning_review_material_exclusion" in cursor.statement
    assert cursor.params == ("7", *CURRENT_REVIEW_EXTRACTORS, 3)


def test_material_queries_only_expose_deepseek_review_summary() -> None:
    """复习资料列表只展示复习提炼摘要，不回退到 RAG 截断摘要。"""
    class RecordingCursor:
        """记录资料列表查询。"""

        def execute(self, statement, _params):
            self.statement = statement

        def fetchall(self):
            return []

    cursor = RecordingCursor()
    transaction = DatabaseReviewTransaction(cursor, "learning_evidence")
    transaction._statement = lambda query: query  # type: ignore[method-assign]

    assert transaction.list_review_materials("7") == []
    assert "THEN rm.summary" in cursor.statement
    assert "ELSE NULL" in cursor.statement
    assert "lm.document_summary" not in cursor.statement
    assert "learning_review_folder_material" in cursor.statement


class SummaryGenerationTransaction:
    """记录生成服务传给仓储的资料摘要。"""

    def __init__(self) -> None:
        self.saved: dict | None = None

    def is_material_excluded(self, material_id: int, user_id: str) -> bool:
        return False

    def list_evidences(self, material: MaterialSourceRecord):
        return [
            Evidence(
                evidenceId=f"material-{material.id}-1",
                documentId=f"material-{material.id}",
                documentTitle=material.title,
                title=material.title,
                sectionName="核心原理",
                snippet="Kafka 使用 ISR 跟踪与 Leader 保持同步的副本。",
                source="upload",
                documentType="pdf",
                score=1.0,
                retrievalSource="summary",
            )
        ]

    def save_generation(self, material: MaterialSourceRecord, **kwargs):
        self.saved = kwargs
        return ReviewMaterialRecord(
            material_id=material.id,
            title=material.title,
            document_type=material.document_type,
            material_status=material.material_status,
            is_learning_content=kwargs["is_learning_content"],
            category=kwargs["category"],
            status=kwargs["status"],
            reason=kwargs["reason"],
            extractor=kwargs["extractor"],
            card_count=0,
            index_request_version=material.index_request_version,
            synced_index_request_version=material.index_request_version,
            updated_at=NOW,
            summary=kwargs["summary"],
        )


class SummaryExtractor:
    """提供可区分的 DeepSeek 提炼摘要，验证 RAG 摘要不会覆盖它。"""

    def __init__(self, summary: str) -> None:
        self.summary = summary

    def extract(self, _material, _evidences):
        return SimpleNamespace(
            is_learning_content=False,
            category="待确认",
            reason="测试摘要优先级",
            knowledge_points=(),
            extractor=CURRENT_REVIEW_EXTRACTORS[0],
            summary=self.summary,
        )


@pytest.mark.parametrize(
    "document_summary",
    [None, "RAG 截断摘要"],
)
def test_generation_always_persists_deepseek_review_summary(
    document_summary: str | None,
) -> None:
    """无论 RAG 索引摘要是否存在，复习中心都保存同次 DeepSeek 总结。"""
    transaction = SummaryGenerationTransaction()
    service = ReviewService(
        repository=FakeReviewRepository(transaction),
        extractor=SummaryExtractor("提炼生成的摘要"),  # type: ignore[arg-type]
        now_provider=lambda: NOW,
    )
    material = MaterialSourceRecord(12, "Kafka 高可用", "7", "pdf", "READY", document_summary, 1, NOW)

    result = service._generate(material, "7", force=True)

    assert result is not None
    assert result.summary == "提炼生成的摘要"
    assert transaction.saved is not None
    assert transaction.saved["summary"] == "提炼生成的摘要"


def test_deepseek_failure_is_persisted_and_deactivates_old_cards() -> None:
    """密钥、请求或质量失败必须保存 FAILED + 空卡片，不能继续展示旧坏卡。"""
    class FailingExtractor:
        """模拟 DeepSeek 质量门禁失败。"""

        def extract(self, _material, _evidences):
            raise ReviewExtractionError("DeepSeek 生成的卡片未通过质量门禁")

    transaction = SummaryGenerationTransaction()
    service = ReviewService(
        repository=FakeReviewRepository(transaction),
        extractor=FailingExtractor(),  # type: ignore[arg-type]
        now_provider=lambda: NOW,
    )
    material = MaterialSourceRecord(12, "MVCC 面试课程", "7", "mp4", "READY", "RAG 开头截断", 1, NOW)

    result = service._generate(material, "7", force=True)

    assert result is not None
    assert result.status == "FAILED"
    assert result.cardCount == 0
    assert result.summary is None
    assert result.reason == "DeepSeek 生成的卡片未通过质量门禁"
    assert transaction.saved is not None
    assert transaction.saved["extractor"] == "failed:review-card-v9"
    assert transaction.saved["cards"] == []


def test_unexpected_extractor_failure_is_persisted_instead_of_remaining_pending() -> None:
    """提取器未预期异常也必须保存 FAILED，避免前端永久显示等待生成。"""
    class BrokenExtractor:
        """模拟模型客户端之外的未预期运行错误。"""

        def extract(self, _material, _evidences):
            raise RuntimeError("不应直接暴露的内部错误")

    transaction = SummaryGenerationTransaction()
    service = ReviewService(
        repository=FakeReviewRepository(transaction),
        extractor=BrokenExtractor(),  # type: ignore[arg-type]
        now_provider=lambda: NOW,
    )
    material = MaterialSourceRecord(12, "RabbitMQ 消息可靠性", "7", "mp4", "READY", None, 1, NOW)

    result = service._generate(material, "7", force=True)

    assert result is not None
    assert result.status == "FAILED"
    assert result.reason == "复习生成遇到未预期错误（RuntimeError），请稍后重新生成"
    assert "不应直接暴露" not in result.reason
    assert transaction.saved is not None
    assert transaction.saved["extractor"] == "failed:review-card-v9"
    assert transaction.saved["quality_feedback"] == [result.reason]


def test_quality_retry_exhaustion_is_persisted_as_manual_review() -> None:
    """复习图耗尽自动修复后，服务必须保存 NEEDS_REVIEW 和质量反馈。"""
    class ManualReviewExtractor:
        """模拟 LangGraph 自动修复耗尽。"""

        def extract(self, _material, _evidences):
            raise ReviewManualReviewRequired(
                "自动修复 6 次后仍未通过复习卡片质量门禁",
                attempts=6,
                quality_feedback=["结构化原始问题覆盖不足：应覆盖 20 个，已覆盖 6 个"],
            )

    transaction = SummaryGenerationTransaction()
    service = ReviewService(
        repository=FakeReviewRepository(transaction),
        extractor=ManualReviewExtractor(),  # type: ignore[arg-type]
        now_provider=lambda: NOW,
    )
    material = MaterialSourceRecord(12, "Kafka 面试课程", "7", "mp4", "READY", None, 1, NOW)

    result = service._generate(material, "7", force=True)

    assert result is not None
    assert result.status == "NEEDS_REVIEW"
    assert result.needsManualReview is True
    assert transaction.saved is not None
    assert transaction.saved["generation_attempts"] == 6
    assert transaction.saved["quality_feedback"]


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


def test_overview_exposes_actionable_documents_within_daily_limit() -> None:
    """顶部提醒按资料计数，并包含当天已开始但仍有到期卡片的资料。"""
    settings = ReviewSettingsRecord("7", True, 0.90, 20, "09:00", "Asia/Shanghai")

    result = overview_response(
        ReviewOverviewStats(
            10,
            18,
            30,
            5,
            NOW,
            due_material_count=5,
            started_due_material_count=1,
        ),
        settings,
    )

    assert result.dueCount == 10
    assert result.actionableDueCount == 3


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
        summary="Kafka 高可用摘要",
        status="GENERATED",
        reason="模型完成提炼",
        extractor="model:review-card-v2",
        cards=[],
    )

    assert result.index_request_version == 2
    assert result.status == "PENDING"
    assert not any("INSERT INTO" in statement for statement in cursor.statements)
