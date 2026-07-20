"""工作台和设置页公开 FastAPI 路由测试。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.auth import get_auth_service
from app.api.page_data import get_page_data_service, router
from app.auth.service import AuthBusinessError
from app.core.result import BusinessError, Result
from app.main import app as main_app
from app.page_data.repository import PageMaterialRecord, PageProgressRecord, SystemSettingRecord
from app.page_data.service import PageDataService
from app.schemas.auth import AuthUserResponse


class StaticAuthService:
    """为页面路由提供固定认证用户。"""

    def current_user(self, token: str | None) -> AuthUserResponse:
        """仅接受测试令牌，验证路由从会话而不是请求参数读取用户。"""
        if token != "page-token":
            raise AuthBusinessError("登录状态已失效")
        return AuthUserResponse(id=42, account="page-user", displayName="页面用户", role="USER")


class InMemoryPageDataRepository:
    """替代 PostgreSQL 的最小页面数据事务实现。"""

    def __init__(self) -> None:
        self.material = PageMaterialRecord(
            id=8,
            title="机器学习课程笔记",
            user_id="42",
            document_type="pdf",
            source="upload",
            status="PARSING",
            parser="mineru",
            document_summary="课程资料摘要",
            chunk_count=18,
            original_filename="course.pdf",
            original_file_path="uploads/course.pdf",
            storage_type="local",
            object_key=None,
            public_url=None,
            created_at=datetime(2026, 7, 20, 9, 0),
            updated_at=datetime(2026, 7, 21, 9, 0),
        )
        self.progress = PageProgressRecord(
            id=1,
            stage="embedding.chunk",
            message="第 2/3 块：生成 embedding",
            success=True,
            context_json=(
                '{"stageCode":"embedding.chunk","stageLabel":"生成 embedding",'
                '"status":"RUNNING","currentStep":7,"totalSteps":8,'
                '"currentChunk":2,"totalChunks":3,"percent":55}'
            ),
            created_at=datetime(2026, 7, 21, 9, 20),
        )
        self.video_progress = PageProgressRecord(
            id=2,
            stage="parse.video.ocr",
            message="识别视频课件文字",
            success=True,
            context_json='{"stageCode":"parse.video.ocr","status":"RUNNING","percent":40}',
            created_at=datetime(2026, 7, 21, 9, 10),
        )
        self.last_material_range: tuple[str, datetime, datetime, int] | None = None

    @contextmanager
    def transaction(self) -> Iterator["InMemoryPageDataRepository"]:
        """内存测试替身不需要真实数据库事务。"""
        yield self

    def material_count(self, user_id: str) -> int:
        """返回当前用户资料总数。"""
        assert user_id == "42"
        return 3

    def material_count_since(self, user_id: str, start_at: datetime) -> int:
        """返回当前用户七日新增资料数。"""
        assert user_id == "42"
        assert start_at == datetime(2026, 7, 14, 15, 30)
        return 2

    def chunk_count(self, user_id: str) -> int:
        """返回当前用户证据切块总数。"""
        assert user_id == "42"
        return 18

    def count_errors_since(self, start_at: datetime) -> int:
        """返回全局近三十日错误数。"""
        assert start_at == datetime(2026, 6, 21, 15, 30)
        return 4

    def count_open_errors_since(self, start_at: datetime) -> int:
        """返回全局近三十日未关闭错误数。"""
        assert start_at == datetime(2026, 6, 21, 15, 30)
        return 1

    def list_materials_between(
        self,
        user_id: str,
        start_at: datetime,
        end_at: datetime,
        limit: int,
    ) -> list[PageMaterialRecord]:
        """记录日期钳制后的查询范围并返回一条资料。"""
        self.last_material_range = (user_id, start_at, end_at, limit)
        return [self.material]

    def list_progress(self, material_id: int, limit: int, video_only: bool = False) -> list[PageProgressRecord]:
        """模拟常规和视频关键阶段日志，其中一条重复记录用于去重断言。"""
        assert material_id == 8
        if video_only:
            assert limit == 80
            return [self.progress, self.video_progress]
        assert limit == 40
        return [self.progress]

    def list_settings(self) -> list[SystemSettingRecord]:
        """返回已经按数据库排序规则排列的设置项。"""
        return [
            SystemSettingRecord(
                key="rag.embedding.model",
                group="RAG",
                label="Embedding 模型",
                value="text-embedding-v4",
                sort_order=10,
            )
        ]


def build_test_app() -> FastAPI:
    """构造仅挂载页面数据路由的应用。"""
    app = FastAPI()

    @app.exception_handler(BusinessError)
    async def handle_business_error(_: Request, error: BusinessError) -> JSONResponse:
        """模拟主应用的 Java 兼容异常转换。"""
        return JSONResponse(status_code=200, content=Result.failure(error.message).model_dump())

    app.include_router(router)
    return app


def test_main_application_registers_logs_and_page_data_routes() -> None:
    """生产 FastAPI 主应用必须公开注册日志和页面数据迁移接口。"""
    paths = registered_route_paths(main_app.routes)
    assert {
        "/api/logs/events",
        "/api/logs/events/batch",
        "/api/logs/errors",
        "/api/logs/internal/events",
        "/api/logs/internal/errors",
        "/api/logs/events/recent",
        "/api/logs/errors/recent",
        "/api/logs/overview",
        "/api/page-data/dashboard",
        "/api/page-data/settings",
    }.issubset(paths)


def registered_route_paths(routes: object) -> set[str]:
    """兼容 FastAPI 惰性 `_IncludedRouter` 和普通路由列表。"""
    paths: set[str] = set()
    for route in routes:  # type: ignore[union-attr]
        path = getattr(route, "path", None)
        if isinstance(path, str):
            paths.add(path)
        nested_router = getattr(route, "original_router", None)
        nested_routes = getattr(nested_router, "routes", None)
        if nested_routes is not None:
            paths.update(registered_route_paths(nested_routes))
    return paths


def test_dashboard_uses_authenticated_user_clamps_dates_and_merges_progress() -> None:
    """工作台统计隔离用户，日期限制与 Java 相同，并合并去重进度。"""
    repository = InMemoryPageDataRepository()
    service = PageDataService(repository=repository, clock=lambda: datetime(2026, 7, 21, 15, 30))
    app = build_test_app()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_page_data_service] = lambda: service
    client = TestClient(app)
    try:
        response = client.get(
            "/api/page-data/dashboard?startDate=2026-06-01&endDate=2026-08-01&recentDays=1&recentLimit=99",
            headers={"Authorization": "Bearer page-token"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["code"] == 1
        assert body["data"]["materialCount"] == 3
        assert body["data"]["materialDelta7Days"] == 2
        assert body["data"]["evidenceCount"] == 18
        assert body["data"]["openErrorCount"] == 1
        assert body["data"]["errorCount30Days"] == 4
        assert body["data"]["recentTaskStartDate"] == "2026-07-15"
        assert body["data"]["recentTaskEndDate"] == "2026-07-21"
        assert body["data"]["recentTaskLimit"] == 50
        assert repository.last_material_range == (
            "42",
            datetime(2026, 7, 15),
            datetime(2026, 7, 22),
            50,
        )
        material = body["data"]["recentMaterials"][0]
        assert material["userId"] == "42"
        assert material["latestProgress"]["stageCode"] == "embedding.chunk"
        assert [item["stageCode"] for item in material["progressEvents"]] == [
            "embedding.chunk",
            "parse.video.ocr",
        ]
    finally:
        app.dependency_overrides.clear()


def test_page_data_auth_validation_and_public_settings_keep_result_contract() -> None:
    """认证失败、非法查询参数和无鉴权设置页均返回既有信封。"""
    repository = InMemoryPageDataRepository()
    app = build_test_app()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_page_data_service] = lambda: PageDataService(repository=repository)
    client = TestClient(app)
    try:
        missing_token = client.get("/api/page-data/dashboard")
        assert missing_token.status_code == 200
        assert missing_token.json() == {"code": 0, "msg": "登录状态已失效", "data": None}

        invalid_date = client.get(
            "/api/page-data/dashboard?startDate=2026/07/21",
            headers={"Authorization": "Bearer page-token"},
        )
        assert invalid_date.status_code == 200
        assert invalid_date.json() == {"code": 0, "msg": "开始日期参数不合法", "data": None}

        settings_response = client.get("/api/page-data/settings")
        assert settings_response.status_code == 200
        assert settings_response.json() == {
            "code": 1,
            "msg": None,
            "data": [
                {
                    "key": "rag.embedding.model",
                    "group": "RAG",
                    "label": "Embedding 模型",
                    "value": "text-embedding-v4",
                    "sortOrder": 10,
                }
            ],
        }
    finally:
        app.dependency_overrides.clear()
