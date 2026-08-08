"""复习卡片全量查看、人工编辑与 LLM 对比预览测试。"""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.core.result import BusinessError
from app.review.card_rewriter import CardRewriter, CardRewriteCandidate, MaterialRewriteCandidate
from app.review.fsrs_scheduler import FsrsReviewScheduler
from app.review.repository import MaterialSourceRecord, ReviewCardRecord, ReviewMaterialRecord, ReviewSettingsRecord
from app.review.service import ReviewService
from app.schemas.rag import Evidence
from app.schemas.review import (
    ReviewCardRewriteRequest,
    ReviewCardUpdateRequest,
    ReviewMaterialRewriteApplyRequest,
    ReviewMaterialRewriteRequest,
)


NOW = datetime(2026, 8, 7, 2, 0, tzinfo=timezone.utc)


def evidence(evidence_id: str = "material-12-1") -> Evidence:
    """构造一条可用于严格改写校验的真实原文。"""
    return Evidence(
        evidenceId=evidence_id,
        documentId="material-12",
        documentTitle="Kafka 高可用",
        title="Kafka 高可用",
        sectionName="ISR",
        snippet="ISR 保存与 Leader 保持同步的副本集合，并用于 Leader 故障后的优先选举。",
        source="upload",
        documentType="pdf",
        score=1.0,
        retrievalSource="summary",
    )


class CardEditingTransaction:
    """提供卡片库、预览和更新所需的最小内存事务。"""

    def __init__(self) -> None:
        scheduler = FsrsReviewScheduler()
        self.card = ReviewCardRecord(
            id=81,
            material_id=12,
            user_id="7",
            material_title="Kafka 高可用",
            document_type="pdf",
            question="ISR 有什么作用？",
            answer="ISR 保存与 Leader 保持同步的副本集合。",
            hint="回忆 Leader 与 Follower",
            evidence_refs_json='[{"evidenceId":"material-12-1"}]',
            fsrs_card_json=scheduler.new_card_json(NOW),
            due_at=NOW,
            retrievability=0.8,
            review_count=3,
            lapse_count=1,
            active=True,
            created_at=NOW,
            updated_at=NOW,
            source_key="knowledge-old",
            material_summary="资料讲解 ISR 与故障转移。",
            folder_id=7,
            folder_name="后端面试",
        )
        self.material = MaterialSourceRecord(12, "Kafka 高可用", "7", "pdf", "READY", None, 1, NOW)
        self.evidences = [evidence()]
        self.update_called = False

    def list_all_active_cards(self, user_id: str):
        return [self.card] if user_id == "7" else []

    def get_or_create_settings(self, user_id: str, *, for_update: bool = False):
        return ReviewSettingsRecord(user_id, True, 0.9, 20, "09:00", "Asia/Shanghai")

    def find_card(self, card_id: int, user_id: str):
        return self.card if card_id == self.card.id and user_id == self.card.user_id else None

    def find_card_for_update(self, card_id: int, user_id: str):
        return self.find_card(card_id, user_id)

    def find_material(self, material_id: int, user_id: str):
        return self.material if material_id == self.material.id and user_id == self.material.user_id else None

    def list_evidences(self, material: MaterialSourceRecord):
        return self.evidences if material.id == self.material.id else []

    def update_card_content(self, card_id: int, user_id: str, **changes):
        current = self.find_card(card_id, user_id)
        if current is None:
            return None
        self.update_called = True
        self.card = replace(
            current,
            question=changes["question"],
            answer=changes["answer"],
            hint=changes["hint"],
            evidence_refs_json=changes.get("evidence_refs_json") or current.evidence_refs_json,
            source_key=f"custom:{current.id}",
            updated_at=NOW,
        )
        return self.card


class CardEditingRepository:
    """复用同一个可观察事务。"""

    def __init__(self, transaction: CardEditingTransaction) -> None:
        self.value = transaction

    @contextmanager
    def transaction(self):
        yield self.value


