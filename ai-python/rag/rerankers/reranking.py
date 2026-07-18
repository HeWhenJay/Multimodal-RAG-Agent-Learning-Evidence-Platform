from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Any

from app.schemas.rag import Evidence
from rag.observability.model_logging import log_model_call
from rag.observability.process_logger import logged_rag_method, process_event


DEFAULT_RERANK_BASE_URL = "https://dashscope.aliyuncs.com"
DEFAULT_RERANK_MODEL = "qwen3-rerank"
LOCAL_RERANK_MODEL = "deterministic-feature-reranker-v2"
TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_+#.-]+")


@dataclass(frozen=True)
class RerankResult:
    evidences: list[Evidence]
    provider: str
    model: str
    fallback_reason: str | None = None
    selection_reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def diagnostics(self) -> dict[str, Any]:
        """返回重排提供方、选择原因和可解释评分详情。"""
        result: dict[str, Any] = {
            "rerankProvider": self.provider,
            "rerankModel": self.model,
            **self.details,
        }
        if self.fallback_reason:
            result["rerankFallbackReason"] = self.fallback_reason
        if self.selection_reason:
            result["rerankSelectionReason"] = self.selection_reason
        return result


@dataclass(frozen=True)
class LocalRerankConfig:
    """保存本地确定性重排的归一化特征权重。"""

    fusion_weight: float
    lexical_weight: float
    title_weight: float
    rank_weight: float
    warnings: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "LocalRerankConfig":
        """读取本地重排权重，非法值或全零配置回退默认权重。"""
        defaults = (0.40, 0.35, 0.15, 0.10)
        warnings: list[str] = []
        raw_weights = (
            read_local_weight("RAG_LOCAL_RERANK_FUSION_WEIGHT", defaults[0], warnings),
            read_local_weight("RAG_LOCAL_RERANK_LEXICAL_WEIGHT", defaults[1], warnings),
            read_local_weight("RAG_LOCAL_RERANK_TITLE_WEIGHT", defaults[2], warnings),
            read_local_weight("RAG_LOCAL_RERANK_RANK_WEIGHT", defaults[3], warnings),
        )
        if sum(raw_weights) <= 0:
            warnings.append("本地重排权重全部为 0，已回退默认权重")
            raw_weights = defaults
        total = sum(raw_weights)
        normalized = tuple(weight / total for weight in raw_weights)
        return cls(*normalized, warnings=tuple(warnings))

    def diagnostics(self) -> dict[str, float]:
        """返回可写入响应诊断的归一化权重。"""
        return {
            "fusion": round(self.fusion_weight, 6),
            "lexical": round(self.lexical_weight, 6),
            "title": round(self.title_weight, 6),
            "rank": round(self.rank_weight, 6),
        }


@dataclass(frozen=True)
class LocalRerankOutcome:
    """保存本地重排结果和逐候选特征分解。"""

    evidences: list[Evidence]
    diagnostics: dict[str, Any]


