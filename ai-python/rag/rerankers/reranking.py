from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from app.schemas.rag import Evidence


DEFAULT_RERANK_BASE_URL = "https://dashscope.aliyuncs.com"
DEFAULT_RERANK_MODEL = "qwen3-rerank"
TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_+#.-]+")


@dataclass(frozen=True)
class RerankResult:
    evidences: list[Evidence]
    provider: str
    model: str
    fallback_reason: str | None = None

    def diagnostics(self) -> dict[str, str]:
        result = {
            "rerankProvider": self.provider,
            "rerankModel": self.model,
        }
        if self.fallback_reason:
            result["rerankFallbackReason"] = self.fallback_reason
        return result


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
        if self.provider == "local":
            return False
        if self.provider == "dashscope":
            return True
        return bool(self.api_key)

    def rerank(self, question: str, evidences: list[Evidence], top_k: int) -> RerankResult:
        if not evidences:
            return RerankResult([], "local", "deterministic-reranker")
        if not self.should_call_dashscope:
            return RerankResult(local_rerank(question, evidences, top_k), "local", "deterministic-reranker")
        if not self.api_key:
            return RerankResult(
                local_rerank(question, evidences, top_k),
                "local",
                "deterministic-reranker",
                "DASHSCOPE_API_KEY 未配置",
            )
        try:
            return RerankResult(self._call_rerank(question, evidences, top_k), "dashscope", self.model)
        except Exception as exc:
            return RerankResult(
                local_rerank(question, evidences, top_k),
                "local",
                "deterministic-reranker",
                f"百炼 rerank 失败: {exc}",
            )

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
            score = float(item.get("relevance_score") or item.get("score") or evidences[index].score)
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
    query_terms = set(tokenize(question))
    ranked = []
    for index, item in enumerate(evidences):
        document_terms = set(tokenize(render_document(item)))
        overlap = len(query_terms & document_terms) / max(len(query_terms), 1)
        score = item.score * 0.7 + overlap * 0.3 + 1.0 / (1000 + index)
        ranked.append(item.model_copy(update={"score": round(score, 6), "retrievalSource": "rerank"}))
    return sorted(ranked, key=lambda item: item.score, reverse=True)[:top_k]


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]
