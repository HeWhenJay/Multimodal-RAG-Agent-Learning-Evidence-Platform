"""Python 公开 RAG 控制面路由与用户边界测试。"""

from __future__ import annotations

from datetime import datetime

from fastapi.testclient import TestClient

from app.api.auth import get_auth_service
from app.api.rag_control import get_rag_control_service
from app.auth.service import AuthBusinessError
from app.main import app
from app.repositories.rag_control import MaterialRecord, ProgressLogRecord
from app.schemas.auth import AuthUserResponse
from app.schemas.rag import QueryResponse, QueryTaskResponse
from app.schemas.rag_control import (
    DshPluginMaterialPageResponse,
    DshPluginMaterialResponse,
    MaterialPreviewResponse,
    MaterialUploadChunkResponse,
    RagMaterialResponse,
    RagOverviewPublicResponse,
    RagQueryHistoryResponse,
    RagRemoteVideoBatchItemResponse,
    RagRemoteVideoBatchResponse,
)
from app.services.rag_control_service import RagControlService


class StaticAuthService:
    """为公开路由提供固定登录用户，避免连接认证数据库。"""

    def current_user(self, token: str | None) -> AuthUserResponse:
        if token != "test-token":
            raise AuthBusinessError("登录状态已失效")
        return AuthUserResponse(id=42, account="rag-user", displayName="RAG 用户", role="USER")


class StubRagControlService:
    """替代控制服务，专门断言 router 不信任请求中的用户信息。"""

    def __init__(self) -> None:
        self.users: list[str] = []

    def _remember(self, user_id: str) -> None:
        self.users.append(user_id)

    def overview(self, user_id: str) -> RagOverviewPublicResponse:
        self._remember(user_id)
        return RagOverviewPublicResponse(materialCount=1, chunkCount=2, evidenceCount=2, lastIndexedTitle="测试资料")

    def list_materials(self, user_id: str) -> list[RagMaterialResponse]:
        self._remember(user_id)
        return [sample_material()]

    def list_dsh_materials(self, user_id: str, **kwargs) -> DshPluginMaterialPageResponse:
        self._remember(user_id)
        assert kwargs["limit"] == 30
        return DshPluginMaterialPageResponse(
            items=[DshPluginMaterialResponse(id=1, title="测试资料", documentType="markdown", source="manual", status="READY")],
            total=1,
            hasMore=False,
        )

    def get_material(self, material_id: int, user_id: str) -> RagMaterialResponse:
        self._remember(user_id)
        assert material_id == 1
        return sample_material()

    def list_evidences(self, material_id: int, user_id: str, limit: int):
        self._remember(user_id)
        assert material_id == 1 and limit == 20
        return []

    def preview_material(self, material_id: int, source: str | None, user_id: str) -> MaterialPreviewResponse:
        self._remember(user_id)
        return MaterialPreviewResponse(
            materialId=material_id,
            title="测试资料",
            documentType="markdown",
            source=source,
            contentType="text/markdown; charset=UTF-8",
            content="# 预览",
        )

    def index_text(self, request, user_id: str) -> RagMaterialResponse:
        self._remember(user_id)
        assert request.title == "手工资料"
        return sample_material()

    def import_remote_video(self, request, user_id: str) -> RagMaterialResponse:
        self._remember(user_id)
        assert request.url == "https://www.bilibili.com/video/BV1xx411c7mD"
        assert request.confirmedAuthorized is True
        return sample_material()

    def import_remote_videos(self, request, user_id: str) -> RagRemoteVideoBatchResponse:
        """断言批量分享原文同样只使用认证用户。"""
        self._remember(user_id)
        assert "【课程】https://www.bilibili.com/video/BV1xx411c7mD" in request.text
        assert request.confirmedAuthorized is True
        material = sample_material()
        return RagRemoteVideoBatchResponse(
            candidateCount=1,
            queuedCount=1,
            items=[
                RagRemoteVideoBatchItemResponse(
                    lineNumber=1,
                    url="https://www.bilibili.com/video/BV1xx411c7mD",
                    canonicalUrl="https://www.bilibili.com/video/BV1xx411c7mD",
                    status="QUEUED",
                    message="已加入 RAG 处理队列",
                    material=material,
                )
            ],
        )

    def upload_material(self, *, filename, content, content_type, high_precision, user_id: str) -> RagMaterialResponse:
        self._remember(user_id)
        assert filename == "note.md" and content == b"# note" and high_precision is True
        return sample_material()

    def upload_chunk(self, **kwargs) -> MaterialUploadChunkResponse:
        self._remember(kwargs["user_id"])
        assert kwargs["chunk_index"] == 0 and kwargs["total_chunks"] == 1
        return MaterialUploadChunkResponse(
            uploadId="upload-1",
            filename="video.mp4",
            chunkIndex=0,
            totalChunks=1,
            receivedChunks=1,
            nextChunkIndex=1,
            status="PROCESSING",
            message="已完成",
            completed=True,
            material=sample_material(),
        )

    def reindex_material(self, material_id: int, high_precision: bool, user_id: str) -> RagMaterialResponse:
        self._remember(user_id)
        assert material_id == 1 and high_precision is True
        return sample_material()

    def query(self, request, user_id: str) -> QueryResponse:
        self._remember(user_id)
        assert request.question == "资料够吗？"
        return sample_query()

    def list_query_history(self, user_id: str, start_date, end_date, limit):
        self._remember(user_id)
        return [sample_history()]

    def start_query_task(self, request, user_id: str) -> QueryTaskResponse:
        self._remember(user_id)
        return sample_task("task-1")

    def get_query_task(self, task_id: str, user_id: str) -> QueryTaskResponse:
        self._remember(user_id)
        assert task_id == "task-1"
        return sample_task(task_id)


