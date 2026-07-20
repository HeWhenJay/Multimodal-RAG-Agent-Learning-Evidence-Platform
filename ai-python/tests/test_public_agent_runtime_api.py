"""纯 Python 公开 Agent、记忆和 durable worker 契约测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.agent_runtime.repository import InMemoryAgentRepository
from app.agent_runtime.service import AgentRuntimeService
from app.api.agent import current_stream_user_id, get_agent_runtime_service
from app.core.current_user import get_current_user
from app.main import app
from app.schemas.auth import AuthUserResponse
from app.workers.agent_task_worker import AgentTaskWorker, agent_worker_enabled, worker_poll_seconds


def test_public_task_is_owned_by_session_and_worker_completes_without_external_http(monkeypatch) -> None:
    """浏览器提交后只写 Python 事实表，worker 通过本地 gateway 完成任务。"""
    service = AgentRuntimeService(InMemoryAgentRepository())
    client = configured_client(service)
    monkeypatch.setenv("AGENT_LLM_ENABLED", "false")
    try:
        created = client.post(
            "/api/agent/tasks",
            json={
                "taskType": "pure_read_query",
                "title": "Redis 证据",
                "userId": "9999",
                "input": {"goal": "说明 Redis 缓存策略并引用资料证据", "topK": 3},
            },
        )
        assert created.status_code == 200
        body = created.json()
        assert body["code"] == 1
        assert body["data"]["status"] == "CREATED"
        task_id = body["data"]["id"]

        assert AgentTaskWorker(service).process_available_tasks() == 1
        completed = client.get(f"/api/agent/tasks/{task_id}").json()
        assert completed["code"] == 1
        assert completed["data"]["status"] == "COMPLETED", completed["data"]
        assert completed["data"]["final"]["evidenceIds"] == []
        assert any(item["toolName"] == "rag_query_probe_non_persistent" for item in completed["data"]["toolCalls"])
        assert all(item["taskId"] == task_id for item in completed["data"]["messages"])

        with client.stream("GET", f"/api/agent/tasks/{task_id}/stream?token=test-token") as stream:
            payload = "".join(stream.iter_text())
        assert "event: task" in payload
        assert "event: done" in payload
    finally:
        app.dependency_overrides.clear()


def test_planning_review_is_persisted_then_worker_resumes_from_python_records(monkeypatch) -> None:
    """审批接口仅落库，下一轮 worker 才按同一任务恢复统一图。"""
    service = AgentRuntimeService(InMemoryAgentRepository())
    client = configured_client(service)
    monkeypatch.setenv("AGENT_LLM_ENABLED", "false")
    try:
        created = client.post(
            "/api/agent/tasks",
            json={
                "taskType": "planning_task",
                "input": {
                    "goal": "分析后端实习 JD 适配度",
                    "jobDescription": "要求 Python、PostgreSQL、RAG 项目经验",
                    "resumeText": "完成过学习证据 RAG 项目",
                },
            },
        ).json()["data"]
        task_id = created["id"]
        worker = AgentTaskWorker(service)
        assert worker.process_available_tasks() == 1
        waiting = client.get(f"/api/agent/tasks/{task_id}").json()["data"]
        assert waiting["status"] == "WAITING_PLAN_REVIEW", waiting
        review_id = waiting["reviews"][0]["id"]

        decision = client.post(
            f"/api/agent/tasks/{task_id}/reviews/{review_id}/decide",
            json={"decision": "APPROVED", "comment": "可以继续", "changes": {}, "userId": "9999"},
        ).json()
        assert decision["code"] == 1
        assert decision["data"]["status"] == "RUNNING"
        assert worker.process_available_tasks() == 1
        resumed = client.get(f"/api/agent/tasks/{task_id}").json()["data"]
        assert resumed["status"] == "WAITING_OUTPUT_REVIEW"
        assert resumed["reviews"][-1]["reviewType"] == "OUTPUT"
        assert resumed["plan"]["requiresPlanReview"] is True
    finally:
        app.dependency_overrides.clear()


def test_memory_crud_uses_current_user_and_rejects_scope_expansion() -> None:
    """记忆 API 不能信任请求 userId，PATCH 不能把 TASK 作用域扩大为 USER。"""
    service = AgentRuntimeService(InMemoryAgentRepository())
    client = configured_client(service)
    try:
        created = client.post(
            "/api/agent/memories",
            json={
                "userId": "other-user",
                "memoryType": "PREFERENCE",
                "namespace": "resume_style",
                "scopeType": "TASK",
                "scopeId": "task-1",
                "subjectKey": "writing_tone",
                "content": "简历项目描述优先强调可追溯 evidence。",
                "importance": 0.82,
            },
        ).json()
        assert created["code"] == 1
        memory = created["data"]
        assert memory["userId"] == "7"
        assert memory["status"] == "ACTIVE"

        listed = client.get("/api/agent/memories?namespace=resume_style").json()
        assert listed["code"] == 1
        assert [item["id"] for item in listed["data"]] == [memory["id"]]

        detail = client.get(f"/api/agent/memories/{memory['id']}").json()
        assert detail["code"] == 1
        assert detail["data"]["subjectKey"] == "writing_tone"

        rejected = client.patch(
            f"/api/agent/memories/{memory['id']}",
            json={"scopeType": "USER", "content": "尝试扩大作用域"},
        ).json()
        assert rejected == {
            "code": 0,
            "msg": "AGENT_MEMORY_SCOPE_ESCALATION: PATCH 不能扩大记忆作用域",
            "data": None,
        }

        archived = client.post(f"/api/agent/memories/{memory['id']}/archive").json()
        assert archived["code"] == 1
        assert archived["data"]["status"] == "ARCHIVED"
    finally:
        app.dependency_overrides.clear()


def test_public_agent_validation_and_cross_user_access_keep_result_envelope() -> None:
    """业务验证与越权查询均使用 `{code,msg,data}`，不泄露资源归属。"""
    service = AgentRuntimeService(InMemoryAgentRepository())
    current_user = {"id": 7}
    client = configured_client(service, current_user)
    try:
        invalid = client.post("/api/agent/tasks", json={"taskType": "pure_read_query", "input": {}}).json()
        assert invalid == {"code": 0, "msg": "AGENT_VALIDATION_FAILED: 任务目标不能为空", "data": None}

        task_id = client.post(
            "/api/agent/tasks",
            json={"taskType": "pure_read_query", "input": {"goal": "仅限当前用户"}},
        ).json()["data"]["id"]
        current_user["id"] = 8
        forbidden = client.get(f"/api/agent/tasks/{task_id}").json()
        assert forbidden == {"code": 0, "msg": "AGENT_TASK_NOT_FOUND: Agent 任务不存在", "data": None}
    finally:
        app.dependency_overrides.clear()


def test_agent_worker_uses_runtime_config_environment_names(monkeypatch) -> None:
    """`run.py` 传给 worker 的 AI_AGENT 配置必须控制实际子进程。"""
    monkeypatch.setenv("AI_AGENT_WORKER_ENABLED", "true")
    monkeypatch.setenv("AI_AGENT_WORKER_POLL_INTERVAL_SECONDS", "0.25")

    assert agent_worker_enabled() is True
    assert worker_poll_seconds() == 0.25


def configured_client(service: AgentRuntimeService, current_user: dict[str, int] | None = None) -> TestClient:
    """用内存仓储和可切换当前用户构造 FastAPI 客户端。"""
    subject = current_user if current_user is not None else {"id": 7}

    def user() -> AuthUserResponse:
        return AuthUserResponse(id=subject["id"], account=f"user-{subject['id']}", displayName="测试用户", role="USER")

    app.dependency_overrides[get_agent_runtime_service] = lambda: service
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[current_stream_user_id] = lambda: str(subject["id"])
    return TestClient(app)
