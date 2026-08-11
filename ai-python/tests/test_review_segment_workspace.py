"""交互式复习分段工作台的原文、并发生成和原子合并测试。"""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import time

import pytest

from app.core.result import BusinessError
from app.review.knowledge_extractor import (
    ExtractionResult,
    KnowledgePoint,
    LearningMaterialContext,
    ReviewExtractionError,
)
from app.review.generation_graph import ReviewManualReviewRequired
from app.review.repository import (
    MaterialSourceRecord,
    ReviewCardRecord,
    ReviewMaterialRecord,
    ReviewSettingsRecord,
)
from app.review.service import ReviewService
from app.schemas.rag import Evidence
from app.schemas.review import ReviewCardUpdateRequest, ReviewSegmentMergeRequest


NOW = datetime(2026, 8, 10, 7, 0, tzinfo=timezone.utc)


def make_evidence(index: int) -> Evidence:
    """构造带稳定位置和时间码的测试 evidence。"""
    return Evidence(
        evidenceId=f"e-{index}",
        documentId="material-31",
        documentTitle="Redis 缓存面试",
        title="Redis 缓存面试",
        sectionName=f"缓存章节 {index // 5 + 1}",
        snippet=f"Redis 缓存穿透第 {index} 条：布隆过滤器可以快速判断 key 是否可能存在。",
        source="upload",
        documentType="mp4",
        score=1.0,
        retrievalSource="summary",
        startTime=f"00:{index:02d}",
        endTime=f"00:{index + 1:02d}",
    )


