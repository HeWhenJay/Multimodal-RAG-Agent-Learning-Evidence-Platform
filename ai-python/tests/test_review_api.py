"""复习公开路由、认证归属与参数校验测试。"""

from datetime import datetime, timezone
from threading import Event
import time

from fastapi.testclient import TestClient

from app.api.auth import get_auth_service
from app.api.review import get_review_service
from app.auth.service import AuthBusinessError
from app.main import app
from app.schemas.auth import AuthUserResponse
from app.schemas.review import (
    ReviewCard,
    ReviewBatchDeletionResult,
    ReviewCardContent,
    ReviewCardLibrary,
    ReviewCardLibraryMaterial,
    ReviewCardRewritePreview,
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
    ReviewMaterialCardSnapshot,
    ReviewMaterialRewritePreview,
    ReviewMissingKnowledgeResult,
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
        self.generation_event = Event()

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
                    folderId=7,
                    folderName="后端面试",
                    dueCardCount=1,
                    cards=[card],
                )
            ],
        )

    def get_card(self, card_id: int, user_id: str) -> ReviewCard:
        self.remember(user_id)
        assert card_id == 81
        return sample_card()

    def list_card_library(self, user_id: str) -> ReviewCardLibrary:
        """模拟返回包含已复习卡片的全量卡片库。"""
        self.remember(user_id)
        return ReviewCardLibrary(
            totalMaterialCount=1,
            totalCardCount=1,
            reviewedCardCount=1,
            materials=[
                ReviewCardLibraryMaterial(
                    materialId=12,
                    title="Kafka 高可用",
                    documentType="mp4",
                    cardCount=1,
                    reviewedCardCount=1,
                    cards=[sample_card().model_copy(update={"reviewCount": 2})],
                )
            ],
        )

    def preview_card_rewrite(self, card_id: int, payload, user_id: str) -> ReviewCardRewritePreview:
        """模拟生成不落库的单卡片改写候选。"""
        self.remember(user_id)
        assert card_id == 81 and payload.mode == "SOURCE_FIRST"
        return ReviewCardRewritePreview(
            cardId=card_id,
            mode=payload.mode,
            original=ReviewCardContent(question="ISR 有什么作用？", answer="原答案"),
            proposed=ReviewCardContent(question="ISR 的核心作用是什么？", answer="- **跟踪**同步副本"),
            evidenceRefs=[],
            modelName="gpt-5.6-terra",
        )

    def preview_material_rewrite(self, material_id: int, payload, user_id: str) -> ReviewMaterialRewritePreview:
        """模拟生成不落库的资料合并候选。"""
        self.remember(user_id)
        assert material_id == 12 and payload.mode == "SOURCE_FIRST"
        return ReviewMaterialRewritePreview(
            materialId=material_id,
            title="Kafka 高可用",
            sourceVersion=7,
            originalFingerprint="0123456789abcdef",
            originalCardIds=[81],
            originalCards=[
                ReviewMaterialCardSnapshot(
                    cardId=81,
                    content=ReviewCardContent(question="ISR 有什么作用？", answer="原答案"),
                )
            ],
            proposedCards=[
                ReviewMaterialCardSnapshot(
                    content=ReviewCardContent(question="Kafka 如何保证高可用？", answer="综合答案"),
                )
            ],
            originalSummary="原摘要",
            proposedSummary="新摘要",
            mode=payload.mode,
            modelName="gpt-5.6-terra",
        )

    def update_card(self, card_id: int, payload, user_id: str) -> ReviewCard:
        """模拟应用用户确认后的 Markdown 卡片正文。"""
        self.remember(user_id)
        assert card_id == 81
        return sample_card().model_copy(
            update={
                "question": payload.question,
                "answer": payload.answer,
                "hint": payload.hint,
                "isUserEdited": True,
            }
        )

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

    def reorder_folder_materials(self, folder_id: int, material_ids: list[int], user_id: str) -> ReviewGroupOrderResult:
        """记录文件夹排序接口使用的认证用户与完整顺序。"""
        self.remember(user_id)
        assert folder_id == 7 and material_ids == [13, 12]
        return ReviewGroupOrderResult(materialIds=material_ids, orderedCount=2)

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
        self.generation_event.set()
        return sample_material()

    def prepare_material_generation(self, material_id: int, user_id: str) -> ReviewMaterial:
        """模拟立即入队并返回后台生成状态。"""
        self.remember(user_id)
        assert material_id == 12
        return sample_material().model_copy(
            update={
                "status": "GENERATING",
                "reason": "已收到人工说明，生成请求已转入后台队列",
                "needsManualReview": False,
            }
        )

    def create_manual_card(self, material_id: int, payload, user_id: str) -> ReviewCard:
        """模拟用户手动建卡，并记录认证用户。"""
        self.remember(user_id)
        assert material_id == 12
        assert payload.question == "类方法如何定义和调用？"
        return sample_card().model_copy(
            update={
                "question": payload.question,
                "answer": payload.answer,
                "hint": payload.hint,
                "sourceType": "MANUAL",
                "evidenceRefs": [],
            }
        )

    def supplement_missing_knowledge(self, material_id: int, payload, user_id: str, progress_callback=None) -> ReviewMissingKnowledgeResult:
        self.remember(user_id)
        assert material_id == 12
        assert payload.message == "还讲了零拷贝"
        if progress_callback:
            progress_callback({
                "stageCode": "missing.test",
                "stageLabel": "测试阶段",
                "message": "测试补漏处理中",
                "percent": 60,
            })
        return ReviewMissingKnowledgeResult(
            materialId=material_id,
            assistantMessage="已追加 1 张卡片。",
            addedCount=1,
            cards=[sample_card()],
        )

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
    """24 个公开端点只能使用认证用户并保持 Result 信封。"""
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
            client.post("/api/reviews/materials/12/missing-knowledge", headers=headers, json={"message": "还讲了零拷贝"}),
            client.post("/api/reviews/materials/batch-delete", headers=headers, json={"materialIds": [13, 12]}),
            client.delete("/api/reviews/materials/12", headers=headers),
            client.get("/api/reviews/cards/81", headers=headers),
            client.get("/api/reviews/cards/library", headers=headers),
            client.post("/api/reviews/cards/81/rewrite-preview", headers=headers, json={"instruction": "改成列表", "mode": "SOURCE_FIRST"}),
            client.put("/api/reviews/cards/81", headers=headers, json={"question": "ISR 的核心作用是什么？", "answer": "- **跟踪**同步副本", "hint": "回忆 Leader"}),
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
        assert responses[3].json()["data"]["groups"][0]["folderId"] == 7
        assert responses[4].json()["data"] == {"materialIds": [13, 12], "orderedCount": 2}
        assert responses[5].json()["data"][0]["summary"] == "资料讲解 Kafka 分区副本与 ISR 故障转移机制。"
        assert responses[6].json()["data"][0]["name"] == "Python 面试"
        assert responses[8].json()["data"]["materials"][0]["cards"][0]["answer"] is None
        assert responses[11].json()["data"] == {"folderId": 7, "materialIds": [12, 13], "movedCount": 2}
        assert responses[13].json()["data"]["addedCount"] == 1
        assert responses[13].json()["data"]["cards"][0]["id"] == 81
        assert service.generation_event.wait(1)
        assert responses[17].json()["data"]["reviewedCardCount"] == 1
        assert responses[18].json()["data"]["proposed"]["answer"].startswith("-")
        assert responses[19].json()["data"]["isUserEdited"] is True
        assert service.users == ["42"] * 25
    finally:
        app.dependency_overrides.clear()


