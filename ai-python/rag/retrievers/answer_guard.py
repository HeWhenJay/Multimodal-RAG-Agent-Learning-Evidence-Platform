from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.schemas.rag import Evidence


AnswerStatus = Literal["ANSWERED", "REFUSED"]
RefusalReason = Literal[
    "NO_EVIDENCE",
    "FILTERED_OUT",
    "LOW_CONFIDENCE",
    "INSUFFICIENT_COVERAGE",
    "WEAK_SNIPPET",
    "ONLY_DIAGNOSTIC_CANDIDATES",
    "UNKNOWN",
]

REFUSAL_POLICY = "STRICT_EVIDENCE_GUARD_V1"
TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_+#.-]+")
TIMESTAMP_PATTERN = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?\b")


@dataclass(frozen=True)
class AnswerGuardDecision:
    answerStatus: AnswerStatus
    refusalReason: RefusalReason | None
    refusalPolicy: str
    confidence: float
    supportingEvidenceIds: list[str]
    candidateEvidenceSummaries: list[dict[str, Any]]
    thresholds: dict[str, Any]
    signals: dict[str, Any]
    message: str

    def diagnostics(self) -> dict[str, Any]:
        """返回可写入 diagnostics.answerGuard 的结构化判定结果。"""
        return {
            "answerStatus": self.answerStatus,
            "refusalReason": self.refusalReason,
            "refusalPolicy": self.refusalPolicy,
            "confidence": self.confidence,
            "supportingEvidenceIds": self.supportingEvidenceIds,
            "candidateEvidenceSummaries": self.candidateEvidenceSummaries,
            "thresholds": self.thresholds,
            "signals": self.signals,
            "message": self.message,
        }


def evaluate_answer_guard(
    *,
    question: str,
    expanded_queries: list[str],
    evidences: list[Evidence],
    diagnostics: dict[str, Any],
) -> AnswerGuardDecision:
    """在回答生成前执行准入判定，避免弱 evidence 触发确定性回答。"""
    try:
        return _evaluate_answer_guard(
            question=question,
            expanded_queries=expanded_queries,
            evidences=evidences,
            diagnostics=diagnostics,
        )
    except Exception as exc:
        thresholds = guard_thresholds(diagnostics)
        message = refusal_message("UNKNOWN")
        return AnswerGuardDecision(
            answerStatus="REFUSED",
            refusalReason="UNKNOWN",
            refusalPolicy=REFUSAL_POLICY,
            confidence=0.0,
            supportingEvidenceIds=[],
            candidateEvidenceSummaries=candidate_summaries(evidences),
            thresholds=thresholds,
            signals={"answerableScore": 0.0, "guardError": str(exc)},
            message=message,
        )


