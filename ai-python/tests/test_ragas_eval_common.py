from pathlib import Path

from evaluation.ragas_eval_common import (
    build_ragas_input_row,
    calculate_document_hit,
    ensure_ragas_eval_config,
    evaluate_boundary_case,
    has_valid_evidence_reference,
    load_json,
    load_jsonl,
    normalize_path,
    snake_case_query_to_project_request,
)


def test_load_eval_cases_and_documents():
    """确认评估用例和文档清单可以按 UTF-8 正确读取。"""
    cases = load_jsonl(Path("docs/testing/ragas-small-eval-cases.jsonl"))
    documents = load_json(Path("docs/testing/ragas-small-eval-documents.json"))

    assert len(cases) == 12
    assert len(documents) == 10
    assert cases[0]["expected_document_ids"] == ["ragas-d01"]
    assert documents[0]["documentId"] == "ragas-d01"


def test_snake_case_query_to_project_request():
    """确认样本字段会转换为项目接口需要的 camelCase。"""
    case = {
        "question": "RAG-Fusion 如何排序？",
        "top_k": 5,
        "metadata_filter": {"userId": "ragas-small-eval", "visibilityScope": "private"},
    }

    payload = snake_case_query_to_project_request(case)

    assert payload == {
        "question": "RAG-Fusion 如何排序？",
        "topK": 5,
        "metadataFilter": {"userId": "ragas-small-eval", "visibilityScope": "private"},
    }


def test_normalize_path_uses_windows_style_comparison():
    """确认路径比较不会受斜杠方向和大小写影响。"""
    assert normalize_path("C:/Users/WhenJayHe/notes/study/demo.md") == "c:\\users\\whenjayhe\\notes\\study\\demo.md"


def test_calculate_document_hit_top1_and_top3():
    """确认文档级 top1/top3 命中计算符合人工核验口径。"""
    assert calculate_document_hit(["ragas-d02"], ["ragas-d02", "ragas-d01"])["top1_hit"]
    top3 = calculate_document_hit(["ragas-d03"], ["ragas-d01", "ragas-d02", "ragas-d03"])
    assert not top3["top1_hit"]
    assert top3["top3_hit"]
    assert top3["hit_rank"] == 3
    assert not calculate_document_hit(["ragas-d04"], ["ragas-d01", "ragas-d02", "ragas-d03"])["top3_hit"]


def test_has_valid_evidence_reference_requires_project_reference_fields():
    """确认引用结构必须能追踪到 evidenceId 和来源字段。"""
    evidence = {
        "evidenceId": "chunk-1",
        "title": "RAG 笔记",
        "sectionName": "检索评估",
        "source": "ragas-small-eval",
        "score": 0.9,
    }

    assert has_valid_evidence_reference("答案引用 [chunk-1]\n\n证据引用：...", [evidence])
    assert not has_valid_evidence_reference("没有引用。", [evidence])


def test_boundary_case_blocks_metadata_leakage():
    """确认不存在 documentType 下的边界样本不能返回其它资料。"""
    case = {"case_id": "B02", "expected_document_ids": []}
    response = {"answer": "当前知识库没有检索到足够相关的证据。", "evidences": []}

    result = evaluate_boundary_case(case, response)

    assert result["passed"]
    assert result["evidence_count"] == 0


def test_boundary_case_treats_low_score_as_insufficient_evidence():
    """确认低相关 evidence 不会被误判为世界杯问题的有效依据。"""
    case = {"case_id": "B01", "expected_document_ids": []}
    response = {
        "answer": "根据知识库，没有检索到足够相关的证据。",
        "evidences": [{"documentId": "ragas-d01", "score": 0.01}],
    }

    result = evaluate_boundary_case(case, response, boundary_score_threshold=0.18)

    assert result["passed"]
    assert result["max_score"] == 0.01


def test_build_ragas_input_row_keeps_project_auxiliary_fields():
    """确认 Ragas 输入只使用标准字段，同时保留项目辅助核验字段。"""
    case = {
        "case_id": "R01",
        "question": "为什么要评估？",
        "reference": "需要评估风险。",
        "expected_document_ids": ["ragas-d01"],
    }
    response = {
        "answer": "因为存在不确定性。[chunk-1]",
        "evidences": [
            {
                "evidenceId": "chunk-1",
                "documentId": "ragas-d01",
                "snippet": "RAG 应用上线前需要评估。",
            }
        ],
    }

    row = build_ragas_input_row(case, response)

    assert row["user_input"] == "为什么要评估？"
    assert row["response"].startswith("因为存在")
    assert row["retrieved_contexts"] == ["RAG 应用上线前需要评估。"]
    assert row["reference"] == "需要评估风险。"
    assert row["retrieved_context_ids"] == ["chunk-1"]
    assert row["retrieved_document_ids"] == ["ragas-d01"]
    assert row["expected_document_ids"] == ["ragas-d01"]


def test_ragas_mode_requires_separate_eval_key(monkeypatch):
    """确认真实 Ragas 模式不会静默复用项目 DASHSCOPE_API_KEY。"""
    monkeypatch.delenv("RAGAS_EVAL_API_KEY", raising=False)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "project-key")
    monkeypatch.setenv("RAGAS_EVAL_LLM_MODEL", "eval-llm")
    monkeypatch.setenv("RAGAS_EVAL_EMBEDDING_MODEL", "eval-embedding")

    try:
        ensure_ragas_eval_config()
    except RuntimeError as exc:
        assert "RAGAS_EVAL_API_KEY 未配置" in str(exc)
    else:
        raise AssertionError("缺少 RAGAS_EVAL_API_KEY 时必须报错")
