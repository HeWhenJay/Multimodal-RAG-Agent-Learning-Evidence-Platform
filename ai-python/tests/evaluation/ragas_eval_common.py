from __future__ import annotations

import csv
import asyncio
import importlib
import inspect
import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[3]
AI_PYTHON_ROOT = PROJECT_ROOT / "ai-python"
DEFAULT_CASES_PATH = PROJECT_ROOT / "docs" / "testing" / "ragas-small-eval-cases.jsonl"
DEFAULT_DOCUMENTS_PATH = PROJECT_ROOT / "docs" / "testing" / "ragas-small-eval-documents.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "tmp" / "ragas-small-eval"
DEFAULT_BOUNDARY_SCORE_THRESHOLD = 0.18
DEFAULT_RAGAS_COMPATIBLE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_RAGAS_LLM_MODEL = "qwen-plus"
DEFAULT_RAGAS_EMBEDDING_MODEL = "text-embedding-v4"
DEFAULT_RAGAS_TEST_TABLE_PREFIX = "Ragas_Test_"
RAG_PROFILE_ENV = "RAGAS_EVAL_RAG_PROFILE"
RAGAS_METRICS_ENV = "RAGAS_EVAL_METRICS"
RAGAS_TEST_PREFIX_ENV = "RAGAS_TEST_TABLE_PREFIX"
DEFAULT_RAGAS_METRIC_KEYS = ("context_precision", "context_recall", "faithfulness", "answer_relevancy")
RAGAS_METRIC_ALIASES = {
    "context_precision": "context_precision",
    "precision": "context_precision",
    "contextprecision": "context_precision",
    "context_precision_with_reference": "context_precision",
    "llm_context_precision_with_reference": "context_precision",
    "context_recall": "context_recall",
    "recall": "context_recall",
    "contextrecall": "context_recall",
    "llm_context_recall": "context_recall",
    "faithfulness": "faithfulness",
    "answer_relevancy": "answer_relevancy",
    "answer_relevance": "answer_relevancy",
    "response_relevancy": "answer_relevancy",
    "response_relevance": "answer_relevancy",
    "relevancy": "answer_relevancy",
}


@dataclass(frozen=True)
class EvaluationRunResult:
    """保存一次离线评估的结构化结果。"""

    rows: list[dict[str, Any]]
    ragas_rows: list[dict[str, Any]]
    summary: dict[str, Any]


@dataclass(frozen=True)
class RagasEvalSettings:
    """保存真实 Ragas 评分所需的环境配置，不包含明文 Key 输出。"""

    provider: str
    ragas_provider: str
    base_url: str | None
    api_key: str
    llm_model: str
    embedding_model: str
    timeout_seconds: float
    max_retries: int
    max_wait_seconds: float
    max_workers: int
    batch_size: int | None
    temperature: float
    max_tokens: int | None
    metric_keys: tuple[str, ...]


@dataclass(frozen=True)
class RagasModelAdapter:
    """保存 Ragas 评估模型、embedding 与指标适配器。"""

    adapter_name: str
    llm: Any
    embeddings: Any
    metrics: list[Any]
    metric_names: list[str]
    construction_errors: list[str]


@dataclass(frozen=True)
class RagasMetricsRunResult:
    """保存一次真实 Ragas 评分运行结果。"""

    summary: dict[str, Any]
    ragas_version: str
    model_adapter: str
    metric_names: list[str]


def ensure_ai_python_path() -> None:
    """把 ai-python 加入 sys.path，保证脚本可从仓库根目录直接运行。"""
    ai_path = str(AI_PYTHON_ROOT)
    if ai_path not in sys.path:
        sys.path.insert(0, ai_path)


def load_current_project_rag_config() -> None:
    """加载当前项目 Python RAG 配置，供 current 档直接复用现有流程。"""
    ensure_ai_python_path()
    from run import load_runtime_config, parse_args

    load_runtime_config(parse_args([]))


def configure_current_rag_environment() -> None:
    """配置与生产一致的 RAG 组件，并在生产同库中用 Ragas_Test 前缀表隔离。"""
    load_current_project_rag_config()
    _require_first_env(("DASHSCOPE_API_KEY",), "百炼 API Key")
    _require_first_env(("RAG_DATABASE_URL", "DATABASE_URL"), "PostgreSQL/pgvector 数据库地址")
    if (os.getenv("RAG_STORE_BACKEND") or "").strip().lower() != "pgvector":
        raise RuntimeError("Ragas 评估必须复用生产 PostgreSQL/pgvector 配置，RAG_STORE_BACKEND 需要为 pgvector。")
    os.environ["RAG_TABLE_PREFIX"] = validate_ragas_test_table_prefix(
        os.getenv(RAGAS_TEST_PREFIX_ENV) or DEFAULT_RAGAS_TEST_TABLE_PREFIX
    )


def validate_ragas_test_table_prefix(value: str) -> str:
    """校验 Ragas 评估表前缀，必须显式带 Ragas_Test 并可作为 PostgreSQL 标识符片段。"""
    prefix = value.strip()
    if not prefix.startswith("Ragas_Test"):
        raise RuntimeError(f"{RAGAS_TEST_PREFIX_ENV} 必须以 Ragas_Test 开头，避免误写生产 RAG 表。")
    if not prefix.replace("_", "").isalnum() or not (prefix[0].isalpha() or prefix[0] == "_"):
        raise RuntimeError(f"{RAGAS_TEST_PREFIX_ENV} 只能包含字母、数字和下划线，并且必须以字母或下划线开头。")
    return prefix


def normalize_rag_profile(profile: str | None) -> str:
    """规范化评估调用项目 RAG 的运行档位。"""
    value = (profile or os.getenv(RAG_PROFILE_ENV) or "current").strip().lower()
    if value != "current":
        raise ValueError("RAG 运行档位只允许 current，Ragas 评估必须使用真实 PostgreSQL/pgvector。")
    return value


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """按 UTF-8 读取 JSONL 评估用例。"""
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as file:
        for line_no, line in enumerate(file, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                rows.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path} 第 {line_no} 行不是合法 JSON：{exc}") from exc
    return rows


def load_json(path: Path) -> list[dict[str, Any]]:
    """按 UTF-8 读取 JSON 文档清单。"""
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list):
        raise ValueError(f"{path} 顶层必须是数组")
    return data


def filter_eval_cases(
    cases: list[dict[str, Any]],
    *,
    case_id: str | None = None,
    case_index: int | None = None,
) -> list[dict[str, Any]]:
    """按 case_id 或 1 基序号筛选评估用例，便于逐条排查慢指标。"""
    if case_id and case_index is not None:
        raise ValueError("--case-id 和 --case-index 只能二选一。")
    if case_index is not None and case_index <= 0:
        raise ValueError("--case-index 必须是大于 0 的 1 基序号。")
    if case_id:
        selected = [case for case in cases if str(case.get("case_id") or "") == case_id]
        if not selected:
            raise ValueError(f"未找到 case_id={case_id} 的评估用例。")
        return selected
    if case_index is not None:
        if case_index > len(cases):
            raise ValueError(f"--case-index 超出范围：当前候选用例 {len(cases)} 条，收到 {case_index}。")
        return [cases[case_index - 1]]
    return cases