def test_review_folder_order_route_keeps_result_contract_and_authenticated_owner() -> None:
    """文件夹排序接口必须使用认证用户，并返回统一 Result 信封。"""
    service = StubReviewService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_review_service] = lambda: service
    client = TestClient(app)
    try:
        response = client.put(
            "/api/reviews/folders/7/materials/order",
            headers={"Authorization": "Bearer review-token"},
            json={"materialIds": [13, 12]},
        )
        assert response.status_code == 200
        assert response.json() == {"code": 1, "msg": None, "data": {"materialIds": [13, 12], "orderedCount": 2}}
        assert service.users[-1] == "42"
    finally:
        app.dependency_overrides.clear()


def test_missing_knowledge_task_returns_immediately_and_can_be_reopened() -> None:
    """后台补漏接口提交后立即返回，并允许通过任务编号读取完成结果。"""
    service = StubReviewService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_review_service] = lambda: service
    client = TestClient(app)
    headers = {"Authorization": "Bearer review-token"}
    try:
        created = client.post(
            "/api/reviews/materials/12/missing-knowledge/tasks",
            headers=headers,
            json={"message": "还讲了零拷贝"},
        )
        assert created.status_code == 200
        task = created.json()["data"]
        assert task["materialId"] == 12
        assert task["status"] in {"QUEUED", "RUNNING", "SUCCEEDED"}
        assert task["progress"]["stageCode"] in {"missing.queue", "missing.prepare", "missing.test", "missing.completed"}

        latest = None
        for _ in range(50):
            response = client.get(
                f"/api/reviews/materials/12/missing-knowledge/tasks/{task['taskId']}",
                headers=headers,
            )
            latest = response.json()["data"]
            if latest["status"] in {"SUCCEEDED", "FAILED"}:
                break
            time.sleep(0.01)

        assert latest is not None
        assert latest["status"] == "SUCCEEDED"
        assert latest["result"]["addedCount"] == 1
        assert any(event["stageCode"] == "missing.test" for event in latest["progress"]["events"])
        reopened = client.get(
            "/api/reviews/materials/12/missing-knowledge/tasks/latest",
            headers=headers,
        )
        assert reopened.status_code == 200
        assert reopened.json()["data"]["taskId"] == task["taskId"]
    finally:
        app.dependency_overrides.clear()