def sample_material() -> RagMaterialResponse:
    """构造一条前端兼容资料。"""
    return RagMaterialResponse(
        id=1,
        title="测试资料",
        userId="42",
        documentType="markdown",
        source="manual",
        status="READY",
        chunkCount=2,
        createdAt=datetime(2026, 7, 20, 10, 0, 0),
        updatedAt=datetime(2026, 7, 20, 10, 0, 0),
    )


def sample_query() -> QueryResponse:
    """构造拒答查询结果，避免测试调用模型。"""
    return QueryResponse(
        answer="当前知识库没有检索到足够相关的证据。",
        answerStatus="REFUSED",
        refusalReason="NO_EVIDENCE",
        expandedQueries=["资料够吗？"],
        evidences=[],
    )


def sample_task(task_id: str) -> QueryTaskResponse:
    """构造可直接被前端轮询的完成任务。"""
    now = "2026-07-20T10:00:00"
    return QueryTaskResponse(
        taskId=task_id,
        status="COMPLETED",
        message="RAG 检索问答完成",
        result=sample_query(),
        createdAt=now,
        updatedAt=now,
    )


def sample_history() -> RagQueryHistoryResponse:
    """构造历史快照以覆盖公开响应序列化。"""
    return RagQueryHistoryResponse(
        id=1,
        question="资料够吗？",
        answer="当前知识库没有检索到足够相关的证据。",
        status="COMPLETED",
        topK=5,
        createdAt=datetime(2026, 7, 20, 10, 0, 0),
        updatedAt=datetime(2026, 7, 20, 10, 0, 0),
    )


def test_public_rag_routes_keep_result_contract_and_auth_ownership() -> None:
    """15 个公开路径均使用认证用户并保持 Java `Result` 信封。"""
    service = StubRagControlService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_rag_control_service] = lambda: service
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-token"}
    try:
        responses = [
            client.get("/api/rag/overview", headers=headers),
            client.get("/api/rag/materials", headers=headers),
            client.get("/api/rag/materials/1", headers=headers),
            client.get("/api/rag/materials/1/evidences?limit=20", headers=headers),
            client.get("/api/rag/materials/1/preview", headers=headers),
            client.post(
                "/api/rag/materials/text",
                headers=headers,
                json={"title": "手工资料", "documentType": "markdown", "source": "manual", "content": "# 内容"},
            ),
            client.post(
                "/api/rag/materials/url",
                headers=headers,
                json={
                    "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                    "highPrecision": False,
                    "confirmedAuthorized": True,
                },
            ),
            client.post(
                "/api/rag/materials/url/batch",
                headers=headers,
                json={
                    "text": "【课程】https://www.bilibili.com/video/BV1xx411c7mD",
                    "highPrecision": False,
                    "confirmedAuthorized": True,
                },
            ),
            client.post(
                "/api/rag/materials/upload",
                headers=headers,
                data={"highPrecision": "true"},
                files={"file": ("note.md", b"# note", "text/markdown")},
            ),
            client.post(
                "/api/rag/materials/upload/chunk",
                headers=headers,
                data={
                    "filename": "video.mp4",
                    "chunkIndex": "0",
                    "totalChunks": "1",
                    "totalSize": "5",
                    "highPrecision": "false",
                },
                files={"file": ("video.mp4", b"12345", "video/mp4")},
            ),
            client.post("/api/rag/materials/1/reindex?highPrecision=true", headers=headers),
            client.post(
                "/api/rag/query",
                headers=headers,
                json={"question": "资料够吗？", "metadataFilter": {"userId": "attacker"}},
            ),
            client.get("/api/rag/query/history", headers=headers),
            client.post("/api/rag/query/tasks", headers=headers, json={"question": "资料够吗？"}),
            client.get("/api/rag/query/tasks/task-1", headers=headers),
        ]
        assert all(response.status_code == 200 for response in responses)
        assert all(response.json()["code"] == 1 for response in responses)
        assert service.users == ["42"] * 15
    finally:
        app.dependency_overrides.clear()


