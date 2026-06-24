import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

AI_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(AI_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(AI_PYTHON_DIR))

os.environ["RAG_STORE_BACKEND"] = "memory"

from agent.memory_service import IN_MEMORY_INDEX
from app.main import app


def test_memory_api_requires_internal_token(monkeypatch):
    """内部记忆接口必须校验 Java 内部令牌。"""
    use_memory_backend(monkeypatch)
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)

    response = client.post(
        "/internal/agent/memory/query",
        json={"taskId": "task-1", "userId": "7", "query": "Redis", "topK": 3},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "AGENT_INTERNAL_TOKEN_INVALID"


def test_active_memory_can_be_indexed_and_retrieved(monkeypatch):
    """ACTIVE 记忆写入索引后可以按 Java 授权范围检索。"""
    use_memory_backend(monkeypatch)
    IN_MEMORY_INDEX.clear()
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    headers = {"X-Agent-Internal-Token": "agent-secret"}

    upsert = client.post("/internal/agent/memory/index/upsert", headers=headers, json=memory_payload())
    query = client.post(
        "/internal/agent/memory/query",
        headers=headers,
        json={
            "taskId": "task-1",
            "userId": "7",
            "query": "Redis 缓存偏好",
            "topK": 3,
            "allowedScopes": [{"scopeType": "USER", "scopeId": None}],
        },
    )

    assert upsert.status_code == 200
    assert upsert.json()["indexed"] is True
    assert query.status_code == 200
    assert [item["memoryId"] for item in query.json()["memories"]] == ["agent-memory-1"]


def test_memory_query_is_user_isolated(monkeypatch):
    """检索只能返回当前 Java 授权 userId 的记忆。"""
    use_memory_backend(monkeypatch)
    IN_MEMORY_INDEX.clear()
    IN_MEMORY_INDEX["agent-memory-1"] = indexed_memory_row(user_id="7")
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)

    response = client.post(
        "/internal/agent/memory/query",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "task-2",
            "userId": "8",
            "query": "Redis",
            "topK": 3,
            "allowedScopes": [{"scopeType": "USER", "scopeId": None}],
        },
    )

    assert response.status_code == 200
    assert response.json()["memories"] == []


def test_pending_review_memory_is_not_returned(monkeypatch):
    """PENDING_REVIEW 记忆不能进入默认 memoryContext。"""
    use_memory_backend(monkeypatch)
    IN_MEMORY_INDEX.clear()
    row = indexed_memory_row(user_id="7")
    row["status"] = "PENDING_REVIEW"
    IN_MEMORY_INDEX["agent-memory-1"] = row
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)

    response = client.post(
        "/internal/agent/memory/query",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "task-1",
            "userId": "7",
            "query": "Redis",
            "topK": 3,
            "allowedScopes": [{"scopeType": "USER", "scopeId": None}],
        },
    )

    assert response.status_code == 200
    assert response.json()["memories"] == []


def test_deleted_memory_index_is_not_returned(monkeypatch):
    """删除索引后同一记忆不再被召回。"""
    use_memory_backend(monkeypatch)
    IN_MEMORY_INDEX.clear()
    IN_MEMORY_INDEX["agent-memory-1"] = indexed_memory_row(user_id="7")
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    headers = {"X-Agent-Internal-Token": "agent-secret"}

    delete = client.post(
        "/internal/agent/memory/index/delete",
        headers=headers,
        json={"memoryId": "agent-memory-1", "userId": "7"},
    )
    query = client.post(
        "/internal/agent/memory/query",
        headers=headers,
        json={
            "taskId": "task-1",
            "userId": "7",
            "query": "Redis",
            "topK": 3,
            "allowedScopes": [{"scopeType": "USER", "scopeId": None}],
        },
    )

    assert delete.status_code == 200
    assert delete.json()["deleted"] is True
    assert query.json()["memories"] == []


def memory_payload() -> dict:
    """构造 Java 已授权的索引写入请求。"""
    return {
        "memoryId": "agent-memory-1",
        "userId": "7",
        "memoryType": "PREFERENCE",
        "namespace": "user_preference",
        "scopeType": "USER",
        "scopeId": None,
        "subjectKey": "redis_answer_style",
        "content": "用户希望 Redis 问答先说明缓存策略，再补充持久化证据。",
        "summary": "Redis 问答先讲缓存策略。",
        "retrievalText": "Redis 缓存策略 持久化 用户偏好",
        "status": "ACTIVE",
        "confidence": 0.8,
        "importance": 0.7,
        "sensitivityLevel": "LOW",
    }


def use_memory_backend(monkeypatch) -> None:
    """测试中强制使用内存索引，避免依赖本机 PostgreSQL 状态。"""
    monkeypatch.delenv("RAG_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)


def indexed_memory_row(user_id: str) -> dict:
    """构造内存检索索引行。"""
    row = memory_payload()
    row["userId"] = user_id
    row["retrievalText"] = "Redis 缓存策略 持久化 用户偏好"
    row["termCounts"] = {"Redis": 1, "缓存": 1, "策略": 1}
    row["embedding"] = None
    row["deletedAt"] = None
    row["updatedAt"] = None
    return row
