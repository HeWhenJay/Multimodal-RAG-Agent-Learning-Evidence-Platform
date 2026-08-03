"""复习文件夹的文档归档、详情分组与权限错误测试。"""

from contextlib import contextmanager
from datetime import datetime, timezone
import json

import pytest

from app.core.result import BusinessError
from app.review.fsrs_scheduler import FsrsReviewScheduler
from app.review.repository import (
    ReviewCardRecord,
    ReviewFolderRecord,
    ReviewMaterialRecord,
    ReviewSettingsRecord,
)
from app.review.service import ReviewService
from app.schemas.review import ReviewMaterialFolderRequest


NOW = datetime(2026, 8, 3, 1, 0, tzinfo=timezone.utc)


class FolderTransaction:
    """提供文件夹服务测试所需的最小事务行为。"""

    def __init__(self) -> None:
        self.folder = ReviewFolderRecord(7, "42", "Python 面试", 1, 2, 1, NOW)
        self.assignment: tuple[str, list[int], int | None] | None = None
        self.allow_assignment = True

    def find_review_folder(self, folder_id: int, user_id: str, *, now: datetime):
        assert now == NOW
        return self.folder if folder_id == 7 and user_id == "42" else None

    def list_review_materials_in_folder(self, folder_id: int, user_id: str, limit: int = 100):
        assert (folder_id, user_id, limit) == (7, "42", 100)
        return [
            ReviewMaterialRecord(
                material_id=12,
                title="Python 基础面经",
                document_type="mp4",
                material_status="READY",
                is_learning_content=True,
                category="面试复习",
                status="GENERATED",
                reason="包含结构化问题",
                extractor="model:review-card-v8",
                card_count=2,
                index_request_version=1,
                synced_index_request_version=1,
                updated_at=NOW,
                summary="资料按原视频问题顺序讲解 Python 面试考点。",
                folder_id=7,
                folder_name="Python 面试",
            )
        ]

    def list_review_cards_in_folder(self, folder_id: int, user_id: str):
        assert (folder_id, user_id) == (7, "42")
        scheduler = FsrsReviewScheduler()
        return [
            ReviewCardRecord(
                id=card_id,
                material_id=12,
                user_id="42",
                material_title="Python 基础面经",
                document_type="mp4",
                question=f"Python 考点 {card_id} 的核心机制是什么？",
                answer=f"Python 考点 {card_id} 的核心机制由原视频解释。",
                hint="关注视频中该考点的定义和行为",
                evidence_refs_json=json.dumps([]),
                fsrs_card_json=scheduler.new_card_json(NOW),
                due_at=NOW,
                retrievability=1.0,
                review_count=0,
                lapse_count=0,
                active=True,
                created_at=NOW,
                updated_at=NOW,
            )
            for card_id in (81, 82)
        ]

    def get_or_create_settings(self, user_id: str, *, for_update: bool = False):
        assert user_id == "42" and for_update is False
        return ReviewSettingsRecord("42", True, 0.9, 20, "09:00", "Asia/Shanghai")

    def assign_review_materials_to_folder(self, user_id: str, material_ids: list[int], folder_id: int | None):
        self.assignment = (user_id, material_ids, folder_id)
        return list(material_ids) if self.allow_assignment else None


class FolderRepository:
    """每次服务调用复用同一个内存事务。"""

    def __init__(self, transaction: FolderTransaction) -> None:
        self.value = transaction

    @contextmanager
    def transaction(self):
        yield self.value


def test_folder_detail_groups_all_cards_by_document_without_preloading_answers() -> None:
    """文件夹详情不应用到期截断，并保持答案与 evidence 延迟揭示。"""
    service = ReviewService(repository=FolderRepository(FolderTransaction()), now_provider=lambda: NOW)

    detail = service.get_folder(7, "42")

    assert detail.folder.name == "Python 面试"
    assert detail.folder.cardCount == 2
    assert len(detail.materials) == 1
    assert len(detail.materials[0].cards) == 2
    assert all(card.answer is None and card.evidenceRefs == [] for card in detail.materials[0].cards)


def test_folder_assignment_uses_authenticated_owner_and_rejects_partial_match() -> None:
    """文档批量归档只传递认证用户，任一资料无效时整批失败。"""
    transaction = FolderTransaction()
    service = ReviewService(repository=FolderRepository(transaction), now_provider=lambda: NOW)
    payload = ReviewMaterialFolderRequest(materialIds=[12, 13], folderId=7)

    result = service.assign_materials_to_folder(payload, "42")

    assert result.materialIds == [12, 13]
    assert transaction.assignment == ("42", [12, 13], 7)

    transaction.allow_assignment = False
    with pytest.raises(BusinessError, match="复习资料不存在"):
        service.assign_materials_to_folder(payload, "42")


def test_folder_assignment_rejects_foreign_folder_before_moving_materials() -> None:
    """越权文件夹统一按不存在处理，不能继续写入文档归属。"""
    transaction = FolderTransaction()
    service = ReviewService(repository=FolderRepository(transaction), now_provider=lambda: NOW)

    with pytest.raises(BusinessError, match="复习文件夹不存在"):
        service.assign_materials_to_folder(
            ReviewMaterialFolderRequest(materialIds=[12], folderId=99),
            "42",
        )

    assert transaction.assignment is None