def test_card_rewrite_task_runs_in_background_and_restores_comparison() -> None:
    """单卡片改写提交不能等待 LLM，完成后可按最近任务恢复对比。"""

    class BlockingCardRewriteService(StubReviewService):
        """用事件模拟耗时模型，验证 HTTP 请求先于模型完成返回。"""

        def __init__(self) -> None:
            super().__init__()
            self.rewrite_started = Event()
            self.rewrite_release = Event()

        def preview_card_rewrite(self, card_id: int, payload, user_id: str) -> ReviewCardRewritePreview:
            self.rewrite_started.set()
            self.rewrite_release.wait(3)
            return super().preview_card_rewrite(card_id, payload, user_id)

    service = BlockingCardRewriteService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_review_service] = lambda: service
    client = TestClient(app)
    headers = {"Authorization": "Bearer review-token"}
    try:
        started_at = time.monotonic()
        created = client.post(
            "/api/reviews/cards/81/rewrite-tasks",
            headers=headers,
            json={"instruction": "改成列表", "mode": "SOURCE_FIRST"},
        )
        assert time.monotonic() - started_at < 1
        assert created.status_code == 200
        task = created.json()["data"]
        assert task["cardId"] == 81
        assert task["status"] in {"QUEUED", "RUNNING"}
        assert service.rewrite_started.wait(1)

        reused = client.post(
            "/api/reviews/cards/81/rewrite-tasks",
            headers=headers,
            json={"instruction": "改成列表", "mode": "SOURCE_FIRST"},
        )
        assert reused.json()["data"]["taskId"] == task["taskId"]
        service.rewrite_release.set()

        latest = None
        for _ in range(100):
            latest = client.get(
                f"/api/reviews/cards/81/rewrite-tasks/{task['taskId']}",
                headers=headers,
            ).json()["data"]
            if latest["status"] in {"SUCCEEDED", "FAILED"}:
                break
            time.sleep(0.01)
        assert latest["status"] == "SUCCEEDED"
        assert latest["result"]["proposed"]["answer"].startswith("-")
        assert latest["progress"]["stageCode"] == "rewrite.card.completed"
        reopened = client.get("/api/reviews/cards/81/rewrite-tasks/latest", headers=headers)
        assert reopened.json()["data"]["taskId"] == task["taskId"]
        applied = client.put(
            "/api/reviews/cards/81",
            headers=headers,
            json={"question": "ISR 的核心作用是什么？", "answer": "应用候选"},
        )
        assert applied.json()["code"] == 1
        assert client.get(
            "/api/reviews/cards/81/rewrite-tasks/latest",
            headers=headers,
        ).json()["data"] is None
        mismatched = client.get(
            f"/api/reviews/cards/82/rewrite-tasks/{task['taskId']}",
            headers=headers,
        )
        assert mismatched.json()["code"] == 0
    finally:
        service.rewrite_release.set()
        app.dependency_overrides.clear()


