"""复习卡片分组、答案揭示与生成短锁测试。"""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json

from app.review.fsrs_scheduler import FsrsReviewScheduler
from app.review.generation_guard import ReviewGenerationGuard
from app.review.repository import (
    DatabaseReviewTransaction,
    ReviewCardRecord,
    ReviewOverviewStats,
    ReviewSettingsRecord,
)
from app.review.service import ReviewService
from app.schemas.rag import Evidence


NOW = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)


def card(
    card_id: int,
    material_id: int,
    title: str,
    *,
    folder_id: int | None = None,
    folder_name: str | None = None,
) -> ReviewCardRecord:
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
        material_summary=f"{title}资料摘要",
        folder_id=folder_id,
        folder_name=folder_name,
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
        raise AssertionError("分组队列不应调用普通到期查询")

    def list_due_group_cards(
        self,
        user_id: str,
        *,
        now: datetime,
        today_start: datetime,
        tomorrow_start: datetime,
        limit: int,
    ) -> list[ReviewCardRecord]:
        """按仓储已经确定的资料优先级返回卡片。"""
        material_ids = list(dict.fromkeys(item.material_id for item in self.cards))[:limit]
        return [item for item in self.cards if item.material_id in material_ids]

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
    assert [group.materialSummary for group in result.groups] == [
        "Kafka 高可用资料摘要",
        "Redis 持久化资料摘要",
    ]
    assert all(card.answer is None for group in result.groups for card in group.cards)
    assert all(card.evidenceRefs == [] for group in result.groups for card in group.cards)
    assert result.groups[0].cards[0].hint == "先回忆核心概念"


def test_due_group_keeps_folder_location_for_archived_material() -> None:
    """文件夹内到期资料仍进入今日队列，并返回前端跳转所需的文件夹定位。"""
    repository = GroupingRepository()
    repository.value.cards = [card(4, 14, "Kafka 零拷贝", folder_id=7, folder_name="消息中间件")]
    service = ReviewService(repository=repository, now_provider=lambda: NOW)

    result = service.list_due_groups("7", 20)

    assert len(result.groups) == 1
    assert result.groups[0].folderId == 7
    assert result.groups[0].folderName == "消息中间件"
    assert result.groups[0].cards[0].materialId == 14


def test_selected_document_returns_all_six_due_cards_without_group_truncation() -> None:
    """文档入选今日队列后必须返回全部到期卡片，不能再固定截断为四张。"""
    repository = GroupingRepository()
    repository.value.cards = [card(card_id, 12, "Faiss 的使用") for card_id in range(1, 7)]
    service = ReviewService(repository=repository, now_provider=lambda: NOW)

    result = service.list_due_groups("7", 1)

    assert len(result.groups) == 1
    assert result.groups[0].dueCardCount == 6
    assert len(result.groups[0].cards) == 6


def test_due_card_query_maps_deepseek_review_summary() -> None:
    """数据库到期队列只联结当前已生成的 DeepSeek 复习摘要。"""
    class DueCursor:
        """返回一张带资料摘要的数据库卡片行。"""

        def execute(self, statement, params) -> None:
            self.statement = statement
            self.params = params

        def fetchall(self):
            return [
                {
                    "id": 1,
                    "material_id": 12,
                    "user_id": "7",
                    "material_title": "Kafka 高可用",
                    "material_summary": "DeepSeek 复习总结",
                    "folder_id": 7,
                    "folder_name": "消息中间件",
                    "document_type": "pdf",
                    "question": "ISR 有什么作用？",
                    "answer": "ISR 跟踪同步副本。",
                    "due_at": NOW,
                    "active": True,
                }
            ]

    cursor = DueCursor()
    transaction = DatabaseReviewTransaction(cursor, "learning_evidence")
    transaction._statement = lambda query: query  # type: ignore[method-assign]

    records = transaction.list_due_cards("7", now=NOW, limit=20)

    assert records[0].material_summary == "DeepSeek 复习总结"
    assert records[0].folder_id == 7
    assert records[0].folder_name == "消息中间件"
    assert "rm.summary AS material_summary" in cursor.statement
    assert "folder_material.folder_id" in cursor.statement
    assert "rm.status = 'GENERATED'" in cursor.statement
    assert "lm.document_summary" not in cursor.statement
    assert "display_order" not in cursor.statement


def test_due_group_query_applies_material_order_without_changing_card_rank() -> None:
    """分组查询应先应用资料优先级，组内仍按到期时间和卡片 ID 排序。"""
    class DueCursor:
        """记录分组到期查询并返回空结果。"""

        def execute(self, statement, params) -> None:
            self.statement = statement
            self.params = params

        def fetchall(self):
            return []

    cursor = DueCursor()
    transaction = DatabaseReviewTransaction(cursor, "learning_evidence")
    transaction._statement = lambda query: query  # type: ignore[method-assign]

    today_start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    tomorrow_start = today_start + timedelta(days=1)
    assert transaction.list_due_group_cards(
        "7",
        now=NOW,
        today_start=today_start,
        tomorrow_start=tomorrow_start,
        limit=20,
    ) == []
    normalized_sql = " ".join(cursor.statement.split())
    assert "rm.display_order AS material_display_order" in normalized_sql
    assert "LEFT JOIN {schema}.learning_review_folder_material folder_material" in normalized_sql
    assert "folder_material.folder_id" in normalized_sql
    assert "folder.name AS folder_name" in normalized_sql
    assert "AND NOT EXISTS ( SELECT 1 FROM {schema}.learning_review_folder_material" not in normalized_sql
    assert "ORDER BY due_cards.material_display_order ASC NULLS LAST" in normalized_sql
    assert "MIN(c.due_at) OVER (PARTITION BY c.material_id) AS group_due_at" in normalized_sql
    assert "material_rank <= 4" not in normalized_sql
    assert "new_candidates.new_rank <= GREATEST" in normalized_sql
    assert cursor.params == ("7", today_start, tomorrow_start, "7", "7", NOW, 20)


def test_overview_counts_archived_due_cards_in_daily_queue() -> None:
    """归档只改变资料管理位置，不能从今日到期统计和每日额度中移除卡片。"""
    class OverviewCursor:
        """记录两段概览 SQL 并返回空统计。"""

        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement, _params) -> None:
            self.statements.append(" ".join(str(statement).split()))

        def fetchone(self):
            return {}

    cursor = OverviewCursor()
    transaction = DatabaseReviewTransaction(cursor, "learning_evidence")
    transaction._statement = lambda query: query  # type: ignore[method-assign]
    today_start = datetime(2026, 8, 1, tzinfo=timezone.utc)

    transaction.overview_stats(
        "7",
        now=NOW,
        today_start=today_start,
        tomorrow_start=today_start + timedelta(days=1),
    )

    assert len(cursor.statements) == 2
    assert all("learning_review_folder_material" not in statement for statement in cursor.statements)


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