def _evaluate_answer_guard(
    *,
    question: str,
    expanded_queries: list[str],
    evidences: list[Evidence],
    diagnostics: dict[str, Any],
) -> AnswerGuardDecision:
    """执行可解释评分和拒答原因选择。"""
    thresholds = guard_thresholds(diagnostics)
    summaries = candidate_summaries(evidences)
    total_candidate_chunks = int_value(diagnostics.get("totalCandidateChunkCount"))
    filtered_chunks = int_value(diagnostics.get("filteredChunkCount"))

    if total_candidate_chunks <= 0:
        return refused("NO_EVIDENCE", thresholds, summaries, evidence_count=len(evidences))
    if filtered_chunks <= 0:
        return refused("FILTERED_OUT", thresholds, summaries, evidence_count=len(evidences))
    if not evidences:
        return refused("LOW_CONFIDENCE", thresholds, summaries, evidence_count=0)

    provider = str(diagnostics.get("rerankProvider") or "local").lower()
    score_signals = score_signals_for(evidences, provider=provider)
    keyword_coverage = keyword_coverage_signal(question, expanded_queries, evidences)
    snippet_qualities = [snippet_quality_signal(item.snippet) for item in evidences]
    top_snippet_quality = snippet_qualities[0] if snippet_qualities else 0.0
    diagnostic_flags = [is_diagnostic_only_candidate(item) for item in evidences]
    all_diagnostic = bool(evidences) and all(diagnostic_flags)
    supporting = [
        item
        for item, quality, diagnostic_only in zip(evidences, snippet_qualities, diagnostic_flags)
        if quality >= 0.35 and not diagnostic_only
    ]
    evidence_count_signal = min(len(supporting) / max(int(thresholds["minSupportingEvidenceCount"]), 1), 1.0)
    risk_penalty = risk_penalty_signal(diagnostics, evidences=evidences, all_diagnostic=all_diagnostic)
    answerable_score = clamp01(
        score_signals["normalizedTopScore"] * 0.4
        + keyword_coverage * 0.25
        + evidence_count_signal * 0.2
        + top_snippet_quality * 0.15
        - risk_penalty
    )
    signals = {
        **score_signals,
        "keywordCoverage": round(keyword_coverage, 6),
        "evidenceCountSignal": round(evidence_count_signal, 6),
        "snippetQualitySignal": round(top_snippet_quality, 6),
        "riskPenalty": round(risk_penalty, 6),
        "answerableScore": round(answerable_score, 6),
        "supportingEvidenceCount": len(supporting),
        "candidateCount": len(evidences),
    }

    if all_diagnostic:
        return refused("ONLY_DIAGNOSTIC_CANDIDATES", thresholds, summaries, signals=signals)
    if top_snippet_quality < 0.35:
        return refused("WEAK_SNIPPET", thresholds, summaries, signals=signals)
    top_evidence = evidences[0]
    if score_signals["topScoreForThreshold"] < float(thresholds["minTopScore"]) and not local_score_is_covered_by_strong_context(
        score_signals=score_signals,
        keyword_coverage=keyword_coverage,
        top_snippet_quality=top_snippet_quality,
        thresholds=thresholds,
        top_evidence=top_evidence,
    ):
        return refused("LOW_CONFIDENCE", thresholds, summaries, signals=signals)
    if bool(thresholds["strictMode"]) and keyword_coverage < float(thresholds["minKeywordCoverage"]):
        return refused("INSUFFICIENT_COVERAGE", thresholds, summaries, signals=signals)
    if len(supporting) < int(thresholds["minSupportingEvidenceCount"]):
        return refused("INSUFFICIENT_COVERAGE", thresholds, summaries, signals=signals)
    if answerable_score < float(thresholds["minAnswerableScore"]):
        return refused("LOW_CONFIDENCE", thresholds, summaries, signals=signals)

    supporting_ids = [item.evidenceId for item in supporting]
    return AnswerGuardDecision(
        answerStatus="ANSWERED",
        refusalReason=None,
        refusalPolicy=REFUSAL_POLICY,
        confidence=round(answerable_score, 6),
        supportingEvidenceIds=supporting_ids,
        candidateEvidenceSummaries=summaries,
        thresholds=thresholds,
        signals=signals,
        message=f"回答准入通过：可回答分 {answerable_score:.4f}，支持证据 {len(supporting_ids)} 条。",
    )


def guard_thresholds(diagnostics: dict[str, Any]) -> dict[str, Any]:
    """读取可校准阈值，并按 rerank provider 选择 Top 分门槛。"""
    provider = str(diagnostics.get("rerankProvider") or "local").lower()
    min_top_env = "RAG_ANSWER_MIN_TOP_SCORE_DASHSCOPE" if provider == "dashscope" else "RAG_ANSWER_MIN_TOP_SCORE_LOCAL"
    min_top_default = "0.55" if provider == "dashscope" else "0.25"
    return {
        "minAnswerableScore": float(os.getenv("RAG_ANSWER_MIN_ANSWERABLE_SCORE", "0.45")),
        "minTopScore": float(os.getenv(min_top_env, min_top_default)),
        "minKeywordCoverage": float(os.getenv("RAG_ANSWER_MIN_KEYWORD_COVERAGE", "0.08")),
        "minSupportingEvidenceCount": max(1, int(os.getenv("RAG_ANSWER_MIN_SUPPORTING_EVIDENCE_COUNT", "1"))),
        "strictMode": os.getenv("RAG_ANSWER_STRICT_MODE", "true").strip().lower() not in {"0", "false", "no", "off"},
        "rerankProvider": provider,
        "policy": REFUSAL_POLICY,
    }


def score_signals_for(evidences: list[Evidence], *, provider: str) -> dict[str, float]:
    """归一化候选分数，同时保留本地 fallback 的原始 Top 分用于门槛判断。"""
    scores = [float(item.score or 0.0) for item in evidences]
    raw_top = scores[0] if scores else 0.0
    if provider == "dashscope":
        normalized_scores = [clamp01(score) for score in scores]
    else:
        min_score = min(scores) if scores else 0.0
        max_score = max(scores) if scores else 0.0
        if max_score > min_score:
            normalized_scores = [(score - min_score) / (max_score - min_score) for score in scores]
        elif scores:
            # 本地 rerank 分数不是统一置信度；单候选或同分候选无法做 min-max 区分时，
            # 由原始 Top 分门槛和关键词覆盖继续约束是否可回答。
            normalized_scores = [1.0 for _ in scores]
        else:
            normalized_scores = []
    normalized_top = normalized_scores[0] if normalized_scores else 0.0
    top_for_threshold = clamp01(raw_top) if provider != "dashscope" else normalized_top
    return {
        "rawTopScore": round(raw_top, 6),
        "normalizedTopScore": round(clamp01(normalized_top), 6),
        "topScoreForThreshold": round(clamp01(top_for_threshold), 6),
    }


