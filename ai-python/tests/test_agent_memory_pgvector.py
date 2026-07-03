import sys
from pathlib import Path

AI_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(AI_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(AI_PYTHON_DIR))

from agents.memory.memory_service import AgentMemoryService
from app.schemas.agent_memory import MemoryIndexUpsertRequest, MemoryQueryRequest


class FakeContext:
    """提供 connection/cursor 的上下文管理协议。"""

    def __init__(self, value, on_enter=None):
        self.value = value
        self.on_enter = on_enter

    def __enter__(self):
        if self.on_enter:
            self.on_enter()
        return self.value

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeCursor:
    """记录 SQL 与参数，按测试需要返回预设行。"""

    def __init__(self, rows=None):
        self.executed = []
        self.rows = rows or []
        self.rowcount = 0

    def execute(self, sql, params=None):
        self.executed.append((str(sql), params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    """模拟 psycopg connection 的事务和 cursor 行为。"""

    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.transaction_opened = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def transaction(self):
        return FakeContext(self, lambda: setattr(self, "transaction_opened", True))

    def cursor(self):
        return FakeContext(self.cursor_obj)


def test_pgvector_upsert_uses_real_embedding_and_vector_literal(monkeypatch):
    """PostgreSQL 写入侧必须调用 RAG embed_text 并写入 pgvector literal。"""
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql://unused")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    service = AgentMemoryService()
    cursor = FakeCursor()
    connection = FakeConnection(cursor)
    embed_calls = []

    def fake_embed_text(text, dimensions=None):
        embed_calls.append((text, dimensions))
        return [0.1] * 1024

    monkeypatch.setattr(service, "_connect", lambda: connection)
    monkeypatch.setattr(service, "_json_adapter", lambda: (lambda value: value))
    monkeypatch.setattr("agents.memory.memory_service.embed_text", fake_embed_text)

    result = service.upsert_index(MemoryIndexUpsertRequest(**memory_payload()))

    sql = "\n".join(item[0] for item in cursor.executed)
    insert_params = cursor.executed[-1][1]
    assert result.indexed is True
    assert result.diagnostics == {"backend": "pgvector", "embeddingProvider": "dashscope", "vectorDimensions": 1024}
    assert embed_calls == [("Redis 缓存策略 持久化 用户偏好", 1024)]
    assert connection.transaction_opened is True
    assert "%s::vector" in sql
    assert isinstance(insert_params[6], str)
    assert insert_params[6].startswith("[0.1")


def test_pgvector_search_uses_vector_distance_sql_and_authorized_filters(monkeypatch):
    """PostgreSQL 查询侧必须通过 pgvector 距离算子和统一授权过滤召回。"""
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql://unused")
    service = AgentMemoryService()
    cursor = FakeCursor(rows=[{"memory_id": "agent-memory-1", "score": 0.88}])
    embed_calls = []

    def fake_embed_text(text, dimensions=None):
        embed_calls.append((text, dimensions))
        return [0.2] * 1024

    monkeypatch.setattr(service, "_connect", lambda: FakeConnection(cursor))
    monkeypatch.setattr("agents.memory.memory_service.embed_text", fake_embed_text)

    request = MemoryQueryRequest(
        taskId="task-1",
        userId="7",
        query="Redis",
        topK=3,
        namespaces=["user_preference"],
        memoryTypes=["PREFERENCE"],
        allowedScopes=[{"scopeType": "TASK", "scopeId": "task-1"}],
    )

    hits = service._pgvector_search("Redis 缓存", request, limit=5)

    sql, params = cursor.executed[-1]
    assert hits == [("agent-memory-1", 0.88)]
    assert "1 - (embedding.embedding <=> %s::vector) AS score" in sql
    assert "ORDER BY embedding.embedding <=> %s::vector" in sql
    assert "item.user_id = %s" in sql
    assert "item.status = 'ACTIVE'" in sql
    assert "item.deleted_at IS NULL" in sql
    assert "COALESCE(item.sensitivity_level, 'LOW') != 'HIGH'" in sql
    assert "embedding.status = 'ACTIVE'" in sql
    assert "embedding.deleted_at IS NULL" in sql
    assert "item.namespace = ANY(%s)" in sql
    assert "item.memory_type = ANY(%s)" in sql
    assert "item.scope_type = %s AND item.scope_id = %s" in sql
    assert embed_calls == [("Redis 缓存", 1024)]
    assert params[1:] == ["7", ["user_preference"], ["PREFERENCE"], "TASK", "task-1", params[0], 5]


def test_pgvector_query_keeps_vector_only_hits(monkeypatch):
    """RRF 融合后必须回填只由 pgvector 命中的记忆元数据。"""
    monkeypatch.setenv("RAG_DATABASE_URL", "postgresql://unused")
    service = AgentMemoryService()

    bm25_rows = [
        query_row(
            memory_id="agent-memory-bm25-only",
            retrieval_text="Spring 事务",
            term_counts={"spring": 1, "事务": 1},
        )
    ]
    vector_only_row = query_row(
        memory_id="agent-memory-vector-only",
        retrieval_text="Redis 缓存策略",
        term_counts={"redis": 1, "缓存": 1},
    )
    loaded_ids = []

    def fake_load_by_ids(request, memory_ids):
        loaded_ids.extend(memory_ids)
        return [vector_only_row] if "agent-memory-vector-only" in memory_ids else []

    monkeypatch.setattr("agents.memory.memory_service.local_expand_queries", lambda query, count=5: [query])
    monkeypatch.setattr(service, "_load_memory_rows", lambda request, limit=200: bm25_rows)
    monkeypatch.setattr(service, "_pgvector_search", lambda query, request, limit: [("agent-memory-vector-only", 0.93)])
    monkeypatch.setattr(service, "_load_memory_rows_by_ids", fake_load_by_ids)

    response = service.query(
        MemoryQueryRequest(
            taskId="task-1",
            userId="7",
            query="Redis 缓存偏好",
            topK=3,
            allowedScopes=[{"scopeType": "USER", "scopeId": None}],
        )
    )

    assert [item.memoryId for item in response.memories] == ["agent-memory-vector-only"]
    assert "agent-memory-vector-only" in loaded_ids
    assert response.diagnostics["pgvectorUsed"] is True
    assert response.diagnostics["bm25CandidateCount"] == 1
    assert response.diagnostics["vectorHitCount"] == 1
    assert response.diagnostics["finalCandidateCount"] == 1


def memory_payload() -> dict:
    """构造 PostgreSQL 写入测试使用的记忆请求。"""
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


def query_row(memory_id: str, retrieval_text: str, term_counts: dict[str, int]) -> dict:
    """构造 Memory Service 查询组装所需的标准行。"""
    return {
        "memoryId": memory_id,
        "userId": "7",
        "memoryType": "PREFERENCE",
        "namespace": "user_preference",
        "scopeType": "USER",
        "scopeId": None,
        "subjectKey": "redis_answer_style",
        "summary": "Redis 问答先讲缓存策略。",
        "content": "用户希望 Redis 问答先说明缓存策略。",
        "retrievalText": retrieval_text,
        "termCounts": term_counts,
        "embedding": None,
        "status": "ACTIVE",
        "confidence": 0.8,
        "importance": 0.7,
        "deletedAt": None,
        "updatedAt": None,
    }
