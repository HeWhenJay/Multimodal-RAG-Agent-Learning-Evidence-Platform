import os
import time

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
            "userId": "api-user",
            "content": "## 混合检索\nBM25 适合关键词，向量检索适合语义，RRF 用于融合排序。",
        },
    )

    assert index_response.status_code == 200
    assert index_response.json()["chunkCount"] >= 1

    query_response = client.post(
        "/internal/rag/query",
        json={"question": "BM25 和向量检索怎么融合？", "topK": 3, "metadataFilter": {"userId": "api-user"}},
    )

    assert query_response.status_code == 200
    data = query_response.json()
    assert data["answerStatus"] == "ANSWERED"
    assert data["supportingEvidenceIds"]
    assert data["evidences"]
    assert "RAG 项目笔记" in data["evidences"][0]["title"]


def test_jd_analysis_api_uses_rag_evidence():
    client = TestClient(app)
    client.post(
        "/internal/rag/documents/index-text",
        json={
            "documentId": "doc-jd-api",
            "title": "RAG-Fusion 项目复盘",
            "documentType": "markdown",
            "source": "api-test",
            "userId": "user-jd",
            "content": "## RAG-Fusion\n项目使用 Multi-Query、BM25 和向量检索做 RRF 融合排序。",
        },
    )

    response = client.post(
        "/internal/rag/jd-analysis",
        json={
            "userId": "user-jd",
            "jobDescription": "需要熟悉 RAG-Fusion、Multi-Query 和 BM25 的 AI 应用开发实习生。",
            "resumeText": "做过 RAG-Fusion 检索增强项目。",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["skills"]
    assert data["learningPlan"]
    assert data["resumeAlignments"]
    assert data["matchScore"] >= 0


def test_subtitle_index_and_query_returns_video_time_range():
    client = TestClient(app)
    response = client.post(
        "/internal/rag/documents/index-file",
        data={
            "document_id": "doc-video-api",
            "title": "某课程视频",
            "document_type": "srt",
            "source": "upload",
            "user_id": "video-user",
            "visibility_scope": "private",
        },
        files={
            "file": (
                "course.srt",
                "1\n01:23:10,000 --> 01:25:42,000\n这里讲到了 RAG-Fusion 和 RRF 融合排序。\n",
                "text/plain",
            )
        },
    )
    assert response.status_code == 200

    query_response = client.post(
        "/internal/rag/query",
        json={
            "question": "我在哪个视频里学过 RAG-Fusion？",
            "topK": 3,
            "metadataFilter": {"userId": "video-user"},
        },
    )

    assert query_response.status_code == 200
    query_data = query_response.json()
    assert query_data["answerStatus"] == "ANSWERED"
    assert "某课程视频 01:23:10-01:25:42" in query_data["answer"]
    assert "从这里播放" in query_data["answer"]
    evidence = query_data["evidences"][0]
    assert evidence["title"] == "某课程视频"
    assert evidence["startTime"] == "01:23:10"
    assert evidence["endTime"] == "01:25:42"
    assert evidence["playbackUrl"].startswith("/videos?")
    assert "documentId=doc-video-api" in evidence["playbackUrl"]
    assert "startTime=01%3A23%3A10" in evidence["playbackUrl"]


def test_query_task_refuses_without_calling_llm(monkeypatch):
    def forbid_generate(*args, **kwargs):
        raise AssertionError("拒答任务不应调用 LLM 回答生成")

    monkeypatch.setattr("rag.retrievers.retrieval.generate_grounded_answer", forbid_generate)
    client = TestClient(app)
    start_response = client.post(
        "/internal/rag/query/tasks",
        json={
            "question": "完全没有资料的用户问 RAG guard 应该如何拒答？",
            "topK": 3,
            "metadataFilter": {"userId": "task-refused-user"},
        },
    )
    assert start_response.status_code == 200
    task_id = start_response.json()["taskId"]

    task_data = start_response.json()
    for _ in range(30):
        if task_data["status"] != "RUNNING":
            break
        time.sleep(0.05)
        task_data = client.get(f"/internal/rag/query/tasks/{task_id}").json()

    assert task_data["status"] == "COMPLETED"
    assert task_data["result"]["answerStatus"] == "REFUSED"
    assert task_data["result"]["refusalReason"] == "NO_EVIDENCE"
    assert task_data["result"]["evidences"] == []
    assert task_data["result"]["supportingEvidenceIds"] == []
    stages = [event["stageCode"] for event in task_data["result"]["progressEvents"]]
    assert "query.guard" in stages
    assert stages[-1] == "query.answer"
