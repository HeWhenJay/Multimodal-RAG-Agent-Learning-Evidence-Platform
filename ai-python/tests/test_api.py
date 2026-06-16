import os

from fastapi.testclient import TestClient

os.environ["RAG_STORE_BACKEND"] = "memory"

from app.main import app


def test_index_and_query_api():
    client = TestClient(app)
    index_response = client.post(
        "/internal/rag/documents/index-text",
        json={
            "documentId": "doc-api",
            "title": "RAG 项目笔记",
            "documentType": "markdown",
            "source": "api-test",
            "content": "## 混合检索\nBM25 适合关键词，向量检索适合语义，RRF 用于融合排序。",
        },
    )

    assert index_response.status_code == 200
    assert index_response.json()["chunkCount"] >= 1

    query_response = client.post(
        "/internal/rag/query",
        json={"question": "BM25 和向量检索怎么融合？", "topK": 3},
    )

    assert query_response.status_code == 200
    data = query_response.json()
    assert data["evidences"]
    assert "RAG 项目笔记" in data["evidences"][0]["title"]