def test_material_detail_response_contains_processing_snapshot() -> None:
    """资料详情接口使用的响应转换必须返回统一处理快照。"""

    class ProgressTransaction:
        """返回一条向量生成事件的最小事务替身。"""

        def list_progress(self, material_id: int, limit: int) -> list[ProgressLogRecord]:
            """模拟资料详情查询最近进度。"""
            assert material_id == 9
            assert limit == 30
            return [
                ProgressLogRecord(
                    stage="embedding.chunk",
                    message="第 2/4 块：生成 embedding",
                    success=True,
                    context_json=(
                        '{"stageCode":"embedding.chunk","stageLabel":"生成 embedding",'
                        '"status":"RUNNING","currentStep":7,"totalSteps":8,'
                        '"currentChunk":2,"totalChunks":4,"percent":60}'
                    ),
                    created_at=datetime(2026, 8, 1, 10, 1),
                )
            ]

    material = MaterialRecord(
        id=9,
        title="课程视频",
        user_id="42",
        document_type="mp4",
        source="upload",
        status="PARSING",
        parser="video-rag-v6",
        document_summary=None,
        chunk_count=0,
        original_filename="course.mp4",
        original_file_path="oss://private/course.mp4",
        storage_type="oss",
        object_key="private/course.mp4",
        public_url=None,
        active_index_job_id="job-9",
        index_request_version=1,
        created_at=datetime(2026, 8, 1, 10, 0),
        updated_at=datetime(2026, 8, 1, 10, 0),
    )
    service = object.__new__(RagControlService)

    response = service._material_response(ProgressTransaction(), material)

    assert response.processingProgress is not None
    assert response.processingProgress.currentPhaseCode == "EMBEDDING"
    assert response.processingProgress.currentChunk == 2
    assert response.processingProgress.totalChunks == 4
    assert response.processingProgress.percent == 60


def test_public_rag_validation_and_missing_token_use_result_envelope() -> None:
    """公开 RAG 校验或会话失败不能返回 FastAPI 默认 422/401 结构。"""
    service = StubRagControlService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_rag_control_service] = lambda: service
    client = TestClient(app)
    try:
        missing_token = client.get("/api/rag/overview")
        assert missing_token.status_code == 200
        assert missing_token.json() == {"code": 0, "msg": "登录状态已失效", "data": None}

        invalid_query = client.post("/api/rag/query", headers={"Authorization": "Bearer test-token"}, json={})
        assert invalid_query.status_code == 200
        assert invalid_query.json() == {"code": 0, "msg": "问题不能为空", "data": None}
    finally:
        app.dependency_overrides.clear()


def test_dsh_plugin_rag_routes_require_no_login_and_use_fixed_partition(monkeypatch) -> None:
    """DSH 插件本机路由不读取登录令牌，并固定资料分区。"""
    service = StubRagControlService()
    app.dependency_overrides[get_rag_control_service] = lambda: service
    monkeypatch.setenv("DSH_PLUGIN_RAG_USER_ID", "dsh-study")
    client = TestClient(app)
    try:
        overview_response = client.get("/api/dsh-plugin/rag/overview")
        materials_response = client.get("/api/dsh-plugin/rag/materials")
        preview_response = client.get("/api/dsh-plugin/rag/materials/1/preview")
        query_response = client.post("/api/dsh-plugin/rag/query", json={"question": "资料够吗？"})
        video_response = client.post(
            "/api/dsh-plugin/rag/materials/url",
            json={
                "url": "https://www.bilibili.com/video/BV1xx411c7mD",
                "confirmedAuthorized": True,
            },
        )
        assert overview_response.status_code == 200 and overview_response.json()["code"] == 1
        assert materials_response.status_code == 200 and materials_response.json()["code"] == 1
        assert preview_response.status_code == 200 and preview_response.json()["code"] == 1
        assert query_response.status_code == 200 and query_response.json()["code"] == 1
        assert video_response.status_code == 200 and video_response.json()["code"] == 1
        assert service.users == ["dsh-study"] * 5
    finally:
        app.dependency_overrides.clear()


def test_public_query_scope_overrides_client_user_and_visibility() -> None:
    """服务端必须覆盖客户端伪造的 userId 与 visibilityScope。"""
    service = RagControlService(repository=object(), store=object(), parser_router=object(), object_storage=object(), executor=object())
    scoped = service._scoped_query_request(
        type("Request", (), {
            "question": "查询我的资料",
            "topK": 50,
            "candidateMultiplier": 1,
            "metadataFilter": {"userId": "attacker", "visibilityScope": "staging", "documentType": "pdf", "unknown": "x"},
        })(),
        "42",
    )
    assert scoped.topK == 20
    assert scoped.candidateMultiplier == 2
    assert scoped.metadataFilter == {"documentType": "pdf", "userId": "42", "visibilityScope": "private"}