class SegmentExtractor:
    """记录每段独立提示词，并让第二段稳定返回可重试失败。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, int]] = []

    def extract(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
        *,
        user_feedback: str | None = None,
        generation_mode: str = "STANDARD",
        progress_callback=None,
        **_kwargs,
    ) -> ExtractionResult:
        self.calls.append((evidences[0].evidenceId, user_feedback or "", generation_mode, len(evidences)))
        if progress_callback:
            progress_callback({
                "stageCode": "review.actor",
                "stageLabel": "模型生成",
                "message": "正在生成第 2/8 版复习卡片",
                "percent": 40,
                "attempt": 2,
                "maxAttempts": 8,
                "detail": "正在修复面试问题表达",
            })
        if evidences[0].evidenceId == "e-24":
            raise ReviewExtractionError("第二段未通过 evidence 门禁", diagnostics=["缺少布隆过滤器误判率证据"])
        source = evidences[0]
        return ExtractionResult(
            is_learning_content=True,
            category="INTERVIEW_PREP",
            reason="测试资料",
            knowledge_points=(KnowledgePoint(
                source_key=f"knowledge:{source.evidenceId}",
                question=f"面试官：你会如何解释 {source.evidenceId} 这一段？",
                answer=source.snippet,
                hint="先说判断逻辑，再说边界",
                evidence_refs=(source,),
            ),),
            extractor="model:test",
            summary=f"摘要：{source.sectionName}",
        )


class ManualReviewSegmentExtractor(SegmentExtractor):
    """模拟单卡门禁通过但多卡合并未收敛，必须保留最后候选供人工选择。"""

    def extract(self, material, evidences, **kwargs):
        candidate = super().extract(material, evidences, **kwargs)
        raise ReviewManualReviewRequired(
            "多卡片合并未收敛，需要人工选择",
            attempts=1,
            quality_feedback=["卡片 2 的 answer 未通过逐论断 evidence 忠实度校验"],
            last_valid_result=candidate,
        )


class SegmentTransaction:
    """提供分段工作台所需的最小内存事务，模拟真实资料归属和发布行为。"""

    def __init__(self, *, cards: list[ReviewCardRecord] | None = None) -> None:
        self.material = MaterialSourceRecord(
            31, "Redis 缓存面试", "7", "mp4", "READY", "原始摘要", 4, NOW
        )
        self.evidences = [make_evidence(index) for index in range(25)]
        self.cards = list(cards or [])
        self.review_material = ReviewMaterialRecord(
            material_id=31,
            title=self.material.title,
            document_type="mp4",
            material_status="READY",
            is_learning_content=True,
            category="INTERVIEW_PREP",
            status="GENERATED",
            reason="已生成",
            extractor="model:test",
            card_count=len(self.cards),
            index_request_version=4,
            synced_index_request_version=4,
            updated_at=NOW,
            summary="原始摘要",
        )
        self.publish_called = False

    def find_material(self, material_id: int, user_id: str):
        return self.material if material_id == 31 and user_id == "7" else None

    def is_material_excluded(self, material_id: int, user_id: str) -> bool:
        return False

    def list_active_cards_for_material(self, material_id: int, user_id: str):
        return list(self.cards) if material_id == 31 and user_id == "7" else []

    def list_evidences(self, material: MaterialSourceRecord, limit: int = 320):
        return self.evidences[:limit]

    def find_review_material(self, material_id: int, user_id: str):
        return self.review_material if material_id == 31 and user_id == "7" else None

    def get_or_create_settings(self, user_id: str, *, for_update: bool = False):
        return ReviewSettingsRecord(user_id, True, 0.9, 20, "09:00", "Asia/Shanghai")

    def publish_segment_cards_for_material(self, material, original_card_ids, cards, *, summary=None):
        if sorted(original_card_ids) != sorted(card.id for card in self.cards):
            return None
        self.publish_called = True
        inserted: list[ReviewCardRecord] = []
        for index, draft in enumerate(cards, start=1):
            inserted.append(ReviewCardRecord(
                id=300 + index,
                material_id=31,
                user_id="7",
                material_title=material.title,
                document_type=material.document_type,
                question=draft.question,
                answer=draft.answer,
                hint=draft.hint,
                evidence_refs_json=draft.evidence_refs_json,
                fsrs_card_json=draft.fsrs_card_json,
                due_at=draft.due_at,
                retrievability=1.0,
                review_count=0,
                lapse_count=0,
                active=True,
                created_at=NOW,
                updated_at=NOW,
                source_key=draft.source_key,
                material_summary=summary,
            ))
        self.cards = inserted
        self.review_material = replace(self.review_material, card_count=len(inserted), summary=summary)
        return inserted


class SegmentRepository:
    """把内存事务包装成 ReviewService 所需的仓储接口。"""

    def __init__(self, transaction: SegmentTransaction) -> None:
        self.value = transaction

    @contextmanager
    def transaction(self):
        yield self.value


def service_with(transaction: SegmentTransaction, extractor: SegmentExtractor | None = None) -> ReviewService:
    """创建不依赖真实模型和数据库的分段服务。"""
    return ReviewService(
        repository=SegmentRepository(transaction),
        extractor=extractor or SegmentExtractor(),
        now_provider=lambda: NOW,
    )


def test_workspace_splits_raw_evidence_and_keeps_stable_ids() -> None:
    """工作台必须展示 evidence 原文，并按 24 条上限切成稳定分段。"""
    transaction = SegmentTransaction()
    service = service_with(transaction)

    first = service.get_segment_workspace(31, "7")
    second = service.get_segment_workspace(31, "7")

    assert len(first.segments) == 2
    assert [item.segmentId for item in first.segments] == [item.segmentId for item in second.segments]
    assert first.segments[0].evidenceCount == 24
    assert "[e-0]" in first.segments[0].rawContent
    assert "00:00" in first.segments[0].rawContent
    assert first.originalCardIds == []


def test_selected_segments_use_independent_prompts_and_keep_partial_failure() -> None:
    """只生成所选段，逐段提示词独立传递，失败段不能污染成功段。"""
    transaction = SegmentTransaction()
    extractor = SegmentExtractor()
    service = service_with(transaction, extractor)
    workspace = service.get_segment_workspace(31, "7")
    events: list[dict[str, object]] = []

    result = service.generate_selected_segments(
        31,
        "7",
        [workspace.segments[0].segmentId, workspace.segments[1].segmentId],
        {
            workspace.segments[0].segmentId: "重点追问缓存穿透",
            workspace.segments[1].segmentId: "重点追问误判率",
        },
        mode="RELAXED",
        progress_callback=events.append,
    )

    assert {item[0] for item in extractor.calls} == {"e-0", "e-24"}
    assert {item[1] for item in extractor.calls} == {"重点追问缓存穿透", "重点追问误判率"}
    assert {item[2] for item in extractor.calls} == {"RELAXED"}
    assert [item.status for item in result.segments] == ["SUCCEEDED", "FAILED"]
    assert result.segments[0].cards[0].evidenceIds == ["e-0"]
    assert result.segments[1].qualityFeedback == ["缺少布隆过滤器误判率证据"]
    assert events[-1]["percent"] == 94
    model_event = next(item for item in events if item["stageCode"] == "review.actor")
    assert int(model_event["percent"]) > 5
    assert model_event["attempt"] == 2
    assert model_event["maxAttempts"] == 8
    assert model_event["currentSegmentId"] in {item.segmentId for item in workspace.segments}


def test_segment_timeout_returns_failed_result_without_blocking_whole_round(monkeypatch) -> None:
    """单段模型失联超过预算后必须收敛为可重试失败，不能让整轮永久等待。"""
    class SlowSegmentExtractor(SegmentExtractor):
        """模拟超过单段预算后才返回的同步模型调用。"""

        def __init__(self) -> None:
            super().__init__()
            self.budgets = []

        def extract(self, *args, **kwargs):
            self.budgets.append(kwargs["execution_budget"])
            time.sleep(0.15)
            return super().extract(*args, **kwargs)

    monkeypatch.setenv("REVIEW_SEGMENT_TIMEOUT_SECONDS", "0.05")
    transaction = SegmentTransaction()
    extractor = SlowSegmentExtractor()
    service = service_with(transaction, extractor)
    workspace = service.get_segment_workspace(31, "7")
    started_at = time.monotonic()

    result = service.generate_selected_segments(
        31,
        "7",
        [workspace.segments[0].segmentId],
        {},
        mode="RELAXED",
    )

    assert time.monotonic() - started_at < 0.14
    assert result.segments[0].status == "FAILED"
    assert "超过执行时间预算" in result.segments[0].qualityFeedback[0]
    assert extractor.budgets[0].cancelled is True


def test_failed_segment_keeps_last_valid_cards_for_manual_selection() -> None:
    """多卡门禁失败时，仍应把单卡门禁通过的候选返回工作台，而不是丢弃。"""
    transaction = SegmentTransaction()
    service = service_with(transaction, ManualReviewSegmentExtractor())
    workspace = service.get_segment_workspace(31, "7")

    result = service.generate_selected_segments(
        31,
        "7",
        [workspace.segments[0].segmentId],
        {},
        mode="RELAXED",
    )

    segment_result = result.segments[0]
    assert segment_result.status == "FAILED"
    assert segment_result.candidateAvailable is True
    assert len(segment_result.cards) == 1
    assert segment_result.cards[0].evidenceIds == ["e-0"]
    assert "卡片 2" in segment_result.qualityFeedback[0]


def test_merge_segment_candidates_supports_first_generation_and_rejects_unsupported_answer() -> None:
    """首次生成可以原子发布；编辑后答案不忠实时必须拒绝写库。"""
    transaction = SegmentTransaction()
    service = service_with(transaction)
    workspace = service.get_segment_workspace(31, "7")
    generated = service.generate_selected_segments(31, "7", [workspace.segments[0].segmentId], {}, mode="RELAXED")
    candidate = generated.segments[0].cards[0]

    with pytest.raises(BusinessError, match="忠实度校验"):
        service.apply_segment_cards(
            31,
            ReviewSegmentMergeRequest(
                sourceVersion=workspace.sourceVersion,
                originalFingerprint=workspace.originalFingerprint,
                originalCardIds=[],
                proposedCards=[ReviewCardUpdateRequest(
                    question=candidate.content.question,
                    answer="这段原文没有提到的 Broker 自动扩容结论。",
                    hint=candidate.content.hint,
                    rewriteMode="STRICT_SOURCE",
                    evidenceIds=candidate.evidenceIds,
                )],
            ),
            "7",
        )
    assert transaction.publish_called is False

    applied = service.apply_segment_cards(
        31,
        ReviewSegmentMergeRequest(
            sourceVersion=workspace.sourceVersion,
            originalFingerprint=workspace.originalFingerprint,
            originalCardIds=[],
            proposedCards=[ReviewCardUpdateRequest(
                **candidate.content.model_dump(),
                rewriteMode="STRICT_SOURCE",
                evidenceIds=candidate.evidenceIds,
            )],
            proposedSummary="用户确认的缓存穿透摘要",
        ),
        "7",
    )
    assert transaction.publish_called is True
    assert applied.material.cardCount == 1
    assert applied.cards[0].evidenceRefs[0].evidenceId == "e-0"