def keyword_coverage_signal(question: str, expanded_queries: list[str], evidences: list[Evidence]) -> float:
    """计算问题关键词和扩展查询关键词在候选 evidence 中的覆盖率。"""
    query_tokens = set(tokenize(" ".join([question, *expanded_queries])))
    if not query_tokens:
        return 0.0
    evidence_text = " ".join(
        f"{item.title} {item.sectionName} {item.sectionTitle or ''} {item.snippet}"
        for item in evidences
    )
    evidence_tokens = set(tokenize(evidence_text))
    if not evidence_tokens:
        return 0.0
    return len(query_tokens & evidence_tokens) / len(query_tokens)


def snippet_quality_signal(snippet: str | None) -> float:
    """判断 snippet 是否具备直接回答价值。"""
    text = " ".join(str(snippet or "").split())
    if not text:
        return 0.0
    stripped = re.sub(r"[#*\-`>|\s.。·…:：,，;；/\\()\[\]{}]+", "", text)
    if not stripped:
        return 0.0
    if stripped in {"目录", "索引", "标题", "时间戳"}:
        return 0.0
    timestamp_chars = sum(len(match.group(0)) for match in TIMESTAMP_PATTERN.finditer(text))
    timestamp_ratio = timestamp_chars / max(len(text), 1)
    quality = min(len(stripped) / 80, 1.0)
    if len(stripped) < 12:
        quality *= 0.35
    if timestamp_ratio > 0.45:
        quality *= 0.35
    if looks_like_table_of_contents(text):
        quality *= 0.3
    return round(clamp01(quality), 6)


def looks_like_table_of_contents(text: str) -> bool:
    """识别目录、页码和标题列表等弱上下文。"""
    compact = text.strip()
    if "目录" in compact and len(compact) < 80:
        return True
    numbered = re.findall(r"(?:^|\s)\d+(?:\.\d+)*\s*[\u4e00-\u9fffA-Za-z]", compact)
    dot_leaders = compact.count("...")
    return len(numbered) >= 4 or dot_leaders >= 3


def is_diagnostic_only_candidate(evidence: Evidence) -> bool:
    """识别 summary child、重复视频帧和其它不应进入顶层 evidence 的诊断候选。"""
    metadata = evidence.metadata or {}
    child_kind = str(metadata.get("childKind") or "")
    retrieval_layer = str(metadata.get("retrievalLayer") or "")
    channel = str(metadata.get("evidenceChannel") or "")
    matched_child_kinds = metadata.get("matchedChildKinds")
    if retrieval_layer == "parent_aggregated" and isinstance(matched_child_kinds, list):
        if any(str(item) not in {"summary", "video_segment_summary"} for item in matched_child_kinds):
            return False
    if child_kind in {"summary", "video_segment_summary"}:
        return True
    if "-summary-" in evidence.evidenceId:
        return True
    if retrieval_layer == "child" and child_kind == "summary":
        return True
    if isinstance(matched_child_kinds, list) and matched_child_kinds and all(str(item) in {"summary", "video_segment_summary"} for item in matched_child_kinds):
        return True
    if channel == "frame_ocr" and (metadata.get("duplicateGroupId") or metadata.get("visualDecision") == "duplicate"):
        return True
    return False


def local_score_is_covered_by_strong_context(
    *,
    score_signals: dict[str, float],
    keyword_coverage: float,
    top_snippet_quality: float,
    thresholds: dict[str, Any],
    top_evidence: Evidence,
) -> bool:
    """本地 rerank 低量纲分接近门槛时，用覆盖率和片段质量做保守校准。"""
    if str(thresholds.get("rerankProvider") or "local").lower() != "local":
        return False
    raw_top = float(score_signals.get("rawTopScore") or 0.0)
    min_top = float(thresholds["minTopScore"])
    if is_media_locator_evidence(top_evidence):
        return raw_top >= min_top * 0.84 and keyword_coverage >= 0.12 and top_snippet_quality >= 0.5
    return raw_top >= min_top * 0.8 and keyword_coverage >= 0.18 and top_snippet_quality >= 0.7


def is_media_locator_evidence(evidence: Evidence) -> bool:
    """识别带时间定位的视频字幕、OCR 或父段 evidence。"""
    metadata = evidence.metadata or {}
    if evidence.startTime or evidence.endTime:
        return True
    return bool(metadata.get("mediaType") == "video" or metadata.get("evidenceChannel") in {"subtitle", "frame_ocr"})


