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
    ReviewFolder,
    ReviewFolderAssignmentResult,
    ReviewFolderDeletionResult,
    ReviewFolderDetail,
    ReviewFolderMaterial,
    ReviewGroupOrderResult,
    ReviewGradeResult,
    ReviewMaterial,
    ReviewMaterialFolderRequest,
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
        self.feedbacks: list[str | None] = []

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
                    materialSummary="资料讲解 Kafka 分区副本与 ISR 故障转移机制。",
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

    def reorder_due_groups(self, material_ids: list[int], user_id: str) -> ReviewGroupOrderResult:
        """记录排序接口使用的认证用户与拖拽顺序。"""
        self.remember(user_id)
        assert material_ids == [13, 12]
        return ReviewGroupOrderResult(materialIds=material_ids, orderedCount=2)

    def list_materials(self, user_id: str) -> list[ReviewMaterial]:
        self.remember(user_id)
        return [sample_material()]

    def list_folders(self, user_id: str) -> list[ReviewFolder]:
        self.remember(user_id)
        return [sample_folder()]

    def create_folder(self, name: str, user_id: str) -> ReviewFolder:
        self.remember(user_id)
        assert name == "Python 面试"
        return sample_folder().model_copy(update={"name": name})

    def get_folder(self, folder_id: int, user_id: str) -> ReviewFolderDetail:
        self.remember(user_id)
        assert folder_id == 7
        return ReviewFolderDetail(
            folder=sample_folder(),
            materials=[
                ReviewFolderMaterial(
                    materialId=12,
                    title="Kafka 高可用",
                    documentType="mp4",
                    cardCount=1,
                    cards=[sample_card().model_copy(update={"answer": None, "evidenceRefs": []})],
                )
            ],
        )

    def rename_folder(self, folder_id: int, name: str, user_id: str) -> ReviewFolder:
        self.remember(user_id)
        assert folder_id == 7 and name == "后端面试"
        return sample_folder().model_copy(update={"name": name})

    def delete_folder(self, folder_id: int, user_id: str) -> ReviewFolderDeletionResult:
        self.remember(user_id)
        assert folder_id == 7
        return ReviewFolderDeletionResult(folderId=folder_id, unfiledMaterialCount=1)

    def assign_materials_to_folder(
        self,
        payload: ReviewMaterialFolderRequest,
        user_id: str,
    ) -> ReviewFolderAssignmentResult:
        self.remember(user_id)
        assert payload.materialIds == [12, 13] and payload.folderId == 7
        return ReviewFolderAssignmentResult(folderId=7, materialIds=payload.materialIds, movedCount=2)

    def generate_material(self, material_id: int, user_id: str, user_feedback: str | None = None) -> ReviewMaterial:
        self.remember(user_id)
        assert material_id == 12
        self.feedbacks.append(user_feedback)
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
        summary="资料讲解 Kafka 分区副本与 ISR 故障转移机制。",
        documentType="mp4",
        materialStatus="READY",
        isLearningContent=True,
        category="技术原理",
        status="GENERATED",
        cardCount=3,
        indexRequestVersion=1,
        syncedIndexRequestVersion=1,
    )


def sample_folder() -> ReviewFolder:
    """构造一个包含到期卡片统计的复习文件夹。"""
    return ReviewFolder(id=7, name="Python 面试", materialCount=1, cardCount=3, dueCardCount=1, updatedAt=NOW)


def test_review_routes_keep_result_contract_and_authenticated_owner() -> None:
    """20 个公开端点只能使用认证用户并保持 Result 信封。"""
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
            client.put("/api/reviews/due-groups/order", headers=headers, json={"materialIds": [13, 12]}),
            client.get("/api/reviews/materials", headers=headers),
            client.get("/api/reviews/folders", headers=headers),
            client.post("/api/reviews/folders", headers=headers, json={"name": " Python  面试 "}),
            client.get("/api/reviews/folders/7", headers=headers),
            client.patch("/api/reviews/folders/7", headers=headers, json={"name": "后端面试"}),
            client.delete("/api/reviews/folders/7", headers=headers),
            client.put("/api/reviews/materials/folder", headers=headers, json={"materialIds": [12, 13], "folderId": 7}),
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
        assert responses[3].json()["data"]["groups"][0]["materialSummary"] == (
            "资料讲解 Kafka 分区副本与 ISR 故障转移机制。"
        )
        assert responses[4].json()["data"] == {"materialIds": [13, 12], "orderedCount": 2}
        assert responses[5].json()["data"][0]["summary"] == "资料讲解 Kafka 分区副本与 ISR 故障转移机制。"
        assert responses[6].json()["data"][0]["name"] == "Python 面试"
        assert responses[8].json()["data"]["materials"][0]["cards"][0]["answer"] is None
        assert responses[11].json()["data"] == {"folderId": 7, "materialIds": [12, 13], "movedCount": 2}
        assert service.users == ["42"] * 20
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


def test_generate_review_accepts_optional_human_feedback() -> None:
    """人工处理终态可以通过请求体把补充说明传入服务层。"""
    service = StubReviewService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_review_service] = lambda: service
    client = TestClient(app)
    try:
        response = client.post(
            "/api/reviews/materials/12/generate",
            headers={"Authorization": "Bearer review-token"},
            json={"userFeedback": "只保留视频中的 Kafka 原始问题"},
        )
        assert response.status_code == 200
        assert response.json()["code"] == 1
        assert service.feedbacks == ["只保留视频中的 Kafka 原始问题"]
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


def test_duplicate_group_order_ids_are_rejected_before_service_call() -> None:
    """拖拽顺序含重复资料时不得进入业务事务。"""
    service = StubReviewService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_review_service] = lambda: service
    client = TestClient(app)
    try:
        response = client.put(
            "/api/reviews/due-groups/order",
            headers={"Authorization": "Bearer review-token"},
            json={"materialIds": [12, 12]},
        )
        assert response.status_code == 200
        assert response.json() == {"code": 0, "msg": "请求参数不合法", "data": None}
        assert service.users == []
    finally:
        app.dependency_overrides.clear()
