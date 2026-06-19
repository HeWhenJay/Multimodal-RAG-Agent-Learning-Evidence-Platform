from __future__ import annotations

import csv
import importlib
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


def write_run_config(path: Path, *, mode: str, summary: dict[str, Any], ragas_version: str | None = None) -> None:
    """写出本次评估运行配置。"""
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
            "RAGAS_EVAL_PROVIDER": os.getenv("RAGAS_EVAL_PROVIDER"),
            "RAGAS_EVAL_BASE_URL": os.getenv("RAGAS_EVAL_BASE_URL"),
            "RAGAS_EVAL_LLM_MODEL": os.getenv("RAGAS_EVAL_LLM_MODEL"),
            "RAGAS_EVAL_EMBEDDING_MODEL": os.getenv("RAGAS_EVAL_EMBEDDING_MODEL"),
            "RAGAS_EVAL_TIMEOUT_SECONDS": os.getenv("RAGAS_EVAL_TIMEOUT_SECONDS"),
            "RAGAS_EVAL_TEMPERATURE": os.getenv("RAGAS_EVAL_TEMPERATURE", "0"),
        },
        "summary": summary,
    }
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def write_manual_review(path: Path, rows: list[dict[str, Any]], summary: dict[str, Any], ragas_version: str | None = None) -> None:
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
        "## 样本明细",
        "",
        "| 用例 | 类型 | 通过 | top3 | 引用 | evidence | 缺失关键点 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
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


def ensure_ragas_eval_config() -> None:
    """校验真实 Ragas 评分所需的评估模型环境变量。"""
    if not os.getenv("RAGAS_EVAL_API_KEY"):
        raise RuntimeError("RAGAS_EVAL_API_KEY 未配置，请先配置评估模型 Key，或改用 --mode offline。")
    if not os.getenv("RAGAS_EVAL_LLM_MODEL"):
        raise RuntimeError("RAGAS_EVAL_LLM_MODEL 未配置，请指定 Ragas 评估 LLM 模型。")
    if not os.getenv("RAGAS_EVAL_EMBEDDING_MODEL"):
        raise RuntimeError("RAGAS_EVAL_EMBEDDING_MODEL 未配置，请指定 Ragas 评估 embedding 模型。")


def build_ragas_eval_models() -> tuple[Any, Any]:
    """按 RAGAS_EVAL_* 环境变量创建 Ragas 评估模型。"""
    try:
        from openai import AsyncOpenAI
        from ragas.embeddings import OpenAIEmbeddings
        from ragas.llms import llm_factory
    except ImportError as exc:
        raise RuntimeError("运行 Ragas 真实评分需要安装 openai 与 ragas 依赖。") from exc

    api_key = os.getenv("RAGAS_EVAL_API_KEY")
    base_url = os.getenv("RAGAS_EVAL_BASE_URL") or None
    timeout = float(os.getenv("RAGAS_EVAL_TIMEOUT_SECONDS", "60"))
    llm_model = os.getenv("RAGAS_EVAL_LLM_MODEL")
    embedding_model = os.getenv("RAGAS_EVAL_EMBEDDING_MODEL")
    client_kwargs = {"api_key": api_key, "timeout": timeout}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = AsyncOpenAI(**client_kwargs)
    llm = llm_factory(llm_model, client=client)
    embeddings = OpenAIEmbeddings(client=client, model=embedding_model)
    return llm, embeddings


def build_ragas_metrics(llm: Any, embeddings: Any) -> list[Any]:
    """集中创建 Ragas 指标，兼容 ragas>=0.4,<0.5 的常见导入路径。"""
    try:
        collections = importlib.import_module("ragas.metrics.collections")
        return [
            collections.ContextPrecision(llm=llm),
            collections.ContextRecall(llm=llm),
            collections.Faithfulness(llm=llm),
            collections.ResponseRelevancy(llm=llm, embeddings=embeddings),
        ]
    except Exception:
        pass

    metrics_module = importlib.import_module("ragas.metrics")
    result = []
    context_precision_class = getattr(metrics_module, "LLMContextPrecisionWithReference", None) or getattr(
        metrics_module, "LLMContextPrecisionWithoutReference", None
    )
    context_recall_class = getattr(metrics_module, "LLMContextRecall", None) or getattr(metrics_module, "ContextRecall", None)
    faithfulness_class = getattr(metrics_module, "Faithfulness", None)
    response_relevancy_class = getattr(metrics_module, "ResponseRelevancy", None) or getattr(metrics_module, "AnswerRelevancy", None)
    metric_classes = [context_precision_class, context_recall_class, faithfulness_class, response_relevancy_class]
    for metric_class in metric_classes:
        if metric_class is None:
            continue
        try:
            metric = metric_class(llm=llm, embeddings=embeddings)
        except TypeError:
            try:
                metric = metric_class(llm=llm)
            except TypeError:
                metric = metric_class()
        result.append(metric)
    if len(result) < 4:
        raise RuntimeError("当前 Ragas 版本无法找到 Context Precision、Context Recall、Faithfulness、Response Relevancy 指标。")
    return result


def run_ragas_metrics(ragas_rows: list[dict[str, Any]], output_csv: Path) -> dict[str, Any]:
    """运行真实 Ragas LLM 指标，并把结果写出为 CSV。"""
    ensure_ragas_eval_config()
    try:
        from datasets import Dataset
    except ImportError as exc:
        raise RuntimeError("运行 Ragas 真实评分需要安装 datasets 依赖，通常会随 ragas 安装。") from exc

    ragas = importlib.import_module("ragas")
    llm, embeddings = build_ragas_eval_models()
    metrics = build_ragas_metrics(llm, embeddings)
    dataset_rows = [
        {
            "user_input": row["user_input"],
            "response": row["response"],
            "retrieved_contexts": row["retrieved_contexts"],
            "reference": row["reference"],
        }
        for row in ragas_rows
    ]
    dataset = Dataset.from_list(dataset_rows)
    evaluate_func = getattr(ragas, "evaluate", None)
    if evaluate_func is None:
        raise RuntimeError("当前 Ragas 版本没有 evaluate 入口，请在兼容层中补充 aevaluate 调用。")
    run_config = None
    try:
        from ragas.run_config import RunConfig

        run_config = RunConfig(timeout=float(os.getenv("RAGAS_EVAL_TIMEOUT_SECONDS", "60")))
    except Exception:
        run_config = None
    result = evaluate_func(
        dataset=dataset,
        metrics=metrics,
        llm=llm,
        embeddings=embeddings,
        run_config=run_config,
        raise_exceptions=False,
        show_progress=True,
    )
    frame = result.to_pandas()
    frame.insert(0, "case_id", [row["case_id"] for row in ragas_rows])
    frame.to_csv(output_csv, index=False, encoding="utf-8-sig")
    summary: dict[str, Any] = {}
    for column in frame.columns:
        if column == "case_id":
            continue
        values = [float(value) for value in frame[column].tolist() if isinstance(value, (int, float)) and not math.isnan(float(value))]
        if values:
            summary[column] = round(sum(values) / len(values), 4)
    return summary