def risk_penalty_signal(diagnostics: dict[str, Any], *, evidences: list[Evidence], all_diagnostic: bool) -> float:
    """汇总本地 fallback、重复候选和诊断候选带来的风险扣分。"""
    penalty = 0.0
    if str(diagnostics.get("rerankProvider") or "local").lower() == "local":
        penalty += 0.08
    if diagnostics.get("rerankFallbackReason"):
        penalty += 0.05
    if os.getenv("RAG_EMBEDDING_PROVIDER", "").strip().lower() == "hash":
        penalty += 0.05
    if all_diagnostic:
        penalty += 0.25
    candidate_count = max(len(evidences), 1)
    dedup_removed = int_value(diagnostics.get("dedupRemovedCount"))
    if dedup_removed / candidate_count >= 0.5:
        penalty += 0.05
    return round(min(penalty, 0.45), 6)


def candidate_summaries(evidences: list[Evidence]) -> list[dict[str, Any]]:
    """把弱候选压缩成诊断摘要，避免把完整 snippet 作为顶层 evidence 暴露。"""
    summaries: list[dict[str, Any]] = []
    for item in evidences[:8]:
        summaries.append(
            {
                "evidenceId": item.evidenceId,
                "documentId": item.documentId,
                "title": item.title,
                "sectionName": item.sectionName,
                "score": item.score,
                "retrievalSource": item.retrievalSource,
                "diagnosticOnly": is_diagnostic_only_candidate(item),
                "snippetPreview": truncate(" ".join((item.snippet or "").split()), 120),
            }
        )
    return summaries


def refused(
    reason: RefusalReason,
    thresholds: dict[str, Any],
    summaries: list[dict[str, Any]],
    *,
    evidence_count: int | None = None,
    signals: dict[str, Any] | None = None,
) -> AnswerGuardDecision:
    """构造统一拒答决策。"""
    final_signals = signals or {
        "answerableScore": 0.0,
        "supportingEvidenceCount": 0,
        "candidateCount": evidence_count or 0,
    }
    confidence = round(float(final_signals.get("answerableScore") or 0.0), 6)
    return AnswerGuardDecision(
        answerStatus="REFUSED",
        refusalReason=reason,
        refusalPolicy=REFUSAL_POLICY,
        confidence=confidence,
        supportingEvidenceIds=[],
        candidateEvidenceSummaries=summaries,
        thresholds=thresholds,
        signals=final_signals,
        message=refusal_message(reason),
    )


def refusal_message(reason: RefusalReason) -> str:
    """生成面向用户的中文拒答正文。"""
    if reason == "NO_EVIDENCE":
        return "当前知识库还没有可检索的学习资料，无法基于个人资料回答该问题。请先上传或索引相关资料。"
    if reason == "FILTERED_OUT":
        return "当前筛选条件下没有可用证据，无法基于个人资料回答该问题。请调整筛选条件或补充相关资料。"
    if reason == "WEAK_SNIPPET":
        return "检索到的片段过短或缺少可回答内容，无法基于个人资料给出可靠回答。请补充更完整的学习资料。"
    if reason == "ONLY_DIAGNOSTIC_CANDIDATES":
        return "当前只命中摘要或诊断候选，缺少可直接引用的原始证据，无法生成可靠回答。"
    if reason == "INSUFFICIENT_COVERAGE":
        return "当前证据覆盖不足，无法完整支撑该问题的回答。请补充更相关的资料后重试。"
    if reason == "LOW_CONFIDENCE":
        return "当前知识库没有检索到足够相关的证据，无法基于个人资料回答该问题。请补充相关学习资料后重试。"
    return "回答准入检查出现可恢复异常，系统已保守拒答，避免基于不可靠证据生成回答。"


def refusal_short_message(reason: RefusalReason | None) -> str | None:
    """生成前端标签和历史列表使用的短拒答文案。"""
    if reason is None:
        return None
    return {
        "NO_EVIDENCE": "知识库暂无证据",
        "FILTERED_OUT": "筛选后无证据",
        "LOW_CONFIDENCE": "证据相关性不足",
        "INSUFFICIENT_COVERAGE": "证据覆盖不足",
        "WEAK_SNIPPET": "证据片段过弱",
        "ONLY_DIAGNOSTIC_CANDIDATES": "仅命中诊断候选",
        "UNKNOWN": "准入检查异常",
    }.get(reason, "证据不足，已拒答")


def tokenize(text: str) -> list[str]:
    """按中文单字和英文技术词切分检索覆盖关键词。"""
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


def int_value(value: Any) -> int:
    """将诊断字段安全转换为整数。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def clamp01(value: float) -> float:
    """把分数约束在 0 到 1 区间。"""
    return max(0.0, min(1.0, float(value)))


def truncate(value: str, max_length: int) -> str:
    """生成固定长度的诊断预览文本。"""
    return value if len(value) <= max_length else value[:max_length].rstrip() + "..."
