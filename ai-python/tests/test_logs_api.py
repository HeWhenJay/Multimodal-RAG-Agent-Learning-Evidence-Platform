"""系统日志公开 FastAPI 路由测试。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.api.logs import get_log_service, router
from app.core.result import BusinessError, Result
from app.logs.repository import LogErrorRecord, LogEventRecord
from app.logs.service import LogService


class InMemoryLogRepository:
    """替代 PostgreSQL 的最小日志事务实现。"""

    def __init__(self) -> None:
        self.events: list[LogEventRecord] = []
        self.errors: list[LogErrorRecord] = []
        self.material_updates: list[tuple[int, str, str | None, int | None]] = []
        self._next_event_id = 1
        self._next_error_id = 1

    @contextmanager
    def transaction(self) -> Iterator["InMemoryLogRepository"]:
        """内存测试替身不需要实际数据库事务。"""
        yield self

    def insert_event(self, record: LogEventRecord) -> int:
        """保存事件并返回递增主键。"""
        event_id = self._next_event_id
        self._next_event_id += 1
        self.events.append(replace(record, id=event_id, created_at=record.server_time))
        return event_id

    def find_error_id_by_fingerprint(self, fingerprint: str) -> int | None:
        """按指纹查找已保存的错误。"""
        for record in self.errors:
            if record.fingerprint == fingerprint:
                return record.id
        return None

    def insert_error(self, record: LogErrorRecord) -> int:
        """保存新的错误聚合记录。"""
        error_id = self._next_error_id
        self._next_error_id += 1
        self.errors.append(
            replace(
                record,
                id=error_id,
                first_seen_at=record.server_time,
                last_seen_at=record.server_time,
                created_at=record.server_time,
            )
        )
        return error_id

    def increase_error_occurrence(self, fingerprint: str, last_seen_at: datetime) -> None:
        """累加内存错误记录的出现次数。"""
        self.errors = [
            replace(record, occurrence_count=record.occurrence_count + 1, last_seen_at=last_seen_at)
            if record.fingerprint == fingerprint
            else record
            for record in self.errors
        ]

    def update_material_progress(self, material_id: int, status: str, parser: str | None, chunk_count: int | None) -> None:
        """记录 RAG 进度回写，供断言验证。"""
        self.material_updates.append((material_id, status, parser, chunk_count))

    def list_recent_events(self, limit: int) -> list[LogEventRecord]:
        """按创建时间倒序返回事件。"""
        return list(reversed(self.events))[:limit]

    def list_recent_errors(self, limit: int) -> list[LogErrorRecord]:
        """按最后出现时间倒序返回错误。"""
        return sorted(self.errors, key=lambda item: (item.last_seen_at or item.server_time, item.id or 0), reverse=True)[:limit]

    def count_events_since(self, start_at: datetime) -> int:
        """统计指定时间后的事件数量。"""
        return sum((item.created_at or item.server_time) >= start_at for item in self.events)

    def count_errors_since(self, start_at: datetime) -> int:
        """统计指定时间后的错误数量。"""
        return sum((item.created_at or item.server_time) >= start_at for item in self.errors)

    def count_open_errors_since(self, start_at: datetime) -> int:
        """统计仍处于 OPEN 状态的错误数量。"""
        return sum(
            (item.created_at or item.server_time) >= start_at and item.status == "OPEN" for item in self.errors
        )

    def count_errors_by_source_since(self, source: str, start_at: datetime) -> int:
        """按来源统计指定时间后的错误数量。"""
        return sum(
            (item.created_at or item.server_time) >= start_at and item.source == source for item in self.errors
        )


def build_test_app() -> FastAPI:
    """构造仅挂载日志路由的应用，避免测试依赖真实数据库。"""
    app = FastAPI()

    @app.exception_handler(BusinessError)
    async def handle_business_error(_: Request, error: BusinessError) -> JSONResponse:
        """保持生产应用的 Java 兼容错误信封。"""
        return JSONResponse(status_code=200, content=Result.failure(error.message).model_dump())

    app.include_router(router)
    return app


def test_log_routes_keep_result_contract_internal_token_and_error_aggregation() -> None:
    """事件、批量、内部日志、错误聚合和查询端点均可脱离 PostgreSQL 验证。"""
    now = datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc)
    repository = InMemoryLogRepository()
    service = LogService(
        repository=repository,
        clock=lambda: now,
        internal_token="worker-token",
        max_batch_size=2,
    )
    app = build_test_app()
    app.dependency_overrides[get_log_service] = lambda: service
    client = TestClient(app)
    try:
        event_response = client.post(
            "/api/logs/events",
            json={
                "domain": "rag",
                "module": "material",
                "stage": "index.completed",
                "eventType": "rag_progress",
                "action": "material_index_completed",
                "materialId": 9,
                "context": {"status": "READY", "chunkCount": 3, "password": "never-store"},
            },
        )
        assert event_response.status_code == 200
        assert event_response.json() == {"code": 1, "msg": None, "data": 1}
        assert '"password":"***"' in repository.events[0].context_json
        assert repository.material_updates == [(9, "READY", None, 3)]

        batch_response = client.post(
            "/api/logs/events/batch",
            json=[
                {"module": "batch", "action": "one"},
                {"module": "batch", "action": "two"},
                {"module": "batch", "action": "discarded-by-limit"},
            ],
        )
        assert batch_response.json() == {"code": 1, "msg": None, "data": 2}
        assert len(repository.events) == 3

        denied_internal = client.post(
            "/api/logs/internal/events",
            json={"module": "worker", "action": "progress"},
        )
        assert denied_internal.json() == {"code": 0, "msg": "内部日志令牌无效", "data": None}

        accepted_internal = client.post(
            "/api/logs/internal/events",
            headers={"X-Internal-Log-Token": "worker-token"},
            json={"source": "java", "module": "worker", "action": "progress"},
        )
        assert accepted_internal.json()["code"] == 1
        assert repository.events[-1].source == "python"

        error_payload = {
            "module": "rag_query",
            "errorType": "TimeoutError",
            "errorCode": "RAG_TIMEOUT",
            "message": "RAG 查询 1001 超时",
        }
        first_error = client.post("/api/logs/errors", json=error_payload)
        second_error = client.post("/api/logs/errors", json={**error_payload, "message": "RAG 查询 1002 超时"})
        assert first_error.json()["data"] == second_error.json()["data"] == 1

        recent_error = client.get("/api/logs/errors/recent?limit=10")
        assert recent_error.json()["data"][0]["occurrenceCount"] == 2
        overview = client.get("/api/logs/overview?days=7")
        assert overview.json()["data"] == {
            "eventCount": 4,
            "errorCount": 1,
            "openErrorCount": 1,
            "frontendErrorCount": 0,
            "javaErrorCount": 1,
            "pythonErrorCount": 0,
        }
    finally:
        app.dependency_overrides.clear()


def test_log_routes_return_business_envelope_for_invalid_input() -> None:
    """缺失日志字段和非法查询值不能泄露 FastAPI 默认 422 格式。"""
    app = build_test_app()
    app.dependency_overrides[get_log_service] = lambda: LogService(repository=InMemoryLogRepository())
    client = TestClient(app)
    try:
        missing_module = client.post("/api/logs/events", json={"action": "missing-module"})
        assert missing_module.status_code == 200
        assert missing_module.json() == {"code": 0, "msg": "模块不能为空", "data": None}

        malformed_body = client.post("/api/logs/events", json=[])
        assert malformed_body.status_code == 200
        assert malformed_body.json() == {"code": 0, "msg": "请求参数不合法", "data": None}

        invalid_limit = client.get("/api/logs/events/recent?limit=not-a-number")
        assert invalid_limit.status_code == 200
        assert invalid_limit.json() == {"code": 0, "msg": "日志条数参数不合法", "data": None}
    finally:
        app.dependency_overrides.clear()