def test_material_rewrite_task_runs_in_background_and_restores_comparison() -> None:
    """资料合并预览后台生成，完成后仍只返回待确认的前后对比。"""

    class BlockingMaterialRewriteService(StubReviewService):
        """阻塞资料合并模型调用，验证创建任务立即返回。"""

        def __init__(self) -> None:
            super().__init__()
            self.rewrite_release = Event()

        def preview_material_rewrite(self, material_id: int, payload, user_id: str) -> ReviewMaterialRewritePreview:
            self.rewrite_release.wait(3)
            return super().preview_material_rewrite(material_id, payload, user_id)

    service = BlockingMaterialRewriteService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_review_service] = lambda: service
    client = TestClient(app)
    headers = {"Authorization": "Bearer review-token"}
    try:
        started_at = time.monotonic()
        created = client.post(
            "/api/reviews/materials/12/rewrite-tasks",
            headers=headers,
            json={"instruction": "合并成一张", "mode": "SOURCE_FIRST"},
        )
        assert time.monotonic() - started_at < 1
        task = created.json()["data"]
        assert task["materialId"] == 12
        assert task["status"] in {"QUEUED", "RUNNING"}
        service.rewrite_release.set()

        latest = None
        for _ in range(100):
            latest = client.get(
                f"/api/reviews/materials/12/rewrite-tasks/{task['taskId']}",
                headers=headers,
            ).json()["data"]
            if latest["status"] in {"SUCCEEDED", "FAILED"}:
                break
            time.sleep(0.01)
        assert latest["status"] == "SUCCEEDED"
        assert len(latest["result"]["originalCards"]) == 1
        assert len(latest["result"]["proposedCards"]) == 1
        assert latest["progress"]["stageCode"] == "rewrite.material.completed"
        reopened = client.get("/api/reviews/materials/12/rewrite-tasks/latest", headers=headers)
        assert reopened.json()["data"]["taskId"] == task["taskId"]
    finally:
        service.rewrite_release.set()
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
        assert response.json()["data"]["status"] == "GENERATING"
        assert service.generation_event.wait(1)
        assert service.feedbacks == ["只保留视频中的 Kafka 原始问题"]
    finally:
        app.dependency_overrides.clear()


def test_manual_card_route_keeps_result_contract_and_authenticated_owner() -> None:
    """用户手动建卡接口必须使用当前认证用户并返回手动来源标识。"""
    service = StubReviewService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_review_service] = lambda: service
    client = TestClient(app)
    try:
        response = client.post(
            "/api/reviews/materials/12/cards",
            headers={"Authorization": "Bearer review-token"},
            json={
                "question": "类方法如何定义和调用？",
                "answer": "使用 @classmethod 定义，第一个参数通常命名为 cls。",
                "hint": "回忆 cls 与 self 的区别",
            },
        )
        assert response.status_code == 200
        assert response.json()["code"] == 1
        assert response.json()["data"]["sourceType"] == "MANUAL"
        assert service.users == ["42"]
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