class StubCardRewriter:
    """返回固定 Markdown 候选，避免测试依赖真实模型。"""

    def rewrite(self, material, card, evidences, *, instruction: str, mode: str):
        assert material.material_id == 12
        assert card.id == 81
        assert instruction == "改成两点列表"
        assert mode == "SOURCE_FIRST"
        return CardRewriteCandidate(
            question="ISR 的核心作用是什么？",
            answer="- **跟踪**同步副本\n- 支持 Leader 故障后的优先选举",
            hint="回忆 `Leader` 与同步副本",
            evidence_refs=(evidences[0],),
            model_name="测试模型",
        )

    def rewrite_material(self, material, cards, evidences, *, instruction: str, mode: str):
        """返回一张覆盖全部现有卡片的资料级候选。"""
        assert instruction == "合并为一张"
        assert mode == "SOURCE_FIRST"
        return MaterialRewriteCandidate(
            summary="Kafka 高性能由顺序写、页缓存和零拷贝共同支撑。",
            question="Kafka 如何实现高性能？",
            answer="- **顺序写**降低磁盘寻址开销\n- **页缓存**批量落盘\n- **零拷贝**减少上下文切换",
            hint="回忆写入、缓存和传输三个层次",
            merge_note=f"已合并 {len(cards)} 张卡片",
            evidence_refs=(evidences[0],),
            model_name="测试模型",
        )


class MaterialEditingTransaction(CardEditingTransaction):
    """补充资料级预览、指纹校验和原子替换所需的内存行为。"""

    def __init__(self) -> None:
        super().__init__()
        self.cards = [
            self.card,
            replace(
                self.card,
                id=82,
                question="页缓存有什么作用？",
                answer="页缓存先在内核空间汇集数据，再批量写入磁盘。",
                source_key="knowledge-page-cache",
            ),
        ]
        self.review_material = ReviewMaterialRecord(
            material_id=12,
            title="Kafka 高可用",
            document_type="pdf",
            material_status="READY",
            is_learning_content=True,
            category="INTERVIEW_PREP",
            status="GENERATED",
            reason="已生成",
            extractor="model:test",
            card_count=2,
            index_request_version=1,
            synced_index_request_version=1,
            updated_at=NOW,
            summary="原资料摘要",
        )
        self.replace_called = False

    def is_material_excluded(self, material_id: int, user_id: str) -> bool:
        return False

    def list_active_cards_for_material(self, material_id: int, user_id: str):
        return list(self.cards) if material_id == 12 and user_id == "7" else []

    def find_review_material(self, material_id: int, user_id: str):
        return self.review_material if material_id == 12 and user_id == "7" else None

    def replace_active_cards_for_material(self, material, original_card_ids, cards, *, summary=None):
        if sorted(original_card_ids) != sorted(card.id for card in self.cards):
            return None
        self.replace_called = True
        draft = cards[0]
        inserted = replace(
            self.card,
            id=90,
            question=draft.question,
            answer=draft.answer,
            hint=draft.hint,
            evidence_refs_json=draft.evidence_refs_json,
            fsrs_card_json=draft.fsrs_card_json,
            due_at=draft.due_at,
            review_count=0,
            lapse_count=0,
            source_key=draft.source_key,
        )
        self.cards = [inserted]
        self.review_material = replace(self.review_material, card_count=1, summary=summary)
        return [inserted]


def test_card_library_includes_reviewed_cards_with_full_markdown_content() -> None:
    """卡片库必须包含已复习卡片，并直接返回答案供查看和编辑。"""
    transaction = CardEditingTransaction()
    service = ReviewService(repository=CardEditingRepository(transaction), now_provider=lambda: NOW)

    result = service.list_card_library("7")

    assert result.totalMaterialCount == 1
    assert result.totalCardCount == 1
    assert result.reviewedCardCount == 1
    assert result.materials[0].cards[0].answer == transaction.card.answer
    assert result.materials[0].cards[0].reviewCount == 3


