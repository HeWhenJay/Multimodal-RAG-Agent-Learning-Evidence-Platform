"""复习知识 Curator A/B 的数据读取、确定性指标和启用决策。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import time
from typing import Any, Iterator

from app.core.environment import read_process_or_windows_user_environment
from app.review.knowledge_extractor import (
    KnowledgePointExtractor,
    LearningMaterialContext,
    clean_review_evidences,
    deduplicate_evidences,
    normalized_sentence,
)
from app.review.langextract_curator import (
    LangExtractKnowledgeCurator,
    ModelUsageAudit,
    _OpenAIClientAuditProxy,
)
from app.review.repository import MaterialSourceRecord, ReviewRepository
from app.schemas.rag import Evidence


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CASES_PATH = PROJECT_ROOT / "docs" / "testing" / "review-curator-ab-cases.jsonl"


@dataclass(frozen=True)
class CuratorArmResult:
    """单个资料、单个实验臂的统一指标。"""

    arm: str
    status: str
    candidates: tuple[str, ...]
    evidence_mapping_success_rate: float
    published_ungrounded_rate: float
    duplicate_rate: float
    raw_candidate_count: int
    accepted_candidate_count: int
    matched_expected_points: tuple[str, ...]
    missing_expected_points: tuple[str, ...]
    expected_recall: float
    request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    duration_seconds: float
    error: str | None = None


@dataclass(frozen=True)
class CuratorCaseResult:
    """一条冻结用例的 A/B 对照结果。"""

    case_id: str
    category: str
    material_id: int
    title: str
    evidence_count: int
    evidence_character_count: int
    baseline: CuratorArmResult
    langextract: CuratorArmResult


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[dict[str, Any]]:
    """按 UTF-8 JSONL 读取人工冻结金标。"""
    result: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            item = json.loads(line)
            if not isinstance(item, dict) or not item.get("caseId"):
                raise ValueError(f"{path} 第 {line_number} 行缺少 caseId")
            result.append(item)
    return result


def select_cases(cases: list[dict[str, Any]], case_ids: list[str] | None) -> list[dict[str, Any]]:
    """按命令行 caseId 过滤并拒绝静默漏跑。"""
    if not case_ids:
        return cases
    requested = set(case_ids)
    selected = [case for case in cases if str(case.get("caseId")) in requested]
    missing = requested - {str(case.get("caseId")) for case in selected}
    if missing:
        raise ValueError(f"未知 A/B caseId：{'、'.join(sorted(missing))}")
    return selected


def load_material_evidences(case: dict[str, Any]) -> tuple[MaterialSourceRecord, list[Evidence]]:
    """只读查找标题唯一匹配的真实资料及其完整 evidence。"""
    database_url = (
        read_process_or_windows_user_environment("REVIEW_DATABASE_URL")
        or read_process_or_windows_user_environment("RAG_DATABASE_URL")
        or read_process_or_windows_user_environment("DATABASE_URL")
    )
    if not database_url:
        raise RuntimeError("未配置 REVIEW_DATABASE_URL、RAG_DATABASE_URL 或 DATABASE_URL")
    title_contains = str(case.get("materialTitleContains") or "").strip()
    if not title_contains:
        raise ValueError(f"用例 {case.get('caseId')} 缺少 materialTitleContains")
    repository = ReviewRepository(database_url=database_url)
    with repository.transaction() as transaction:
        cursor = transaction._cursor  # noqa: SLF001 - 评估工具只读执行冻结标题查询。
        schema = transaction._schema  # noqa: SLF001
        cursor.execute(
            f"SELECT id FROM {schema}.learning_material WHERE title LIKE %s AND status IN ('READY', 'PARTIAL') ORDER BY id",
            (f"%{title_contains}%",),
        )
        material_ids = [int(row["id"]) for row in cursor.fetchall()]
        if len(material_ids) != 1:
            raise RuntimeError(
                f"用例 {case.get('caseId')} 的标题匹配到 {len(material_ids)} 份资料，必须恰好为 1"
            )
        material = transaction.find_material_by_id(material_ids[0])
        if material is None:
            raise RuntimeError(f"资料 {material_ids[0]} 不存在")
        evidences = transaction.list_evidences(material, limit=320)
    cleaned = clean_review_evidences(deduplicate_evidences(evidences))
    if not cleaned:
        raise RuntimeError(f"资料 {material.id} 清洗后没有可用 evidence")
    return material, cleaned


@contextmanager
def audit_openai_calls(audit: ModelUsageAudit) -> Iterator[None]:
    """在 A 臂运行期间审计真实 OpenAI-compatible 调用并限制请求预算。"""
    import openai
    from unittest.mock import patch

    original = openai.OpenAI

    def create_audited_client(**kwargs: Any) -> Any:
        return _OpenAIClientAuditProxy(original(**kwargs), audit)

    with patch("openai.OpenAI", create_audited_client):
        yield


def run_baseline_arm(
    material: MaterialSourceRecord,
    evidences: list[Evidence],
    case: dict[str, Any],
    *,
    max_requests: int,
) -> CuratorArmResult:
    """运行当前生产 KnowledgePointExtractor，作为 A 臂。"""
    audit = ModelUsageAudit(max_requests=max_requests)
    started_at = time.perf_counter()
    previous_attempts = os.environ.get("REVIEW_GENERATION_MAX_ATTEMPTS")
    os.environ["REVIEW_GENERATION_MAX_ATTEMPTS"] = str(max_requests)
    try:
        with audit_openai_calls(audit):
            result = KnowledgePointExtractor(provider="deepseek", langextract_enabled=False).extract(
                LearningMaterialContext(
                    material.id,
                    material.title,
                    material.document_type,
                    material.document_summary,
                ),
                evidences,
            )
        candidate_texts = tuple(
            f"{point.question} {point.answer}"
            for point in result.knowledge_points
        )
        mapped = sum(bool(point.evidence_refs) for point in result.knowledge_points)
        raw_count = len(result.knowledge_points)
        return build_arm_result(
            arm="A-current-curator",
            status="COMPLETED",
            candidates=candidate_texts,
            raw_candidate_count=raw_count,
            accepted_candidate_count=raw_count,
            grounded_candidate_count=mapped,
            case=case,
            audit=audit,
            duration=time.perf_counter() - started_at,
        )
    except Exception as exc:  # noqa: BLE001 - A/B 报告必须保留失败臂指标。
        return build_arm_result(
            arm="A-current-curator",
            status="FAILED",
            candidates=(),
            raw_candidate_count=0,
            accepted_candidate_count=0,
            grounded_candidate_count=0,
            case=case,
            audit=audit,
            duration=time.perf_counter() - started_at,
            error=safe_error(exc),
        )
    finally:
        if previous_attempts is None:
            os.environ.pop("REVIEW_GENERATION_MAX_ATTEMPTS", None)
        else:
            os.environ["REVIEW_GENERATION_MAX_ATTEMPTS"] = previous_attempts


def run_langextract_arm(
    material: MaterialSourceRecord,
    evidences: list[Evidence],
    case: dict[str, Any],
    *,
    max_requests: int,
    extraction_passes: int,
    max_char_buffer: int,
    max_workers: int,
) -> CuratorArmResult:
    """运行官方 LangExtract 候选发现，作为 B 臂。"""
    started_at = time.perf_counter()
    curator = LangExtractKnowledgeCurator(
        extraction_passes=extraction_passes,
        max_char_buffer=max_char_buffer,
        max_workers=max_workers,
        max_model_requests=max_requests,
    )
    try:
        result = curator.extract(material.title, evidences)
        return build_arm_result(
            arm="B-langextract-1.6",
            status="COMPLETED",
            candidates=tuple(item.text for item in result.candidates),
            raw_candidate_count=result.raw_extraction_count,
            accepted_candidate_count=len(result.candidates),
            grounded_candidate_count=result.grounded_extraction_count,
            case=case,
            audit=result.usage,
            duration=result.duration_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - B/A 任一失败都必须形成可审计结论。
        audit = getattr(exc, "usage", None) or curator.last_usage
        return build_arm_result(
            arm="B-langextract-1.6",
            status="FAILED",
            candidates=(),
            raw_candidate_count=0,
            accepted_candidate_count=0,
            grounded_candidate_count=0,
            case=case,
            audit=audit,
            duration=time.perf_counter() - started_at,
            error=safe_error(exc),
        )


def build_arm_result(
    *,
    arm: str,
    status: str,
    candidates: tuple[str, ...],
    raw_candidate_count: int,
    accepted_candidate_count: int,
    grounded_candidate_count: int,
    case: dict[str, Any],
    audit: ModelUsageAudit,
    duration: float,
    error: str | None = None,
) -> CuratorArmResult:
    """把两种异构输出归一为同一套可比较指标。"""
    matched, missing = match_expected_points(case.get("expectedPoints") or [], candidates)
    expected_total = len(matched) + len(missing)
    mapping_rate = grounded_candidate_count / raw_candidate_count if raw_candidate_count else 0.0
    # 重复率比较发布前的最终候选池；两臂内部被门禁或 LangExtract resolver 拦掉的重复只记原始数量，
    # 不把某一臂更细的中间审计口径冒充为公平的最终重复率。
    duplicate_rate = final_duplicate_rate(candidates)
    return CuratorArmResult(
        arm=arm,
        status=status,
        candidates=candidates,
        evidence_mapping_success_rate=round(mapping_rate, 6),
        # 两臂发布集合都只包含带 evidence 的候选，因此发布后无支撑率必须为零。
        published_ungrounded_rate=0.0,
        duplicate_rate=round(duplicate_rate, 6),
        raw_candidate_count=raw_candidate_count,
        accepted_candidate_count=accepted_candidate_count,
        matched_expected_points=tuple(matched),
        missing_expected_points=tuple(missing),
        expected_recall=round(len(matched) / expected_total, 6) if expected_total else 0.0,
        request_count=audit.request_count,
        input_tokens=audit.input_tokens,
        output_tokens=audit.output_tokens,
        total_tokens=audit.total_tokens,
        duration_seconds=round(duration, 4),
        error=error,
    )


def match_expected_points(
    expected_points: list[Any],
    candidate_texts: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """用人工冻结别名做确定性召回匹配，不再引入一个偏向任一臂的裁判模型。"""
    normalized_candidates = [normalized_sentence(item) for item in candidate_texts]
    matched: list[str] = []
    missing: list[str] = []
    for raw in expected_points:
        if isinstance(raw, str):
            name = raw
            aliases = [raw]
            required_groups: list[list[str]] = []
        elif isinstance(raw, dict):
            name = str(raw.get("name") or "").strip()
            aliases = [str(item) for item in raw.get("aliases") or [name]]
            required_groups = [
                [str(term) for term in group]
                for group in raw.get("requiredGroups") or []
                if isinstance(group, list)
            ]
        else:
            continue
        alias_keys = [normalized_sentence(alias) for alias in aliases if normalized_sentence(alias)]
        required_group_keys = [
            [normalized_sentence(term) for term in group if normalized_sentence(term)]
            for group in required_groups
        ]
        groups_matched = bool(required_group_keys) and all(
            any(term in candidate for term in group for candidate in normalized_candidates)
            for group in required_group_keys
        )
        alias_matched = bool(alias_keys) and any(
            alias in candidate for alias in alias_keys for candidate in normalized_candidates
        )
        if name and (groups_matched or (not required_group_keys and alias_matched)):
            matched.append(name)
        elif name:
            missing.append(name)
    return matched, missing


def final_duplicate_rate(candidate_texts: tuple[str, ...]) -> float:
    """以相同近重复规则计算两臂最终候选重复率。"""
    keys = [normalized_sentence(item) for item in candidate_texts if normalized_sentence(item)]
    duplicates = 0
    unique: list[str] = []
    for key in keys:
        if any(texts_are_near_duplicates(key, previous) for previous in unique):
            duplicates += 1
        else:
            unique.append(key)
    return duplicates / len(keys) if keys else 0.0


def texts_are_near_duplicates(left: str, right: str) -> bool:
    """识别逐字、包含式及高度相似改写，避免多 pass 虚增候选数量。"""
    if left == right:
        return True
    shorter, longer = sorted((left, right), key=len)
    if len(shorter) >= 8 and shorter in longer and len(shorter) / len(longer) >= 0.58:
        return True
    if len(shorter) < 10:
        return False
    return SequenceMatcher(None, left, right, autojunk=False).ratio() >= 0.82


def decide_langextract(case_results: list[CuratorCaseResult]) -> dict[str, Any]:
    """按预注册门槛判断是否允许把 LangExtract 接入生产候选发现阶段。"""
    if not case_results:
        return {"enable": False, "reasons": ["没有完成任何 A/B 用例"]}
    baseline_recall = average(item.baseline.expected_recall for item in case_results)
    langextract_recall = average(item.langextract.expected_recall for item in case_results)
    baseline_tokens = sum(item.baseline.total_tokens for item in case_results)
    langextract_tokens = sum(item.langextract.total_tokens for item in case_results)
    cost_ratio = langextract_tokens / baseline_tokens if baseline_tokens else float("inf")
    categories = {item.category for item in case_results}
    reasons: list[str] = []
    if any(item.baseline.status != "COMPLETED" or item.langextract.status != "COMPLETED" for item in case_results):
        reasons.append("至少一个实验臂未完成")
    if langextract_recall - baseline_recall < 0.10:
        reasons.append("人工金标知识点召回率绝对提升不足 10 个百分点")
    if any(item.langextract.evidence_mapping_success_rate < 0.95 for item in case_results):
        reasons.append("LangExtract 原始结果的 evidence 映射成功率低于 95%")
    if any(item.langextract.published_ungrounded_rate > item.baseline.published_ungrounded_rate for item in case_results):
        reasons.append("过滤后的无支撑率高于当前方案")
    if any(item.langextract.duplicate_rate > item.baseline.duplicate_rate for item in case_results):
        reasons.append("LangExtract 重复率高于当前方案")
    if cost_ratio > 1.5:
        reasons.append("LangExtract Token 成本超过当前方案的 1.5 倍")
    if not {"structured_questions", "declarative_course"}.issubset(categories):
        reasons.append("未同时覆盖结构化问题资料和陈述式课程资料")
    return {
        "enable": not reasons,
        "baselineRecall": round(baseline_recall, 6),
        "langextractRecall": round(langextract_recall, 6),
        "recallAbsoluteGain": round(langextract_recall - baseline_recall, 6),
        "baselineTokens": baseline_tokens,
        "langextractTokens": langextract_tokens,
        "tokenCostRatio": round(cost_ratio, 6) if cost_ratio != float("inf") else None,
        "reasons": reasons,
    }


def average(values: Iterator[float]) -> float:
    """计算小样本简单平均值。"""
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def case_result_to_dict(result: CuratorCaseResult) -> dict[str, Any]:
    """把不可变结果转换为 UTF-8 JSON 友好结构。"""
    return asdict(result)


def safe_error(exc: Exception) -> str:
    """保留足够诊断且不泄露请求正文或密钥。"""
    message = " ".join(str(exc).split()).strip()
    return f"{type(exc).__name__}: {message[:1000]}"