def normalize_path(value: str | None) -> str:
    """规范化路径字符串，用于跨 Windows 分隔符比较。"""
    if not value:
        return ""
    return str(value).replace("/", "\\").rstrip("\\").lower()


def snake_case_query_to_project_request(case: dict[str, Any]) -> dict[str, Any]:
    """把样本中的 snake_case 查询字段转换为项目接口 camelCase 字段。"""
    return {
        "question": case["question"],
        "topK": int(case.get("top_k") or case.get("topK") or 5),
        "metadataFilter": dict(case.get("metadata_filter") or case.get("metadataFilter") or {}),
    }


def build_index_request(document: dict[str, Any], content: str) -> dict[str, Any]:
    """构造 /internal/rag/documents/index-text 的请求体。"""
    return {
        "documentId": document["documentId"],
        "title": document["title"],
        "documentType": document.get("documentType", "markdown"),
        "source": document.get("source", "ragas-small-eval"),
        "userId": document.get("userId", "ragas-small-eval"),
        "visibilityScope": document.get("visibilityScope", "private"),
        "language": document.get("language", "zh-CN"),
        "parser": document.get("parser", "ragas-eval-markdown"),
        "sourcePath": document.get("sourcePath"),
        "content": content,
    }


def create_test_client(rag_profile: str = "current"):
    """创建 Python RAG 内部接口测试客户端，必须在环境变量设置后导入 app。"""
    ensure_ai_python_path()
    profile = normalize_rag_profile(rag_profile)
    os.environ[RAG_PROFILE_ENV] = profile
    configure_current_rag_environment()
    from fastapi.testclient import TestClient

    from app.main import app
    from app.api import rag as rag_api
    from rag.retrievers.retrieval import create_rag_store

    rag_api.store = create_rag_store()
    clear_in_memory_store(rag_api.store)
    return TestClient(app)


def clear_in_memory_store(store: Any) -> None:
    """清理内存 RAG 仓库，避免多次评估互相污染。"""
    for attr in ("documents", "chunks", "term_freqs", "doc_freq", "embeddings"):
        value = getattr(store, attr, None)
        if hasattr(value, "clear"):
            value.clear()