class BailianRerankClient:
    """百炼文本重排客户端，失败时由调用方降级到本地重排。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.base_url = (base_url or os.getenv("RAG_RERANK_BASE_URL") or DEFAULT_RERANK_BASE_URL).rstrip("/")
        self.model = model or os.getenv("RAG_RERANK_MODEL") or DEFAULT_RERANK_MODEL
        self.provider = (provider or os.getenv("RAG_RERANK_PROVIDER") or "auto").strip().lower()
        self.timeout_seconds = timeout_seconds or float(os.getenv("RAG_RERANK_TIMEOUT_SECONDS", "30"))

    @property
    def should_call_dashscope(self) -> bool:
        """判断本次配置是否应调用百炼。"""
        if self.provider == "local":
            return False
        if self.provider == "dashscope":
            return True
        return self.provider == "auto" and bool(self.api_key)

    @logged_rag_method("query.rerank", "bailian_rerank", "执行百炼或本地重排")
    def rerank(self, question: str, evidences: list[Evidence], top_k: int) -> RerankResult:
        process_event(
            stage="query.rerank",
            action="rerank_select_provider",
            message="已选择 rerank 提供方",
            context={
                "provider": "dashscope" if self.should_call_dashscope else "local",
                "candidateCount": len(evidences),
                "topK": top_k,
            },
        )
        if not evidences:
            return self._local_result(
                question,
                evidences,
                top_k,
                selection_reason="候选 evidence 为空，无需调用百炼",
            )
        if self.provider not in {"auto", "local", "dashscope"}:
            return self._local_result(
                question,
                evidences,
                top_k,
                fallback_reason=f"未知 RAG_RERANK_PROVIDER: {self.provider}",
                selection_reason="未知 provider，已选择本地确定性重排",
            )
        if not self.should_call_dashscope:
            selection_reason = (
                "RAG_RERANK_PROVIDER=local，使用本地确定性重排"
                if self.provider == "local"
                else "RAG_RERANK_PROVIDER=auto 且 DASHSCOPE_API_KEY 未配置，使用本地确定性重排"
            )
            return self._local_result(question, evidences, top_k, selection_reason=selection_reason)
        if not self.api_key:
            return self._local_result(
                question,
                evidences,
                top_k,
                fallback_reason="DASHSCOPE_API_KEY 未配置",
                selection_reason="强制百炼但缺少 Key，已选择本地确定性重排",
            )
        try:
            ranked = self._call_rerank(question, evidences, top_k)
            return RerankResult(
                ranked,
                "dashscope",
                self.model,
                selection_reason=f"RAG_RERANK_PROVIDER={self.provider} 且 DASHSCOPE_API_KEY 可用",
                details={
                    "rerankInputCandidateCount": len(evidences),
                    "rerankedCandidateCount": len(ranked),
                },
            )
        except Exception as exc:
            return self._local_result(
                question,
                evidences,
                top_k,
                fallback_reason=f"百炼 rerank 失败: {exc}",
                selection_reason="百炼调用失败，已选择本地确定性重排",
            )

    def _local_result(
        self,
        question: str,
        evidences: list[Evidence],
        top_k: int,
        *,
        fallback_reason: str | None = None,
        selection_reason: str | None = None,
    ) -> RerankResult:
        """执行本地重排并统一封装 provider、原因和评分诊断。"""
        outcome = local_rerank_with_diagnostics(question, evidences, top_k)
        return RerankResult(
            evidences=outcome.evidences,
            provider="local",
            model=LOCAL_RERANK_MODEL,
            fallback_reason=fallback_reason,
            selection_reason=selection_reason,
            details=outcome.diagnostics,
        )

    @logged_rag_method("query.rerank", "bailian_rerank_call", "调用百炼 rerank 接口")
    def _call_rerank(self, question: str, evidences: list[Evidence], top_k: int) -> list[Evidence]:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("使用百炼 rerank 需要安装 httpx 依赖") from exc

        payload = {
            "model": self.model,
            "input": {
                "query": question,
                "documents": [render_document(item) for item in evidences],
            },
            "parameters": {
                "top_n": min(top_k, len(evidences)),
                "return_documents": False,
            },
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}/api/v1/services/rerank/text-rerank/text-rerank"
        with log_model_call(
            stage="query.rerank",
            action="bailian_rerank",
            model_name=self.model,
            event="候选 evidence 重排",
            extra_context={"candidateCount": len(evidences), "topK": top_k},
            recoverable=True,
            fallback_message=f"使用 {self.model} 模型完成候选 evidence 重排事件失败，已降级到本地关键词重排继续处理",
        ):
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        results = ((data.get("output") or {}).get("results") or data.get("results") or [])
        ranked: list[Evidence] = []
        for item in results:
            index = int(item.get("index", -1))
            if index < 0 or index >= len(evidences):
                continue
            raw_score = item.get("relevance_score")
            if raw_score is None:
                raw_score = item.get("score")
            if raw_score is None:
                raw_score = evidences[index].score
            score = float(raw_score)
            ranked.append(evidences[index].model_copy(update={"score": round(score, 6), "retrievalSource": "rerank"}))
        if not ranked:
            raise RuntimeError("百炼 rerank 响应没有有效结果")
        return ranked[:top_k]


def rerank_evidences(question: str, evidences: list[Evidence], top_k: int) -> RerankResult:
    """对 RAG-Fusion 候选证据做后检索重排。"""
    return BailianRerankClient().rerank(question, evidences, top_k)


def render_document(item: Evidence) -> str:
    location = item.sectionName or item.sectionTitle or "全文"
    if item.startTime:
        time_range = f"{item.startTime}-{item.endTime}" if item.endTime else item.startTime
        location = f"{location} 视频时间 {time_range}"
    return f"{item.title} {location}\n{item.snippet}"


def local_rerank(question: str, evidences: list[Evidence], top_k: int) -> list[Evidence]:
    """兼容旧调用方，返回本地确定性特征重排后的 evidence。"""
    return local_rerank_with_diagnostics(question, evidences, top_k).evidences


@logged_rag_method("query.rerank", "local_rerank", "执行本地可解释特征重排")
def local_rerank_with_diagnostics(
    question: str,
    evidences: list[Evidence],
    top_k: int,
    *,
    config: LocalRerankConfig | None = None,
) -> LocalRerankOutcome:
    """融合候选分、词覆盖、标题章节覆盖和排名先验进行本地重排。"""
    resolved_config = config or LocalRerankConfig.from_env()
    query_terms = set(tokenize(question))
    normalized_fusion_scores = normalize_fusion_scores([finite_score(item.score) for item in evidences])
    scored: list[tuple[Evidence, dict[str, Any], int]] = []

    for index, (item, normalized_fusion_score) in enumerate(zip(evidences, normalized_fusion_scores)):
        document_terms = set(tokenize(render_document(item)))
        title_terms = set(tokenize(render_title_location(item)))
        lexical_coverage = term_coverage(query_terms, document_terms)
        title_coverage = term_coverage(query_terms, title_terms)
        rank_prior = 1.0 / (index + 1)
        final_score = clamp01(
            normalized_fusion_score * resolved_config.fusion_weight
            + lexical_coverage * resolved_config.lexical_weight
            + title_coverage * resolved_config.title_weight
            + rank_prior * resolved_config.rank_weight
        )
        detail = {
            "evidenceId": item.evidenceId,
            "originalRank": index + 1,
            "inputScore": round(finite_score(item.score), 6),
            "normalizedFusionScore": round(normalized_fusion_score, 6),
            "lexicalCoverage": round(lexical_coverage, 6),
            "titleCoverage": round(title_coverage, 6),
            "rankPrior": round(rank_prior, 6),
            "finalScore": round(final_score, 6),
        }
        metadata = dict(item.metadata or {})
        metadata["localRerank"] = {
            "strategy": LOCAL_RERANK_MODEL,
            **detail,
        }
        ranked_item = item.model_copy(
            update={
                "score": round(final_score, 6),
                "retrievalSource": "rerank",
                "metadata": metadata,
            }
        )
        scored.append((ranked_item, detail, index))

    ranked = sorted(scored, key=lambda entry: (-entry[0].score, entry[2], entry[0].evidenceId))
    limit = max(0, min(int(top_k or 0), len(ranked)))
    selected = ranked[:limit]
    candidate_details = [
        {**detail, "finalRank": final_rank}
        for final_rank, (_item, detail, _original_index) in enumerate(selected, start=1)
    ]
    diagnostics: dict[str, Any] = {
        "rerankInputCandidateCount": len(evidences),
        "rerankedCandidateCount": len(selected),
        "localRerankStrategy": LOCAL_RERANK_MODEL,
        "localRerankWeights": resolved_config.diagnostics(),
        "localRerankCandidateDetails": candidate_details,
    }
    if resolved_config.warnings:
        diagnostics["localRerankConfigurationWarnings"] = list(resolved_config.warnings)
    return LocalRerankOutcome(
        evidences=[item for item, _detail, _index in selected],
        diagnostics=diagnostics,
    )


def normalize_fusion_scores(scores: list[float]) -> list[float]:
    """归一化融合候选分；同分或单候选保留其可解释绝对值。"""
    if not scores:
        return []
    minimum = min(scores)
    maximum = max(scores)
    if maximum > minimum:
        return [(score - minimum) / (maximum - minimum) for score in scores]
    return [clamp01(score) for score in scores]


def render_title_location(item: Evidence) -> str:
    """拼接标题和章节位置，供标题章节覆盖特征使用。"""
    return " ".join(
        value
        for value in (item.documentTitle, item.title, item.sectionName, item.sectionTitle)
        if value
    )


def term_coverage(query_terms: set[str], candidate_terms: set[str]) -> float:
    """计算问题词在候选字段中的覆盖率。"""
    if not query_terms or not candidate_terms:
        return 0.0
    return len(query_terms & candidate_terms) / len(query_terms)


def read_local_weight(name: str, default: float, warnings: list[str]) -> float:
    """读取本地重排非负权重，非法值回退默认值。"""
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = float(raw_value)
    except ValueError:
        warnings.append(f"{name} 不是有效数字，已使用默认值 {default}")
        return default
    if not math.isfinite(value) or value < 0 or value > 10:
        warnings.append(f"{name} 超出范围 0-10，已使用默认值 {default}")
        return default
    return value


def finite_score(value: Any) -> float:
    """把候选分安全转换为有限浮点数。"""
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    return score if math.isfinite(score) else 0.0


def clamp01(value: float) -> float:
    """把本地重排分限制在 0 到 1。"""
    return max(0.0, min(1.0, float(value)))


def tokenize(text: str) -> list[str]:
    """按中文单字和英文技术词切分本地重排文本。"""
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]
