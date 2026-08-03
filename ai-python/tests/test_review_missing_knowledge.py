"""复习资料对话补漏的提取、只追加持久化与服务编排测试。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json

from app.review.fsrs_scheduler import FsrsReviewScheduler
from app.review.missing_knowledge import MissingKnowledgeExtraction, MissingKnowledgeExtractor
from app.review.knowledge_extractor import KnowledgePoint, LearningMaterialContext
from app.review.repository import (
    CURRENT_REVIEW_MODEL_EXTRACTOR,
    DatabaseReviewTransaction,
    MaterialSourceRecord,
    ReviewCardDraft,
    ReviewCardRecord,
    ReviewMaterialRecord,
)
from app.review.service import ReviewService
from app.schemas.rag import Evidence
from app.schemas.review import ReviewMissingKnowledgeRequest


NOW = datetime(2026, 8, 4, 9, 0, tzinfo=timezone.utc)


def sample_evidence(evidence_id: str = "material-12-7") -> Evidence:
    """构造带明确 Kafka 零拷贝事实的原文证据。"""
    return Evidence(
        evidenceId=evidence_id,
        documentId="material-12",
        documentTitle="Kafka 高性能设计",
        title="Kafka 高性能设计",
        sectionName="零拷贝",
        snippet="Kafka 使用零拷贝减少磁盘 IO 和网络 IO 之间的数据复制，从而提升数据传输性能。",
        source="upload",
        documentType="mp4",
        score=1.0,
        retrievalSource="summary",
        metadata={"chunkPosition": 7},
    )


def sample_card(card_id: int = 81, *, question: str = "Kafka 的顺序写为什么能够提升性能？") -> ReviewCardRecord:
    """构造一张已有 FSRS 状态的活动卡片。"""
    return ReviewCardRecord(
        id=card_id,
        material_id=12,
        user_id="42",
        material_title="Kafka 高性能设计",
        document_type="mp4",
        question=question,
        answer="顺序写减少了磁盘寻址开销。",
        hint="回忆磁盘磁头移动",
        evidence_refs_json="[]",
        fsrs_card_json=FsrsReviewScheduler().new_card_json(NOW),
        due_at=NOW,
        retrievability=0.74,
        review_count=5,
        lapse_count=2,
        active=True,
        created_at=NOW,
        updated_at=NOW,
    )


def test_missing_knowledge_validator_accepts_statement_based_course_point_and_deduplicates() -> None:
    """普通课程无需原始问句，LLM 可从陈述式讲解提炼候选，但重复问题必须拒绝。"""
    evidence = sample_evidence()
    extractor = MissingKnowledgeExtractor(provider="deepseek")
    result = extractor.validate_payload(
        LearningMaterialContext(12, "Kafka 高性能设计", "mp4"),
        [evidence],
        {
            "assistantMessage": "找到了零拷贝相关讲解。",
            "cards": [
                {
                    "question": "Kafka 的零拷贝如何提升数据传输性能？",
                    "answer": evidence.snippet,
                    "hint": "从磁盘 IO、网络 IO 和数据复制次数回忆",
                    "evidenceIds": [evidence.evidenceId],
                },
                {
                    "question": "Kafka 的顺序写为什么能够提升性能？",
                    "answer": evidence.snippet,
                    "hint": "回忆磁盘寻址开销",
                    "evidenceIds": [evidence.evidenceId],
                },
            ],
        },
        [sample_card()],
    )

    assert [point.question for point in result.knowledge_points] == ["Kafka 的零拷贝如何提升数据传输性能？"]
    assert result.skipped_count == 1


class RecordingAppendCursor:
    """模拟补漏事务并记录 SQL，证明不会更新或停用旧卡。"""

    def __init__(self) -> None:
        self.statement = ""
        self.params = None
        self.statements: list[str] = []

    def execute(self, statement, params) -> None:
        self.statement = str(statement)
        self.params = params
        self.statements.append(self.statement)

    def fetchone(self):
        if "SELECT lm.index_request_version" in self.statement:
            return {
                "index_request_version": 1,
                "review_material_id": 9,
                "review_status": "GENERATED",
                "extractor": CURRENT_REVIEW_MODEL_EXTRACTOR,
                "review_excluded": False,
            }
        if "INSERT INTO learning_evidence.learning_review_card" in self.statement:
            return {"id": 91}
        return None

    def fetchall(self):
        if "learning_review_card_exclusion" in self.statement:
            return []
        if "SELECT source_key, question" in self.statement:
            return [{"source_key": "old-source", "question": "Kafka 的顺序写为什么能够提升性能？"}]
        if "c.id = ANY" in self.statement:
            card = sample_card(91, question="Kafka 的零拷贝如何提升数据传输性能？")
            return [{
                "id": card.id,
                "material_id": card.material_id,
                "user_id": card.user_id,
                "material_title": card.material_title,
                "document_type": card.document_type,
                "question": card.question,
                "answer": "Kafka 使用零拷贝减少数据复制。",
                "hint": "回忆数据流转",
                "evidence_refs": [],
                "fsrs_card_json": card.fsrs_card_json,
                "due_at": card.due_at,
                "retrievability": 0.0,
                "review_count": 0,
                "lapse_count": 0,
                "active": True,
                "created_at": NOW,
                "updated_at": NOW,
                "material_summary": "Kafka 高性能设计摘要",
            }]
        return []


def test_append_review_cards_never_updates_existing_card_state() -> None:
    """补漏仓储只允许 INSERT 新卡和重算计数，不得出现旧卡 UPDATE。"""
    cursor = RecordingAppendCursor()
    transaction = DatabaseReviewTransaction(cursor, "learning_evidence")
    transaction._statement = lambda query: query.replace("{schema}", "learning_evidence").replace(  # type: ignore[method-assign]
        "{current_model_extractor}",
        f"'{CURRENT_REVIEW_MODEL_EXTRACTOR}'",
    )
    material = MaterialSourceRecord(12, "Kafka 高性能设计", "42", "mp4", "READY", None, 1, NOW)
    draft = ReviewCardDraft(
        source_key="knowledge-new",
        question="Kafka 的零拷贝如何提升数据传输性能？",
        answer="Kafka 使用零拷贝减少数据复制。",
        hint="回忆数据流转",
        evidence_refs_json="[]",
        fsrs_card_json=FsrsReviewScheduler().new_card_json(NOW),
        due_at=NOW,
    )

    inserted = transaction.append_review_cards(material, [draft])

    assert [card.id for card in inserted] == [91]
    assert any("ON CONFLICT (material_id, source_key) DO NOTHING" in statement for statement in cursor.statements)
    assert not any("UPDATE learning_evidence.learning_review_card\n" in statement for statement in cursor.statements)
    assert not any("SET active = FALSE" in statement for statement in cursor.statements)


class SupplementTransaction:
    """为服务测试提供同一份资料、证据和只追加写入。"""

    def __init__(self) -> None:
        self.material = MaterialSourceRecord(12, "Kafka 高性能设计", "42", "mp4", "READY", None, 1, NOW)
        self.existing = sample_card()
        self.appended: list[ReviewCardDraft] = []

    def find_material(self, material_id: int, user_id: str):
        return self.material if (material_id, user_id) == (12, "42") else None

    def is_material_excluded(self, _material_id: int, _user_id: str) -> bool:
        return False

    def find_review_material(self, material_id: int, user_id: str):
        if (material_id, user_id) != (12, "42"):
            return None
        return ReviewMaterialRecord(
            material_id=12,
            title=self.material.title,
            document_type="mp4",
            material_status="READY",
            is_learning_content=True,
            category="课程复习",
            status="GENERATED",
            reason="已生成",
            extractor=CURRENT_REVIEW_MODEL_EXTRACTOR,
            card_count=1,
            index_request_version=1,
            synced_index_request_version=1,
            updated_at=NOW,
        )

    def list_evidences(self, _material):
        return [sample_evidence()]

    def list_active_cards_for_material(self, _material_id: int, _user_id: str):
        return [self.existing]

    def append_review_cards(self, _material, cards: list[ReviewCardDraft]):
        self.appended = cards
        draft = cards[0]
        return [ReviewCardRecord(
            id=91,
            material_id=12,
            user_id="42",
            material_title=self.material.title,
            document_type="mp4",
            question=draft.question,
            answer=draft.answer,
            hint=draft.hint,
            evidence_refs_json=draft.evidence_refs_json,
            fsrs_card_json=draft.fsrs_card_json,
            due_at=draft.due_at,
            retrievability=0.0,
            review_count=0,
            lapse_count=0,
            active=True,
            created_at=NOW,
            updated_at=NOW,
        )]


class SupplementRepository:
    """复用一个内存补漏事务。"""

    def __init__(self, transaction: SupplementTransaction) -> None:
        self.transaction_value = transaction

    @contextmanager
    def transaction(self):
        yield self.transaction_value


class StubMissingExtractor:
    """返回一张有真实 evidence 的补充卡片。"""

    def extract(self, _material, evidences, **_kwargs):
        evidence = evidences[0]
        return MissingKnowledgeExtraction(
            (KnowledgePoint(
                source_key="knowledge-new",
                question="Kafka 的零拷贝如何提升数据传输性能？",
                answer=evidence.snippet,
                hint="从数据复制次数回忆",
                evidence_refs=(evidence,),
            ),),
            "找到零拷贝讲解。",
        )


def test_service_supplement_adds_new_card_without_changing_existing_fsrs_state() -> None:
    """服务只创建全新 FSRS 卡，既有卡片状态在前后保持完全一致。"""
    transaction = SupplementTransaction()
    before = (
        transaction.existing.fsrs_card_json,
        transaction.existing.due_at,
        transaction.existing.review_count,
        transaction.existing.lapse_count,
    )
    service = ReviewService(
        repository=SupplementRepository(transaction),
        missing_knowledge_extractor=StubMissingExtractor(),  # type: ignore[arg-type]
        now_provider=lambda: NOW,
    )

    result = service.supplement_missing_knowledge(
        12,
        ReviewMissingKnowledgeRequest(message="还讲了零拷贝"),
        "42",
    )

    assert result.addedCount == 1
    assert result.cards[0].id == 91
    assert len(transaction.appended) == 1
    assert before == (
        transaction.existing.fsrs_card_json,
        transaction.existing.due_at,
        transaction.existing.review_count,
        transaction.existing.lapse_count,
    )
    assert json.loads(transaction.appended[0].evidence_refs_json)[0]["evidenceId"] == "material-12-7"