def index_eval_documents(client: Any, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把评估文档索引进项目 Python RAG 仓库。"""
    indexed: list[dict[str, Any]] = []
    for document in documents:
        notes_path = Path(document["notesPath"])
        if not notes_path.exists():
            raise FileNotFoundError(f"评估资料不存在：{notes_path}")
        content = notes_path.read_text(encoding="utf-8")
        payload = build_index_request(document, content)
        response = client.post("/internal/rag/documents/index-text", json=payload)
        if response.status_code != 200:
            raise RuntimeError(f"索引资料失败：{document['documentId']} HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        if data.get("status") not in {"READY", "PARTIAL"}:
            raise RuntimeError(f"索引资料状态异常：{document['documentId']} status={data.get('status')}")
        indexed.append(data)
    return indexed


def query_case(client: Any, case: dict[str, Any]) -> dict[str, Any]:
    """调用 /internal/rag/query 执行单条评估用例。"""
    payload = snake_case_query_to_project_request(case)
    response = client.post("/internal/rag/query", json=payload)
    if response.status_code != 200:
        raise RuntimeError(f"查询失败：{case['case_id']} HTTP {response.status_code} {response.text[:500]}")
    return response.json()


def evidence_document_ids(evidences: Iterable[dict[str, Any]]) -> list[str]:
    """提取 evidence 返回中的 documentId 列表，保持原排序并去重。"""
    result: list[str] = []
    for item in evidences:
        document_id = str(item.get("documentId") or "")
        if document_id and document_id not in result:
            result.append(document_id)
    return result


def evidence_source_paths(evidences: Iterable[dict[str, Any]]) -> list[str]:
    """提取 evidence 返回中的 sourcePath 列表，保持原排序并去重。"""
    result: list[str] = []
    for item in evidences:
        source_path = normalize_path(item.get("sourcePath"))
        if source_path and source_path not in result:
            result.append(source_path)
    return result


def calculate_document_hit(expected_ids: list[str], retrieved_ids: list[str]) -> dict[str, Any]:
    """计算文档级 top1/top3 命中结果。"""
    expected = [item for item in expected_ids if item]
    if not expected:
        return {"top1_hit": False, "top3_hit": False, "hit_rank": None}
    for index, document_id in enumerate(retrieved_ids, start=1):
        if document_id in expected:
            return {"top1_hit": index == 1, "top3_hit": index <= 3, "hit_rank": index}
    return {"top1_hit": False, "top3_hit": False, "hit_rank": None}


def calculate_answer_point_coverage(answer: str, points: list[str]) -> dict[str, Any]:
    """用关键点文本包含关系计算粗粒度回答覆盖率。"""
    if not points:
        return {"matched_points": [], "missing_points": [], "coverage": 1.0}
    normalized_answer = answer.lower()
    matched = [point for point in points if point.lower() in normalized_answer]
    missing = [point for point in points if point not in matched]
    return {
        "matched_points": matched,
        "missing_points": missing,
        "coverage": round(len(matched) / max(len(points), 1), 4),
    }


def has_valid_evidence_reference(answer: str, evidences: list[dict[str, Any]]) -> bool:
    """检查回答是否包含返回 evidence 的可追踪引用结构。"""
    if not evidences:
        return False
    evidence_ids = [str(item.get("evidenceId") or "") for item in evidences]
    has_answer_citation = any(evidence_id and f"[{evidence_id}]" in answer for evidence_id in evidence_ids)
    has_reference_section = "证据引用" in answer
    has_required_fields = all(
        item.get("title")
        and item.get("sectionName")
        and item.get("source") is not None
        and item.get("score") is not None
        for item in evidences
    )
    return bool((has_answer_citation or has_reference_section) and has_required_fields)


def evaluate_boundary_case(
    case: dict[str, Any],
    response: dict[str, Any],
    *,
    boundary_score_threshold: float = DEFAULT_BOUNDARY_SCORE_THRESHOLD,
) -> dict[str, Any]:
    """评估边界样本是否遵守无证据拒答和 metadataFilter 约束。"""
    evidences = list(response.get("evidences") or [])
    retrieved_ids = evidence_document_ids(evidences)
    max_score = max((float(item.get("score") or 0) for item in evidences), default=0.0)
    expected_ids = list(case.get("expected_document_ids") or [])
    answer = str(response.get("answer") or "")
    no_effective_evidence = not evidences or max_score < boundary_score_threshold
    no_unexpected_document = not expected_ids and not retrieved_ids if case["case_id"] == "B02" else True
    refusal_text = has_refusal_intent(answer)
    if case["case_id"] == "B02":
        passed = not evidences and no_unexpected_document
    else:
        passed = no_effective_evidence or refusal_text
    return {
        "case_id": case["case_id"],
        "passed": passed,
        "evidence_count": len(evidences),
        "max_score": round(max_score, 6),
        "retrieved_document_ids": retrieved_ids,
        "reason": "边界样本通过" if passed else "边界样本返回了有效证据或未拒答",
    }


def has_refusal_intent(answer: str) -> bool:
    """识别模型是否明确表达证据不足、无法回答或需要补充资料。"""
    text = " ".join(str(answer or "").split())
    if not text:
        return False
    refusal_fragments = (
        "没有检索到足够相关",
        "请先上传",
        "无可用证据",
        "无法回答",
        "无法确定",
        "无法提供",
        "不能确定",
        "不能回答",
        "证据不足",
        "资料不足",
        "信息不足",
        "缺少",
        "缺乏",
        "需要补充",
        "需要上传",
        "不涉及",
        "不包含",
        "没有提及",
        "未提及",
        "未提供",
        "关键信息缺口",
        "不能基于",
        "无法从现有",
    )
    return any(fragment in text for fragment in refusal_fragments)


def build_ragas_input_row(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """构造 Ragas 单轮样本输入，并保留项目辅助核验字段。"""
    evidences = list(response.get("evidences") or [])
    return {
        "case_id": case["case_id"],
        "user_input": case["question"],
        "response": response.get("answer") or "",
        "retrieved_contexts": [item.get("snippet") or "" for item in evidences],
        "reference": case.get("reference") or "",
        "retrieved_context_ids": [item.get("evidenceId") for item in evidences],
        "retrieved_document_ids": evidence_document_ids(evidences),
        "expected_document_ids": list(case.get("expected_document_ids") or []),
    }


def evaluate_case_offline(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    """对单条样本执行离线可确定评分。"""
    evidences = list(response.get("evidences") or [])
    answer = str(response.get("answer") or "")
    retrieved_ids = evidence_document_ids(evidences)
    expected_ids = list(case.get("expected_document_ids") or [])
    hit = calculate_document_hit(expected_ids, retrieved_ids)
    coverage = calculate_answer_point_coverage(answer, list(case.get("expected_answer_points") or []))
    reference_ok = has_valid_evidence_reference(answer, evidences)
    if case.get("case_type") == "manual_boundary":
        boundary = evaluate_boundary_case(case, response)
        passed = boundary["passed"]
    else:
        boundary = {}
        passed = bool(hit["top3_hit"] and reference_ok and evidences)
    return {
        "case_id": case["case_id"],
        "case_type": case.get("case_type"),
        "question": case.get("question"),
        "expected_document_ids": expected_ids,
        "retrieved_document_ids": retrieved_ids,
        "retrieved_source_paths": evidence_source_paths(evidences),
        "evidence_count": len(evidences),
        "top1_hit": hit["top1_hit"],
        "top3_hit": hit["top3_hit"],
        "hit_rank": hit["hit_rank"],
        "answer_point_coverage": coverage["coverage"],
        "matched_points": coverage["matched_points"],
        "missing_points": coverage["missing_points"],
        "evidence_reference_ok": reference_ok,
        "boundary_passed": boundary.get("passed"),
        "passed": passed,
        "answer": answer,
    }


def summarize_offline(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总离线评估结果和门槛判断。"""
    ragas_rows = [row for row in rows if row["case_type"] == "ragas"]
    boundary_rows = [row for row in rows if row["case_type"] == "manual_boundary"]
    top3_hits = sum(1 for row in ragas_rows if row["top3_hit"])
    top1_hits = sum(1 for row in ragas_rows if row["top1_hit"])
    reference_ok = sum(1 for row in ragas_rows if row["evidence_reference_ok"])
    empty_evidence = sum(1 for row in ragas_rows if row["evidence_count"] == 0)
    boundary_passed = sum(1 for row in boundary_rows if row["boundary_passed"])
    passed = top3_hits >= 9 and reference_ok == len(ragas_rows) and boundary_passed == len(boundary_rows) and empty_evidence <= 2
    return {
        "main_case_count": len(ragas_rows),
        "boundary_case_count": len(boundary_rows),
        "top1_hit_count": top1_hits,
        "top3_hit_count": top3_hits,
        "evidence_reference_ok_count": reference_ok,
        "empty_evidence_main_count": empty_evidence,
        "boundary_passed_count": boundary_passed,
        "average_answer_point_coverage": round(
            sum(float(row["answer_point_coverage"]) for row in ragas_rows) / max(len(ragas_rows), 1),
            4,
        ),
        "offline_passed": passed,
        "thresholds": {
            "top3_hit": ">= 9 / 10",
            "evidence_reference_ok": "10 / 10",
            "boundary_passed": "2 / 2",
            "empty_evidence_main": "<= 2",
        },
    }


def run_project_eval(
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
    documents_path: Path = DEFAULT_DOCUMENTS_PATH,
    rag_profile: str = "current",
    case_id: str | None = None,
    case_index: int | None = None,
) -> EvaluationRunResult:
    """执行索引、查询和离线评分，返回报告数据。"""
    cases = filter_eval_cases(load_jsonl(cases_path), case_id=case_id, case_index=case_index)
    documents = load_json(documents_path)
    client = create_test_client(rag_profile=rag_profile)
    index_eval_documents(client, documents)
    rows: list[dict[str, Any]] = []
    ragas_rows: list[dict[str, Any]] = []
    for case in cases:
        response = query_case(client, case)
        rows.append(evaluate_case_offline(case, response))
        if case.get("case_type") == "ragas":
            ragas_rows.append(build_ragas_input_row(case, response))
    return EvaluationRunResult(rows=rows, ragas_rows=ragas_rows, summary=summarize_offline(rows))


def run_project_ragas_input(
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
    documents_path: Path = DEFAULT_DOCUMENTS_PATH,
    rag_profile: str = "current",
    index_documents: bool = True,
    case_id: str | None = None,
    case_index: int | None = None,
) -> EvaluationRunResult:
    """只执行真实项目 RAG 并构造 Ragas 输入，不计算离线指标。"""
    all_ragas_cases = [case for case in load_jsonl(cases_path) if case.get("case_type") == "ragas"]
    cases = filter_eval_cases(all_ragas_cases, case_id=case_id, case_index=case_index)
    client = create_test_client(rag_profile=rag_profile)
    if index_documents:
        documents = load_json(documents_path)
        index_eval_documents(client, documents)
    ragas_rows = [build_ragas_input_row(case, query_case(client, case)) for case in cases]
    selected_case_ids = [row["case_id"] for row in ragas_rows]
    return EvaluationRunResult(
        rows=[],
        ragas_rows=ragas_rows,
        summary={
            "main_case_count": len(ragas_rows),
            "ragas_input_count": len(ragas_rows),
            "index_documents": index_documents,
            "selected_case_ids": selected_case_ids,
        },
    )


def ensure_output_dir(output_dir: Path) -> None:
    """创建评估输出目录。"""
    output_dir.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """按 UTF-8 写出 JSONL 文件。"""
    with path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    """按 UTF-8-SIG 写出 CSV，便于 Windows 表格软件打开中文。"""
    fieldnames = [
        "case_id",
        "case_type",
        "evidence_count",
        "top1_hit",
        "top3_hit",
        "hit_rank",
        "answer_point_coverage",
        "evidence_reference_ok",
        "boundary_passed",
        "passed",
        "expected_document_ids",
        "retrieved_document_ids",
        "missing_points",
        "answer",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            normalized = dict(row)
            for key in ("expected_document_ids", "retrieved_document_ids", "missing_points"):
                normalized[key] = json.dumps(normalized.get(key, []), ensure_ascii=False)
            writer.writerow(normalized)


def write_run_config(
    path: Path,
    *,
    mode: str,
    summary: dict[str, Any],
    output_paths: dict[str, str],
    ragas_version: str | None = None,
    ragas_settings: RagasEvalSettings | None = None,
    ragas_model_adapter: str | None = None,
    metric_names: list[str] | None = None,
    failure_reason: str | None = None,
) -> None:
    """写出本次评估运行配置，不记录评估模型 API Key。"""
    config = {
        "mode": mode,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "rag": {
            "profile": os.getenv(RAG_PROFILE_ENV, "current"),
            "RAG_STORE_BACKEND": os.getenv("RAG_STORE_BACKEND"),
            "RAG_DATABASE_SCHEMA": os.getenv("RAG_DATABASE_SCHEMA"),
            "RAG_TABLE_PREFIX": os.getenv("RAG_TABLE_PREFIX"),
            "RAG_EMBEDDING_PROVIDER": os.getenv("RAG_EMBEDDING_PROVIDER"),
            "RAG_EMBEDDING_MODEL": os.getenv("RAG_EMBEDDING_MODEL"),
            "RAG_VECTOR_DIMENSIONS": os.getenv("RAG_VECTOR_DIMENSIONS"),
            "RAG_ANSWER_PROVIDER": os.getenv("RAG_ANSWER_PROVIDER"),
            "RAG_LLM_MODEL": os.getenv("RAG_LLM_MODEL"),
            "RAG_RERANK_PROVIDER": os.getenv("RAG_RERANK_PROVIDER"),
            "RAG_RERANK_MODEL": os.getenv("RAG_RERANK_MODEL"),
        },
        "ragas": {
            "version": ragas_version,
            "provider": ragas_settings.provider if ragas_settings else os.getenv("RAGAS_EVAL_PROVIDER"),
            "ragasProvider": ragas_settings.ragas_provider if ragas_settings else None,
            "baseUrl": ragas_settings.base_url if ragas_settings else os.getenv("RAGAS_EVAL_BASE_URL"),
            "llmModel": ragas_settings.llm_model if ragas_settings else os.getenv("RAGAS_EVAL_LLM_MODEL"),
            "embeddingModel": ragas_settings.embedding_model if ragas_settings else os.getenv("RAGAS_EVAL_EMBEDDING_MODEL"),
            "timeoutSeconds": ragas_settings.timeout_seconds if ragas_settings else os.getenv("RAGAS_EVAL_TIMEOUT_SECONDS"),
            "maxRetries": ragas_settings.max_retries if ragas_settings else os.getenv("RAGAS_EVAL_MAX_RETRIES"),
            "maxWaitSeconds": ragas_settings.max_wait_seconds if ragas_settings else os.getenv("RAGAS_EVAL_MAX_WAIT_SECONDS"),
            "maxWorkers": ragas_settings.max_workers if ragas_settings else os.getenv("RAGAS_EVAL_MAX_WORKERS"),
            "batchSize": ragas_settings.batch_size if ragas_settings else os.getenv("RAGAS_EVAL_BATCH_SIZE"),
            "temperature": ragas_settings.temperature if ragas_settings else os.getenv("RAGAS_EVAL_TEMPERATURE", "0"),
            "ragasModelAdapter": ragas_model_adapter,
            "metricNames": metric_names or [],
            "failureReason": failure_reason,
        },
        "summary": summary,
        "outputs": output_paths,
    }
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def write_manual_review(
    path: Path,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    *,
    ragas_version: str | None = None,
    ragas_failure_reason: str | None = None,
    ragas_metric_names: list[str] | None = None,
    rerun_command: str | None = None,
) -> None:
    """写出人工复核 Markdown 报告。"""
    lines = [
        "# Ragas 小样本人工复核记录",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"- Ragas 版本：{ragas_version or '未运行真实 Ragas 评分'}",
        f"- RAG 运行档位：`{os.getenv(RAG_PROFILE_ENV, 'current')}`",
        f"- RAG_STORE_BACKEND：`{os.getenv('RAG_STORE_BACKEND')}`",
        f"- RAG_TABLE_PREFIX：`{os.getenv('RAG_TABLE_PREFIX')}`",
        f"- RAG_EMBEDDING_PROVIDER：`{os.getenv('RAG_EMBEDDING_PROVIDER')}`",
        f"- RAG_ANSWER_PROVIDER：`{os.getenv('RAG_ANSWER_PROVIDER')}`",
        f"- RAG_RERANK_PROVIDER：`{os.getenv('RAG_RERANK_PROVIDER')}`",
        f"- 离线门槛结果：{'通过' if summary.get('offline_passed') else '未通过'}",
        "",
        "## 汇总",
        "",
        f"- 主样本 top3 命中：{summary.get('top3_hit_count')} / {summary.get('main_case_count')}",
        f"- 引用结构合格：{summary.get('evidence_reference_ok_count')} / {summary.get('main_case_count')}",
        f"- 边界样本通过：{summary.get('boundary_passed_count')} / {summary.get('boundary_case_count')}",
        f"- 主样本空 evidence：{summary.get('empty_evidence_main_count')}",
        f"- 关键点平均覆盖率：{summary.get('average_answer_point_coverage')}",
        "",
        "## 真实 Ragas 分数汇总",
        "",
    ]
    ragas_scores = summary.get("ragas_scores") or {}
    if ragas_scores:
        for key, value in ragas_scores.items():
            lines.append(f"- `{key}`：{value}")
    elif ragas_failure_reason:
        lines.append("- 本次真实 Ragas 评分未完成，未生成 `ragas_scores.csv`。")
    else:
        lines.append("- 未运行真实 Ragas 评分。")
    if ragas_metric_names:
        lines.extend(["", f"- 指标：{', '.join(f'`{name}`' for name in ragas_metric_names)}"])
    lines.extend(
        [
            "",
            "## 真实 Ragas 失败原因",
            "",
            ragas_failure_reason or "- 无",
            "",
            "## 复跑命令",
            "",
        ]
    )
    if rerun_command:
        lines.extend(["```powershell", rerun_command, "```"])
    else:
        lines.append("- 如需真实评分，请配置 `RAGAS_EVAL_*` 环境变量后运行 `python -B ai-python/tests/evaluation/run_ragas_small_eval.py --mode ragas`。")
    lines.extend(
        [
            "",
            "## 样本明细",
            "",
            "| 用例 | 类型 | 通过 | top3 | 引用 | evidence | 缺失关键点 |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        missing = "、".join(row.get("missing_points") or [])
        lines.append(
            "| {case_id} | {case_type} | {passed} | {top3_hit} | {reference_ok} | {evidence_count} | {missing} |".format(
                case_id=row["case_id"],
                case_type=row["case_type"],
                passed="是" if row["passed"] else "否",
                top3_hit="是" if row["top3_hit"] else "否",
                reference_ok="是" if row["evidence_reference_ok"] else "否",
                evidence_count=row["evidence_count"],
                missing=missing or "-",
            )
        )
    lines.extend(
        [
            "",
            "## 下一步建议",
            "",
            "- 如果 top3 命中不足，优先查看 `retrieved_document_ids` 与期望资料的差异，再定位 Multi-Query、BM25、向量召回和 RRF 排序。",
            "- 如果引用结构不合格，优先检查回答生成是否保留 `[evidenceId]`，以及 evidence 是否包含标题、章节、来源和分数。",
            "- 如果边界样本失败，优先检查 metadataFilter 是否被严格应用，以及无关问题是否被低相关 evidence 误支撑。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def get_ragas_version() -> str:
    """读取当前安装的 Ragas 版本。"""
    ragas = importlib.import_module("ragas")
    return str(getattr(ragas, "__version__", "unknown"))


def _require_env(name: str, description: str) -> str:
    """读取必填环境变量并输出中文错误。"""
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"{name} 未配置，请指定 Ragas 真实评分所需的{description}。")
    return value.strip()


def _first_env(*names: str) -> str | None:
    """按优先级读取第一个非空环境变量。"""
    for name in names:
        value = os.getenv(name)
        if value is not None and value.strip():
            return value.strip()
    return None


def _require_first_env(names: tuple[str, ...], description: str) -> str:
    """按优先级读取必填环境变量，全部缺失时输出中文错误。"""
    value = _first_env(*names)
    if value:
        return value
    joined = " 或 ".join(names)
    raise RuntimeError(f"{joined} 未配置，请指定 Ragas 真实评分所需的{description}。")


def _parse_positive_float(name: str, default: str) -> float:
    """读取必须大于 0 的数字环境变量。"""
    raw_value = (os.getenv(name) or default).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是数字，当前值为：{raw_value}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} 必须大于 0，当前值为：{raw_value}")
    return value


def _parse_non_negative_int(name: str, default: str) -> int:
    """读取非负整数环境变量。"""
    raw_value = (os.getenv(name) or default).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数，当前值为：{raw_value}") from exc
    if value < 0:
        raise RuntimeError(f"{name} 必须大于或等于 0，当前值为：{raw_value}")
    return value


def _parse_positive_int(name: str, default: str) -> int:
    """读取正整数环境变量。"""
    raw_value = (os.getenv(name) or default).strip()
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数，当前值为：{raw_value}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} 必须大于 0，当前值为：{raw_value}")
    return value


def _parse_optional_positive_int(name: str) -> int | None:
    """读取可选正整数环境变量，空值表示交给 Ragas 默认处理。"""
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return None
    try:
        value = int(raw_value.strip())
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是整数，当前值为：{raw_value}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} 必须大于 0，当前值为：{raw_value}")
    return value


def _parse_temperature(name: str, default: str) -> float:
    """读取 0 到 2 范围内的 temperature 配置。"""
    raw_value = (os.getenv(name) or default).strip()
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(f"{name} 必须是数字，当前值为：{raw_value}") from exc
    if value < 0 or value > 2:
        raise RuntimeError(f"{name} 必须在 0 到 2 之间，当前值为：{raw_value}")
    return value


def parse_ragas_metric_keys(raw_value: str | None) -> tuple[str, ...]:
    """读取 Ragas 指标子集配置，未配置时默认运行四项指标。"""
    if raw_value is None or not raw_value.strip():
        return DEFAULT_RAGAS_METRIC_KEYS
    result: list[str] = []
    invalid: list[str] = []
    for item in raw_value.split(","):
        normalized = item.strip().lower().replace("-", "_").replace(" ", "_")
        if not normalized:
            continue
        metric_key = RAGAS_METRIC_ALIASES.get(normalized)
        if metric_key is None:
            invalid.append(item.strip())
            continue
        if metric_key not in result:
            result.append(metric_key)
    if invalid:
        allowed = "、".join(DEFAULT_RAGAS_METRIC_KEYS)
        invalid_text = "、".join(invalid)
        raise RuntimeError(f"{RAGAS_METRICS_ENV} 包含不支持的指标：{invalid_text}。允许值：{allowed}")
    if not result:
        raise RuntimeError(f"{RAGAS_METRICS_ENV} 至少需要配置一个有效指标。")
    return tuple(result)


def _validate_base_url(value: str | None, *, required: bool) -> str | None:
    """校验 OpenAI 或 OpenAI-compatible base_url。"""
    normalized = (value or "").strip()
    if required and not normalized:
        raise RuntimeError("RAGAS_EVAL_BASE_URL 未配置，openai-compatible 模式必须指定兼容 OpenAI 的服务地址。")
    if normalized and not normalized.startswith(("http://", "https://")):
        raise RuntimeError("RAGAS_EVAL_BASE_URL 必须以 http:// 或 https:// 开头。")
    return normalized or None


def load_ragas_eval_settings() -> RagasEvalSettings:
    """校验并读取真实 Ragas 评分所需的评估模型环境变量。"""
    provider = (os.getenv("RAGAS_EVAL_PROVIDER") or "openai-compatible").strip()
    if provider not in {"openai-compatible", "openai"}:
        raise RuntimeError("RAGAS_EVAL_PROVIDER 只允许 openai-compatible 或 openai。")
    api_key_names = ("RAGAS_EVAL_API_KEY",) if provider == "openai" else ("RAGAS_EVAL_API_KEY", "DASHSCOPE_API_KEY")
    api_key = _require_first_env(api_key_names, "API Key")
    llm_model = _first_env("RAGAS_EVAL_LLM_MODEL", "RAG_LLM_MODEL") or DEFAULT_RAGAS_LLM_MODEL
    embedding_model = (
        _first_env("RAGAS_EVAL_EMBEDDING_MODEL", "RAG_EMBEDDING_MODEL", "DASHSCOPE_EMBEDDING_MODEL")
        or DEFAULT_RAGAS_EMBEDDING_MODEL
    )
    if provider == "openai-compatible":
        base_url_value = (
            _first_env("RAGAS_EVAL_BASE_URL", "RAG_LLM_BASE_URL", "RAG_EMBEDDING_BASE_URL", "DASHSCOPE_EMBEDDING_BASE_URL")
            or DEFAULT_RAGAS_COMPATIBLE_BASE_URL
        )
    else:
        base_url_value = os.getenv("RAGAS_EVAL_BASE_URL")
    base_url = _validate_base_url(base_url_value, required=provider == "openai-compatible")
    return RagasEvalSettings(
        provider=provider,
        ragas_provider="openai",
        base_url=base_url,
        api_key=api_key,
        llm_model=llm_model,
        embedding_model=embedding_model,
        timeout_seconds=_parse_positive_float("RAGAS_EVAL_TIMEOUT_SECONDS", "60"),
        max_retries=_parse_non_negative_int("RAGAS_EVAL_MAX_RETRIES", "2"),
        max_wait_seconds=_parse_positive_float("RAGAS_EVAL_MAX_WAIT_SECONDS", "10"),
        max_workers=_parse_positive_int("RAGAS_EVAL_MAX_WORKERS", "2"),
        batch_size=_parse_optional_positive_int("RAGAS_EVAL_BATCH_SIZE"),
        temperature=_parse_temperature("RAGAS_EVAL_TEMPERATURE", "0"),
        max_tokens=_parse_optional_positive_int("RAGAS_EVAL_MAX_TOKENS"),
        metric_keys=parse_ragas_metric_keys(os.getenv(RAGAS_METRICS_ENV)),
    )


def ensure_ragas_eval_config() -> RagasEvalSettings:
    """兼容旧测试入口，返回已校验的 Ragas 评分配置。"""
    return load_ragas_eval_settings()


def require_ragas_core_dependencies() -> None:
    """校验现代主路径依赖，给出可直接执行的安装提示。"""
    missing: list[str] = []
    for module_name in ("ragas", "openai", "datasets"):
        try:
            importlib.import_module(module_name)
        except ImportError:
            missing.append(module_name)
    if missing:
        modules = "、".join(missing)
        raise RuntimeError(
            f"运行 Ragas 真实评分缺少依赖：{modules}。请先执行：python -m pip install -r ai-python/requirements.txt"
        )


def _build_run_config(settings: RagasEvalSettings) -> Any | None:
    """按可用 Ragas 版本创建 RunConfig。"""
    try:
        from ragas.run_config import RunConfig

        return RunConfig(
            timeout=int(settings.timeout_seconds),
            max_retries=settings.max_retries,
            max_wait=int(settings.max_wait_seconds),
            max_workers=settings.max_workers,
        )
    except Exception:
        return None


def _metric_name(metric: Any) -> str:
    """提取指标名称，优先使用 Ragas metric.name。"""
    return str(getattr(metric, "name", None) or metric.__class__.__name__)


def _import_wrapper(module_name: str, class_name: str) -> Any:
    """从 Ragas 顶层或 base 子模块导入 legacy wrapper。"""
    module = importlib.import_module(module_name)
    wrapper = getattr(module, class_name, None)
    if wrapper is not None:
        return wrapper
    base_module = importlib.import_module(f"{module_name}.base")
    return getattr(base_module, class_name)


def _instantiate_metric(metric_class: Any, *, llm: Any, embeddings: Any) -> Any:
    """按不同 Ragas 指标签名创建指标实例。"""
    try:
        return metric_class(llm=llm, embeddings=embeddings)
    except TypeError:
        try:
            return metric_class(llm=llm)
        except TypeError:
            metric = metric_class()
            if getattr(metric, "llm", None) is None:
                setattr(metric, "llm", llm)
            if getattr(metric, "embeddings", None) is None:
                setattr(metric, "embeddings", embeddings)
            return metric


class _RagasEmbeddingCompatibilityWrapper:
    """兼容 Ragas 0.4 新旧指标的 embedding 调用接口。"""

    def __init__(self, embeddings: Any):
        self.embeddings = embeddings

    def embed_text(self, text: str, **kwargs: Any) -> list[float]:
        """转发现代单文本向量接口。"""
        return self.embeddings.embed_text(text, **kwargs)

    async def aembed_text(self, text: str, **kwargs: Any) -> list[float]:
        """转发现代异步单文本向量接口。"""
        return await self.embeddings.aembed_text(text, **kwargs)

    def embed_texts(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """转发现代批量向量接口。"""
        return self.embeddings.embed_texts(texts, **kwargs)

    async def aembed_texts(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        """转发现代异步批量向量接口。"""
        return await self.embeddings.aembed_texts(texts, **kwargs)

    def embed_query(self, text: str) -> list[float]:
        """兼容 legacy AnswerRelevancy 需要的查询向量接口。"""
        return self.embed_text(text)

    async def aembed_query(self, text: str) -> list[float]:
        """兼容 legacy AnswerRelevancy 需要的异步查询向量接口。"""
        return await self.aembed_text(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """兼容 legacy 指标需要的文档向量接口。"""
        return self.embed_texts(texts)

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        """兼容 legacy 指标需要的异步文档向量接口。"""
        return await self.aembed_texts(texts)


def build_modern_ragas_adapter(settings: RagasEvalSettings) -> RagasModelAdapter:
    """构造 Ragas 0.4 现代 OpenAI client 与 evaluate 兼容指标路径。

    使用同步 openai.OpenAI 客户端，避免 AsyncOpenAI + asyncio.run 组合下
    httpx.AsyncClient 跨线程死锁。Ragas 内部 _run_async_in_current_loop
    会开子线程跑协程，同步客户端完全兼容。
    """
    try:
        from openai import OpenAI
        from ragas.embeddings import OpenAIEmbeddings
        from ragas.llms import llm_factory
        from ragas.metrics import AnswerRelevancy, Faithfulness, LLMContextPrecisionWithReference, LLMContextRecall
    except ImportError as exc:
        raise RuntimeError(
            "运行 Ragas 现代评分路径缺少 ragas/openai 依赖，请执行：python -m pip install -r ai-python/requirements.txt"
        ) from exc

    signature = inspect.signature(llm_factory)
    if "client" not in signature.parameters:
        raise RuntimeError("当前 Ragas 的 llm_factory 不支持 client 参数，无法使用现代评分路径。")

    client_kwargs: dict[str, Any] = {
        "api_key": settings.api_key,
        "timeout": settings.timeout_seconds,
    }
    if settings.base_url:
        client_kwargs["base_url"] = settings.base_url
    client = OpenAI(**client_kwargs)
    llm_kwargs: dict[str, Any] = {
        "model": settings.llm_model,
        "provider": settings.ragas_provider,
        "client": client,
        "temperature": settings.temperature,
    }
    has_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
    if settings.max_tokens is not None and has_kwargs:
        llm_kwargs["max_tokens"] = settings.max_tokens
    llm = llm_factory(**llm_kwargs)
    embeddings = _RagasEmbeddingCompatibilityWrapper(OpenAIEmbeddings(client=client, model=settings.embedding_model))
    metric_class_map = {
        "context_precision": LLMContextPrecisionWithReference,
        "context_recall": LLMContextRecall,
        "faithfulness": Faithfulness,
        "answer_relevancy": AnswerRelevancy,
    }
    metric_classes = [metric_class_map[key] for key in settings.metric_keys]
    metrics = [_instantiate_metric(metric_class, llm=llm, embeddings=embeddings) for metric_class in metric_classes]
    return RagasModelAdapter(
        adapter_name="modern",
        llm=llm,
        embeddings=embeddings,
        metrics=metrics,
        metric_names=[_metric_name(metric) for metric in metrics],
        construction_errors=[],
    )


def build_legacy_ragas_adapter(settings: RagasEvalSettings, previous_errors: list[str] | None = None) -> RagasModelAdapter:
    """构造 LangChain wrapper 与 legacy 指标路径。"""
    try:
        from langchain_openai import ChatOpenAI, OpenAIEmbeddings
    except ImportError as exc:
        raise RuntimeError(
            "Ragas 现代路径不可用，fallback 需要 langchain-openai。请执行：python -m pip install -r ai-python/requirements.txt"
        ) from exc

    run_config = _build_run_config(settings)
    client_kwargs: dict[str, Any] = {
        "api_key": settings.api_key,
        "timeout": settings.timeout_seconds,
    }
    if settings.base_url:
        client_kwargs["base_url"] = settings.base_url
    chat = ChatOpenAI(model=settings.llm_model, temperature=settings.temperature, **client_kwargs)
    embedding = OpenAIEmbeddings(model=settings.embedding_model, **client_kwargs)
    llm_wrapper_class = _import_wrapper("ragas.llms", "LangchainLLMWrapper")
    embedding_wrapper_class = _import_wrapper("ragas.embeddings", "LangchainEmbeddingsWrapper")
    llm = llm_wrapper_class(chat, run_config=run_config)
    embeddings = embedding_wrapper_class(embedding, run_config=run_config)
    metrics_module = importlib.import_module("ragas.metrics")
    metric_class_map = {
        "context_precision": getattr(metrics_module, "LLMContextPrecisionWithReference", None)
        or getattr(metrics_module, "LLMContextPrecisionWithoutReference", None),
        "context_recall": getattr(metrics_module, "LLMContextRecall", None) or getattr(metrics_module, "ContextRecall", None),
        "faithfulness": getattr(metrics_module, "Faithfulness", None),
        "answer_relevancy": getattr(metrics_module, "ResponseRelevancy", None)
        or getattr(metrics_module, "AnswerRelevancy", None),
    }
    metric_classes = [metric_class_map[key] for key in settings.metric_keys]
    if any(metric_class is None for metric_class in metric_classes):
        raise RuntimeError("当前 Ragas legacy 指标不完整，无法构造真实评分指标。")
    metrics = [_instantiate_metric(metric_class, llm=llm, embeddings=embeddings) for metric_class in metric_classes]
    return RagasModelAdapter(
        adapter_name="legacy",
        llm=llm,
        embeddings=embeddings,
        metrics=metrics,
        metric_names=[_metric_name(metric) for metric in metrics],
        construction_errors=previous_errors or [],
    )


def build_ragas_model_adapter(settings: RagasEvalSettings) -> RagasModelAdapter:
    """优先构造现代适配器，失败后回退 legacy wrapper。"""
    errors: list[str] = []
    try:
        return build_modern_ragas_adapter(settings)
    except Exception as exc:
        errors.append(f"modern: {exc}")
    try:
        return build_legacy_ragas_adapter(settings, previous_errors=errors)
    except Exception as exc:
        errors.append(f"legacy: {exc}")
        detail = "；".join(errors)
        raise RuntimeError(f"无法构造 Ragas 真实评分模型适配器：{detail}") from exc


def _dataset_rows_for_ragas(ragas_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只保留 Ragas 真实评分需要的标准字段。"""
    return [
        {
            "user_input": row["user_input"],
            "response": row["response"],
            "retrieved_contexts": row["retrieved_contexts"],
            "reference": row["reference"],
        }
        for row in ragas_rows
    ]


def _call_ragas_evaluate(
    ragas_module: Any,
    *,
    dataset: Any,
    adapter: RagasModelAdapter,
    run_config: Any | None,
    batch_size: int | None,
) -> Any:
    """调用 Ragas 评分入口。

    modern 适配器使用同步 openai.OpenAI 客户端，优先走 evaluate()
    同步路径以避免 httpx.AsyncClient 跨线程死锁。
    """
    kwargs = {
        "dataset": dataset,
        "metrics": adapter.metrics,
        "llm": adapter.llm,
        "embeddings": adapter.embeddings,
        "run_config": run_config,
        "raise_exceptions": False,
        "show_progress": True,
        "batch_size": batch_size,
    }
    evaluate_func = getattr(ragas_module, "evaluate", None)
    if evaluate_func is not None:
        return evaluate_func(**kwargs)
    aevaluate_func = getattr(ragas_module, "aevaluate", None)
    if aevaluate_func is not None:
        return asyncio.run(aevaluate_func(**kwargs))
    raise RuntimeError("当前 Ragas 版本没有 evaluate 或 aevaluate 入口，无法运行真实评分。")


def _rows_from_dataframe(frame: Any) -> list[dict[str, Any]]:
    """把 pandas DataFrame 转成普通字典列表。"""
    return list(frame.to_dict(orient="records"))


def ragas_result_to_rows(result: Any) -> list[dict[str, Any]]:
    """兼容 Ragas 不同结果形态，统一转换为行字典列表。"""
    if hasattr(result, "to_pandas"):
        return _rows_from_dataframe(result.to_pandas())
    scores = getattr(result, "scores", None)
    if scores is not None:
        if isinstance(scores, list):
            return [dict(row) for row in scores]
        if isinstance(scores, dict):
            keys = list(scores.keys())
            lengths = [len(value) for value in scores.values() if isinstance(value, list)]
            if lengths and len(set(lengths)) == 1:
                return [{key: scores[key][index] for key in keys} for index in range(lengths[0])]
            return [dict(scores)]
    if isinstance(result, list):
        return [dict(row) for row in result]
    if isinstance(result, dict):
        values = list(result.values())
        if values and all(isinstance(value, list) for value in values):
            lengths = {len(value) for value in values}
            if len(lengths) == 1:
                keys = list(result.keys())
                return [{key: result[key][index] for key in keys} for index in range(next(iter(lengths)))]
        return [dict(result)]
    raise RuntimeError(f"无法识别 Ragas 评分结果类型：{type(result).__name__}")


def write_ragas_scores_csv(output_csv: Path, result: Any, ragas_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """写出真实 Ragas 分数 CSV，并只聚合数值指标列。"""
    import pandas as pd

    rows = ragas_result_to_rows(result)
    if len(rows) != len(ragas_rows):
        raise RuntimeError(f"Ragas 结果行数与输入样本数不一致：结果 {len(rows)} 行，输入 {len(ragas_rows)} 行。")
    normalized_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        normalized = dict(row)
        normalized.pop("case_id", None)
        normalized_rows.append({"case_id": ragas_rows[index]["case_id"], **normalized})
    frame = pd.DataFrame(normalized_rows)
    frame.to_csv(output_csv, index=False, encoding="utf-8-sig")
    summary: dict[str, Any] = {}
    for column in frame.columns:
        if column == "case_id":
            continue
        numeric_values: list[float] = []
        for value in frame[column].tolist():
            if isinstance(value, bool):
                numeric_values.append(float(value))
            elif isinstance(value, (int, float)) and not math.isnan(float(value)):
                numeric_values.append(float(value))
        if numeric_values:
            summary[column] = round(sum(numeric_values) / len(numeric_values), 4)
    return summary


def run_ragas_metrics(
    ragas_rows: list[dict[str, Any]],
    output_csv: Path,
    *,
    settings: RagasEvalSettings | None = None,
) -> RagasMetricsRunResult:
    """运行真实 Ragas LLM 指标，并把结果写出为 CSV。"""
    settings = settings or load_ragas_eval_settings()
    require_ragas_core_dependencies()
    ragas = importlib.import_module("ragas")
    ragas_version = str(getattr(ragas, "__version__", "unknown"))
    from datasets import Dataset

    adapter = build_ragas_model_adapter(settings)
    dataset = Dataset.from_list(_dataset_rows_for_ragas(ragas_rows))
    run_config = _build_run_config(settings)
    result = _call_ragas_evaluate(
        ragas,
        dataset=dataset,
        adapter=adapter,
        run_config=run_config,
        batch_size=settings.batch_size,
    )
    summary = write_ragas_scores_csv(output_csv, result, ragas_rows)
    return RagasMetricsRunResult(
        summary=summary,
        ragas_version=ragas_version,
        model_adapter=adapter.adapter_name,
        metric_names=adapter.metric_names,
    )


def run_ragas_case_by_case(
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
    documents_path: Path = DEFAULT_DOCUMENTS_PATH,
    rag_profile: str = "current",
    index_documents: bool = True,
    output_dir: Path,
    settings: RagasEvalSettings | None = None,
) -> dict[str, Any]:
    """逐条运行 ragas 用例的 RAG 查询和 Ragas 评分，汇总结果。

    每条用例独立执行 RAG 检索 + Ragas LLM 评分，失败不影响后续用例。
    最后合并所有成功用例的分数，写出汇总 CSV。
    """
    settings = settings or load_ragas_eval_settings()
    all_cases = [case for case in load_jsonl(cases_path) if case.get("case_type") == "ragas"]
    if not all_cases:
        raise RuntimeError("没有找到 case_type=ragas 的评估用例。")
    client = create_test_client(rag_profile=rag_profile)
    if index_documents:
        documents = load_json(documents_path)
        index_eval_documents(client, documents)

    per_case_results: list[dict[str, Any]] = []
    success_count = 0
    fail_count = 0
    total = len(all_cases)

    for idx, case in enumerate(all_cases):
        case_id = case.get("case_id", f"case-{idx}")
        print(f"\n{'='*60}")
        print(f"[{idx+1}/{total}] 开始处理用例: {case_id}")
        print(f"{'='*60}")
        try:
            response = query_case(client, case)
            ragas_row = build_ragas_input_row(case, response)
        except Exception as exc:
            print(f"[{case_id}] RAG 查询失败: {exc}")
            per_case_results.append({
                "case_id": case_id,
                "rag_query_ok": False,
                "rag_query_error": str(exc),
                "ragas_ok": False,
                "ragas_scores": {},
            })
            fail_count += 1
            continue

        case_csv = output_dir / f"ragas_scores_{case_id}.csv"
        try:
            if case_csv.exists():
                case_csv.unlink()
            ragas_result = run_ragas_metrics([ragas_row], case_csv, settings=settings)
            per_case_results.append({
                "case_id": case_id,
                "rag_query_ok": True,
                "ragas_ok": True,
                "ragas_scores": ragas_result.summary,
                "ragas_version": ragas_result.ragas_version,
                "metric_names": ragas_result.metric_names,
            })
            print(f"[{case_id}] Ragas 评分完成: {ragas_result.summary}")
            success_count += 1
        except Exception as exc:
            print(f"[{case_id}] Ragas 评分失败: {exc}")
            per_case_results.append({
                "case_id": case_id,
                "rag_query_ok": True,
                "ragas_ok": False,
                "ragas_error": str(exc),
                "ragas_scores": {},
            })
            fail_count += 1

    aggregated = _aggregate_per_case_results(per_case_results, output_dir)
    print(f"\n汇总完成: 成功 {success_count}/{total}，失败 {fail_count}/{total}")
    return aggregated


def _aggregate_per_case_results(
    per_case_results: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    """合并逐条结果，计算平均分，写出汇总 CSV。"""
    import pandas as pd

    metric_names_set: set[str] = set()
    for item in per_case_results:
        if item.get("ragas_ok"):
            metric_names_set.update(item.get("metric_names", []))
    metric_names = sorted(metric_names_set)

    rows: list[dict[str, Any]] = []
    for item in per_case_results:
        row: dict[str, Any] = {"case_id": item["case_id"]}
        if item.get("ragas_ok"):
            for metric in metric_names:
                row[metric] = item["ragas_scores"].get(metric)
        else:
            for metric in metric_names:
                row[metric] = None
        rows.append(row)

    frame = pd.DataFrame(rows)
    summary: dict[str, Any] = {
        "total_cases": len(per_case_results),
        "success_count": sum(1 for item in per_case_results if item.get("ragas_ok")),
        "fail_count": sum(1 for item in per_case_results if not item.get("ragas_ok")),
    }
    for column in frame.columns:
        if column == "case_id":
            continue
        numeric_values: list[float] = []
        for value in frame[column].tolist():
            if value is None:
                continue
            if isinstance(value, bool):
                numeric_values.append(float(value))
            elif isinstance(value, (int, float)) and not math.isnan(float(value)):
                numeric_values.append(float(value))
        if numeric_values:
            summary[column] = round(sum(numeric_values) / len(numeric_values), 4)
            summary[f"{column}_count"] = len(numeric_values)

    summary_csv = output_dir / "ragas_scores_summary.csv"
    frame.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    summary["summary_csv"] = str(summary_csv)
    return summary
