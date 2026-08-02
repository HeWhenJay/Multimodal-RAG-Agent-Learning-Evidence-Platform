"""复习公开路由、认证归属与参数校验测试。"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.api.auth import get_auth_service
from app.api.review import get_review_service
from app.auth.service import AuthBusinessError
from app.main import app
from app.schemas.auth import AuthUserResponse
from app.schemas.review import (
    ReviewCard,
    ReviewBatchDeletionResult,
    ReviewCardGroup,
    ReviewDeletionResult,
    ReviewDueGroups,
    ReviewGradeResult,
    ReviewMaterial,
    ReviewOverview,
    ReviewSettings,
    ReviewSyncResult,
)


NOW = datetime(2026, 8, 1, 1, 0, tzinfo=timezone.utc)


class StaticAuthService:
    """向公开路由提供固定登录用户。"""

    def current_user(self, token: str | None) -> AuthUserResponse:
        """仅接受测试令牌。"""
        if token != "review-token":
            raise AuthBusinessError("登录状态已失效")
        return AuthUserResponse(id=42, account="review-user", displayName="复习用户", role="USER")


class StubReviewService:
    """记录路由传入的权威用户 ID。"""

    def __init__(self) -> None:
        self.users: list[str] = []

    def remember(self, user_id: str) -> None:
        """记录一次服务调用。"""
        self.users.append(user_id)

    def sync(self, user_id: str, limit: int) -> ReviewSyncResult:
        self.remember(user_id)
        assert limit == 1
        return ReviewSyncResult(processedMaterialCount=1, generatedCardCount=3)

    def overview(self, user_id: str) -> ReviewOverview:
        self.remember(user_id)
        return ReviewOverview(
            dueCount=1,
            todayReviewedCount=0,
            totalCardCount=3,
            activeMaterialCount=1,
            nextDueAt=NOW,
            settings=ReviewSettings(),
        )

    def list_due(self, user_id: str, limit: int) -> list[ReviewCard]:
        self.remember(user_id)
        assert limit == 20
        return [sample_card()]

    def list_due_groups(self, user_id: str, limit: int) -> ReviewDueGroups:
        self.remember(user_id)
        assert limit == 20
        card = sample_card().model_copy(update={"answer": None, "evidenceRefs": []})
        return ReviewDueGroups(
            totalDueCount=1,
            remainingToday=20,
            groups=[
                ReviewCardGroup(
                    materialId=12,
                    materialTitle="Kafka 高可用",
                    documentType="mp4",
                    dueCardCount=1,
                    cards=[card],
                )
            ],
        )

    def get_card(self, card_id: int, user_id: str) -> ReviewCard:
        self.remember(user_id)
        assert card_id == 81
        return sample_card()

    def list_materials(self, user_id: str) -> list[ReviewMaterial]:
        self.remember(user_id)
        return [sample_material()]

    def generate_material(self, material_id: int, user_id: str) -> ReviewMaterial:
        self.remember(user_id)
        assert material_id == 12
        return sample_material()

    def delete_material(self, material_id: int, user_id: str) -> ReviewDeletionResult:
        self.remember(user_id)
        assert material_id == 12
        return ReviewDeletionResult(scope="MATERIAL", materialId=material_id)

    def delete_materials(self, material_ids: list[int], user_id: str) -> ReviewBatchDeletionResult:
        self.remember(user_id)
        assert material_ids == [12, 13]
        return ReviewBatchDeletionResult(
            scope="MATERIAL",
            requestedCount=2,
            deletedCount=2,
            materialIds=material_ids,
        )

    def grade(self, card_id: int, payload, user_id: str) -> ReviewGradeResult:
        self.remember(user_id)
        assert card_id == 81 and payload.rating == 3
        return ReviewGradeResult(
            card=sample_card(),
            previousDueAt=NOW,
            nextDueAt=NOW,
            intervalDays=0.01,
            retrievability=1.0,
        )

    def delete_card(self, card_id: int, user_id: str) -> ReviewDeletionResult:
        self.remember(user_id)
        assert card_id == 81
        return ReviewDeletionResult(scope="CARD", materialId=12, cardId=card_id)

    def delete_cards(self, card_ids: list[int], user_id: str) -> ReviewBatchDeletionResult:
        self.remember(user_id)
        assert card_ids == [81, 82]
        return ReviewBatchDeletionResult(
            scope="CARD",
            requestedCount=2,
            deletedCount=2,
            cardIds=card_ids,
        )

    def update_settings(self, payload: ReviewSettings, user_id: str) -> ReviewSettings:
        self.remember(user_id)
        return payload


def sample_card() -> ReviewCard:
    """构造一张最小公开卡片。"""
    return ReviewCard(
        id=81,
        materialId=12,
        materialTitle="Kafka 高可用",
        documentType="mp4",
        question="ISR 有什么作用？",
        answer="ISR 保存与 Leader 保持同步的副本集合。",
        dueAt=NOW,
        retrievability=0.9,
    )


def sample_material() -> ReviewMaterial:
    """构造已生成卡片的资料状态。"""
    return ReviewMaterial(
        materialId=12,
        title="Kafka 高可用",
        documentType="mp4",
        materialStatus="READY",
        isLearningContent=True,
        category="技术原理",
        status="GENERATED",
        cardCount=3,
        indexRequestVersion=1,
        syncedIndexRequestVersion=1,
    )


def test_review_routes_keep_result_contract_and_authenticated_owner() -> None:
    """13 个公开端点只能使用认证用户并保持 Result 信封。"""
    service = StubReviewService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_review_service] = lambda: service
    client = TestClient(app)
    headers = {"Authorization": "Bearer review-token"}
    try:
        responses = [
            client.post("/api/reviews/sync?limit=1", headers=headers),
            client.get("/api/reviews/overview", headers=headers),
            client.get("/api/reviews/due?limit=20", headers=headers),
            client.get("/api/reviews/due-groups?limit=20", headers=headers),
            client.get("/api/reviews/materials", headers=headers),
            client.post("/api/reviews/materials/12/generate", headers=headers),
            client.post("/api/reviews/materials/batch-delete", headers=headers, json={"materialIds": [13, 12]}),
            client.delete("/api/reviews/materials/12", headers=headers),
            client.get("/api/reviews/cards/81", headers=headers),
            client.post("/api/reviews/cards/81/grade", headers=headers, json={"rating": 3}),
            client.post("/api/reviews/cards/batch-delete", headers=headers, json={"cardIds": [82, 81]}),
            client.delete("/api/reviews/cards/81", headers=headers),
            client.put("/api/reviews/settings", headers=headers, json=ReviewSettings().model_dump()),
        ]

        assert all(response.status_code == 200 for response in responses)
        assert all(response.json()["code"] == 1 for response in responses)
        assert service.users == ["42"] * 13
    finally:
        app.dependency_overrides.clear()


def test_invalid_review_rating_uses_chinese_result_envelope() -> None:
    """评分越界不能泄露 FastAPI 默认 422 结构。"""
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_review_service] = StubReviewService
    client = TestClient(app)
    try:
        response = client.post(
            "/api/reviews/cards/81/grade",
            headers={"Authorization": "Bearer review-token"},
            json={"rating": 5},
        )
        assert response.status_code == 200
        assert response.json() == {"code": 0, "msg": "复习评分必须是 1 到 4", "data": None}
    finally:
        app.dependency_overrides.clear()


def test_empty_batch_delete_uses_result_envelope() -> None:
    """批量删除空数组不能进入服务层，并保持统一中文 Result 信封。"""
    service = StubReviewService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_review_service] = lambda: service
    client = TestClient(app)
    try:
        response = client.post(
            "/api/reviews/cards/batch-delete",
            headers={"Authorization": "Bearer review-token"},
            json={"cardIds": []},
        )
        assert response.status_code == 200
        assert response.json()["code"] == 0
        assert service.users == []
    finally:
        app.dependency_overrides.clear()
