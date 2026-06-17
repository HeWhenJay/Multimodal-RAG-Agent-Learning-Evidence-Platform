from rag.retrieval import cached_embedding, embed_text, embedding_provider_name
from rag.retrieval import InMemoryRagStore
from rag.pgvector_store import build_filter_clause, vector_literal
from schemas.rag import IndexTextRequest, QueryRequest


def test_rag_store_indexes_and_queries_with_evidence():
    store = InMemoryRagStore()
    store.index_text(
        IndexTextRequest(
            documentId="doc-spring",
            title="Spring Boot 学习笔记",
            documentType="markdown",
            source="unit-test",
            userId="unit-user",
            content="## 自动配置\nSpring Boot 自动配置通过条件注解和 starter 降低配置成本。\n## 事务\n事务需要关注传播行为。",
        )
    )

    response = store.query(QueryRequest(question="Spring Boot 自动配置如何工作？", topK=3))

    assert response.evidences
    assert response.evidences[0].documentId == "doc-spring"
    assert "自动配置" in response.answer
    assert len(response.expandedQueries) >= 3


def test_pgvector_filter_clause_supports_columns_and_metadata():
    where_sql, params = build_filter_clause(
        {
            "documentType": "markdown",
            "sectionName": ["自动配置", "事务"],
            "customTag": "spring",
        }
    )

    assert "d.document_type = %s" in where_sql
    assert "c.section_name IN (%s, %s)" in where_sql
    assert "c.metadata ->> %s = %s" in where_sql
    assert params == ["markdown", "自动配置", "事务", "customTag", "spring"]


def test_vector_literal_matches_pgvector_input_format():
    assert vector_literal([0.1, -0.25, 1.0]) == "[0.10000000,-0.25000000,1.00000000]"


def test_embedding_defaults_to_1024_dimensions(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "hash")
    monkeypatch.setenv("RAG_VECTOR_DIMENSIONS", "1024")
    cached_embedding.cache_clear()

    embedding = embed_text("RAG-Fusion 混合检索")

    assert len(embedding) == 1024


def test_embedding_provider_defaults_to_dashscope(monkeypatch):
    monkeypatch.delenv("RAG_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    assert embedding_provider_name() == "dashscope"


def test_dashscope_embedding_request_uses_1024_dimensions(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"embedding": [0.001] * 1024}]}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("RAG_VECTOR_DIMENSIONS", "1024")
    cached_embedding.cache_clear()

    embedding = embed_text("RAG-Fusion 混合检索")

    assert len(embedding) == 1024
    assert captured["url"].endswith("/embeddings")
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["model"] == "text-embedding-v4"
    assert captured["json"]["dimensions"] == 1024