def test_rewrite_preview_does_not_write_card_before_user_applies() -> None:
    """LLM 预览只返回原卡片和新卡片对比，不得提前更新数据库。"""
    transaction = CardEditingTransaction()
    service = ReviewService(
        repository=CardEditingRepository(transaction),
        card_rewriter=StubCardRewriter(),  # type: ignore[arg-type]
        now_provider=lambda: NOW,
    )

    result = service.preview_card_rewrite(
        81,
        ReviewCardRewriteRequest(instruction="改成两点列表", mode="SOURCE_FIRST"),
        "7",
    )

    assert result.original.question == "ISR 有什么作用？"
    assert result.proposed.answer.startswith("- **跟踪**")
    assert result.evidenceRefs[0].evidenceId == "material-12-1"
    assert transaction.update_called is False


def test_manual_update_preserves_fsrs_progress_and_marks_card_as_user_edited() -> None:
    """应用编辑只更新正文，不改变复习次数、到期时间或 FSRS 状态。"""
    transaction = CardEditingTransaction()
    original_fsrs = transaction.card.fsrs_card_json
    service = ReviewService(repository=CardEditingRepository(transaction), now_provider=lambda: NOW)

    updated = service.update_card(
        81,
        ReviewCardUpdateRequest(
            question="ISR 的核心作用是什么？",
            answer="- **保存**同步副本集合",
            hint="回忆 `ISR`",
        ),
        "7",
    )

    assert updated.isUserEdited is True
    assert updated.reviewCount == 3
    assert updated.dueAt == NOW
    assert transaction.card.fsrs_card_json == original_fsrs
    assert transaction.card.source_key == "custom:81"


def test_strict_source_apply_rejects_answer_not_supported_by_selected_evidence() -> None:
    """严格依赖原文档位下，用户二次编辑后的无依据答案不能被应用。"""
    transaction = CardEditingTransaction()
    service = ReviewService(repository=CardEditingRepository(transaction), now_provider=lambda: NOW)

    with pytest.raises(BusinessError, match="未通过严格原文忠实度校验"):
        service.update_card(
            81,
            ReviewCardUpdateRequest(
                question="ISR 的核心作用是什么？",
                answer="ISR 会自动扩容 Broker 并降低网络成本。",
                hint="回忆扩容",
                rewriteMode="STRICT_SOURCE",
                evidenceIds=["material-12-1"],
            ),
            "7",
        )

    assert transaction.update_called is False


def test_material_rewrite_preview_merges_cards_without_writing() -> None:
    """资料级预览必须返回一张综合卡片，且生成阶段不能替换旧卡片。"""
    transaction = MaterialEditingTransaction()
    service = ReviewService(
        repository=CardEditingRepository(transaction),
        card_rewriter=StubCardRewriter(),  # type: ignore[arg-type]
        now_provider=lambda: NOW,
    )

    preview = service.preview_material_rewrite(
        12,
        ReviewMaterialRewriteRequest(instruction="合并为一张", mode="SOURCE_FIRST"),
        "7",
    )

    assert len(preview.originalCards) == 2
    assert len(preview.proposedCards) == 1
    assert preview.proposedCards[0].content.question == "Kafka 如何实现高性能？"
    assert len(preview.originalFingerprint) == 64
    assert transaction.replace_called is False


