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
    temperature: float


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


def configure_deterministic_rag_environment() -> None:
    """在导入 FastAPI app 前固定内存检索和本地模型，避免评估误连真实服务。"""
    os.environ["RAG_STORE_BACKEND"] = "memory"
    os.environ["RAG_EMBEDDING_PROVIDER"] = "hash"
    os.environ["RAG_VECTOR_DIMENSIONS"] = "1024"
    os.environ["RAG_ANSWER_PROVIDER"] = "local"
    os.environ["RAG_RERANK_PROVIDER"] = "local"


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


def create_test_client():
    """创建 Python RAG 内部接口测试客户端，必须在环境变量设置后导入 app。"""
    ensure_ai_python_path()
    configure_deterministic_rag_environment()
    from fastapi.testclient import TestClient

    from app.main import app
    from app.api import rag as rag_api

    clear_in_memory_store(rag_api.store)
    return TestClient(app)


def clear_in_memory_store(store: Any) -> None:
    """清理内存 RAG 仓库，避免多次评估互相污染。"""
    for attr in ("documents", "chunks", "term_freqs", "doc_freq", "embeddings"):
        value = getattr(store, attr, None)
        if hasattr(value, "clear"):
            value.clear()


def index_eval_documents(client: Any, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把评估文档索引进项目 Python RAG 内存仓库。"""
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
    refusal_text = any(fragment in answer for fragment in ("没有检索到足够相关", "请先上传", "无可用证据", "知识库"))
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
) -> EvaluationRunResult:
    """执行索引、查询和离线评分，返回报告数据。"""
    cases = load_jsonl(cases_path)
    documents = load_json(documents_path)
    client = create_test_client()
    index_eval_documents(client, documents)
    rows: list[dict[str, Any]] = []
    ragas_rows: list[dict[str, Any]] = []
    for case in cases:
        response = query_case(client, case)
        rows.append(evaluate_case_offline(case, response))
        if case.get("case_type") == "ragas":
            ragas_rows.append(build_ragas_input_row(case, response))
    return EvaluationRunResult(rows=rows, ragas_rows=ragas_rows, summary=summarize_offline(rows))


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
            "RAG_STORE_BACKEND": os.getenv("RAG_STORE_BACKEND"),
            "RAG_EMBEDDING_PROVIDER": os.getenv("RAG_EMBEDDING_PROVIDER"),
            "RAG_VECTOR_DIMENSIONS": os.getenv("RAG_VECTOR_DIMENSIONS"),
            "RAG_ANSWER_PROVIDER": os.getenv("RAG_ANSWER_PROVIDER"),
            "RAG_RERANK_PROVIDER": os.getenv("RAG_RERANK_PROVIDER"),
        },
        "ragas": {
            "version": ragas_version,
            "provider": ragas_settings.provider if ragas_settings else os.getenv("RAGAS_EVAL_PROVIDER"),
            "ragasProvider": ragas_settings.ragas_provider if ragas_settings else None,
            "baseUrl": ragas_settings.base_url if ragas_settings else os.getenv("RAGAS_EVAL_BASE_URL"),
            "llmModel": ragas_settings.llm_model if ragas_settings else os.getenv("RAGAS_EVAL_LLM_MODEL"),
            "embeddingModel": ragas_settings.embedding_model if ragas_settings else os.getenv("RAGAS_EVAL_EMBEDDING_MODEL"),
            "timeoutSeconds": ragas_settings.timeout_seconds if ragas_settings else os.getenv("RAGAS_EVAL_TIMEOUT_SECONDS"),
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
        f"- RAG_STORE_BACKEND：`{os.getenv('RAG_STORE_BACKEND')}`",
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
    api_key = _require_env("RAGAS_EVAL_API_KEY", "API Key")
    llm_model = _require_env("RAGAS_EVAL_LLM_MODEL", "LLM 模型名称")
    embedding_model = _require_env("RAGAS_EVAL_EMBEDDING_MODEL", "embedding 模型名称")
    base_url = _validate_base_url(os.getenv("RAGAS_EVAL_BASE_URL"), required=provider == "openai-compatible")
    return RagasEvalSettings(
        provider=provider,
        ragas_provider="openai",
        base_url=base_url,
        api_key=api_key,
        llm_model=llm_model,
        embedding_model=embedding_model,
        timeout_seconds=_parse_positive_float("RAGAS_EVAL_TIMEOUT_SECONDS", "60"),
        temperature=_parse_temperature("RAGAS_EVAL_TEMPERATURE", "0"),
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


def _build_run_config(timeout_seconds: float) -> Any | None:
    """按可用 Ragas 版本创建 RunConfig。"""
    try:
        from ragas.run_config import RunConfig

        return RunConfig(timeout=timeout_seconds)
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


def build_modern_ragas_adapter(settings: RagasEvalSettings) -> RagasModelAdapter:
    """构造 Ragas 0.4 现代 OpenAI client 与 collections 指标路径。"""
    try:
        from openai import AsyncOpenAI
        from ragas.embeddings import OpenAIEmbeddings
        from ragas.llms import llm_factory
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
    client = AsyncOpenAI(**client_kwargs)
    llm = llm_factory(
        settings.llm_model,
        provider=settings.ragas_provider,
        client=client,
        temperature=settings.temperature,
    )
    embeddings = OpenAIEmbeddings(client=client, model=settings.embedding_model)
    collections = importlib.import_module("ragas.metrics.collections")
    context_precision_class = getattr(collections, "ContextPrecisionWithReference", None) or getattr(
        collections, "ContextPrecision", None
    )
    metric_classes = [
        context_precision_class,
        getattr(collections, "ContextRecall", None),
        getattr(collections, "Faithfulness", None),
        getattr(collections, "AnswerRelevancy", None),
    ]
    if any(metric_class is None for metric_class in metric_classes):
        raise RuntimeError("当前 Ragas collections 指标不完整，无法构造现代评分路径。")
    metrics = [
        metric_classes[0](llm=llm),
        metric_classes[1](llm=llm),
        metric_classes[2](llm=llm),
        metric_classes[3](llm=llm, embeddings=embeddings),
    ]
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

    run_config = _build_run_config(settings.timeout_seconds)
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
    metric_classes = [
        getattr(metrics_module, "LLMContextPrecisionWithReference", None)
        or getattr(metrics_module, "LLMContextPrecisionWithoutReference", None),
        getattr(metrics_module, "LLMContextRecall", None) or getattr(metrics_module, "ContextRecall", None),
        getattr(metrics_module, "Faithfulness", None),
        getattr(metrics_module, "ResponseRelevancy", None) or getattr(metrics_module, "AnswerRelevancy", None),
    ]
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


def _call_ragas_evaluate(ragas_module: Any, *, dataset: Any, adapter: RagasModelAdapter, run_config: Any | None) -> Any:
    """优先调用 ragas.evaluate，没有时使用 aevaluate。"""
    evaluate_func = getattr(ragas_module, "evaluate", None)
    kwargs = {
        "dataset": dataset,
        "metrics": adapter.metrics,
        "llm": adapter.llm,
        "embeddings": adapter.embeddings,
        "run_config": run_config,
        "raise_exceptions": False,
        "show_progress": True,
    }
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
    run_config = _build_run_config(settings.timeout_seconds)
    result = _call_ragas_evaluate(ragas, dataset=dataset, adapter=adapter, run_config=run_config)
    summary = write_ragas_scores_csv(output_csv, result, ragas_rows)
    return RagasMetricsRunResult(
        summary=summary,
        ragas_version=ragas_version,
        model_adapter=adapter.adapter_name,
        metric_names=adapter.metric_names,
    )