def test_material_rewrite_apply_rejects_stale_content_fingerprint() -> None:
    """预览后原卡片正文发生变化时，应用必须拒绝陈旧候选覆盖。"""
    transaction = MaterialEditingTransaction()
    service = ReviewService(
        repository=CardEditingRepository(transaction),
        card_rewriter=StubCardRewriter(),  # type: ignore[arg-type]
        now_provider=lambda: NOW,
    )
    preview = service.preview_material_rewrite(
        12,
        ReviewMaterialRewriteRequest(instruction="合并为一张", mode="SOURCE_FIRST"),
        "7",
    )
    transaction.cards[0] = replace(transaction.cards[0], answer="另一处刚刚更新了答案")

    with pytest.raises(BusinessError, match="卡片内容已被其他操作修改"):
        service.apply_material_rewrite(
            12,
            ReviewMaterialRewriteApplyRequest(
                sourceVersion=preview.sourceVersion,
                originalFingerprint=preview.originalFingerprint,
                originalCardIds=preview.originalCardIds,
                proposedSummary=preview.proposedSummary,
                proposedCards=[
                    ReviewCardUpdateRequest(
                        **preview.proposedCards[0].content.model_dump(),
                        rewriteMode=preview.mode,
                        evidenceIds=preview.proposedCards[0].evidenceIds,
                    )
                ],
            ),
            "7",
        )

    assert transaction.replace_called is False


def test_material_rewrite_apply_replaces_two_cards_with_one_custom_card() -> None:
    """用户确认后应原子替换为一张 custom 综合卡片并更新资料摘要。"""
    transaction = MaterialEditingTransaction()
    service = ReviewService(
        repository=CardEditingRepository(transaction),
        card_rewriter=StubCardRewriter(),  # type: ignore[arg-type]
        now_provider=lambda: NOW,
    )
    preview = service.preview_material_rewrite(
        12,
        ReviewMaterialRewriteRequest(instruction="合并为一张", mode="SOURCE_FIRST"),
        "7",
    )

    result = service.apply_material_rewrite(
        12,
        ReviewMaterialRewriteApplyRequest(
            sourceVersion=preview.sourceVersion,
            originalFingerprint=preview.originalFingerprint,
            originalCardIds=preview.originalCardIds,
            proposedSummary=preview.proposedSummary,
            proposedCards=[
                ReviewCardUpdateRequest(
                    **preview.proposedCards[0].content.model_dump(),
                    rewriteMode=preview.mode,
                    evidenceIds=preview.proposedCards[0].evidenceIds,
                )
            ],
        ),
        "7",
    )

    assert transaction.replace_called is True
    assert result.material.cardCount == 1
    assert len(result.cards) == 1
    assert result.cards[0].isUserEdited is True
    assert result.replacedCardIds == [81, 82]


def test_card_rewriter_retries_cockpit_before_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    """卡片改写遇到 Cockpit 连接错误时必须先重试 Terra，再执行 DeepSeek 降级。"""
    from httpx import Request
    from openai import APIConnectionError

    calls: list[tuple[str, dict]] = []

    class FailingCompletions:
        def create(self, **kwargs):
            calls.append(("primary", kwargs))
            raise APIConnectionError(request=Request("POST", "http://localhost:58966/v1/chat/completions"))

    class FallbackCompletions:
        def create(self, **kwargs):
            calls.append(("fallback", kwargs))
            return SimpleNamespace(choices=[])

    primary_client = SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions()))
    fallback_client = SimpleNamespace(chat=SimpleNamespace(completions=FallbackCompletions()))
    monkeypatch.setenv("REVIEW_LLM_API_KEY", "relay-key")
    monkeypatch.setenv("REVIEW_LLM_FALLBACK_API_KEY", "deepseek-key")
    monkeypatch.setenv("REVIEW_COCKPIT_RETRY_BASE_DELAY_MS", "1")
    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **kwargs: primary_client if kwargs["base_url"] == "http://localhost:58966/v1" else fallback_client,
    )
    rewriter = CardRewriter(provider="deepseek")

    response = rewriter._create_completion(primary_client, {"model": "gpt-5.6-terra", "messages": []})

    assert response.choices == []
    assert [kind for kind, _kwargs in calls] == ["primary", "primary", "fallback"]
    assert calls[2][1]["model"] == "deepseek-v4-flash"
    assert rewriter.active_model_name == "DeepSeek"
