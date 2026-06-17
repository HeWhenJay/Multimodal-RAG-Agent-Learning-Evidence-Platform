from __future__ import annotations

import hashlib
import math
import os
import re
from urllib.parse import quote, urlencode
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Any

from rag.bailian_llm import generate_grounded_answer
from rag.chunking import RecursiveChunker
from rag.models import Chunk, utc_now_iso
from rag.parse_quality import QualitySignals, evaluate_parse_quality
from rag.reranking import rerank_evidences
from rag.summary_index import SummaryIndex
from schemas.rag import (
    DocumentBlock,
    Evidence,
    IndexResponse,
    IndexTextRequest,
    OverviewResponse,
    ParseQuality,
    QueryRequest,
    QueryResponse,
)


TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_+#.-]+")
DEFAULT_EMBEDDING_DIMENSIONS = 1024
DEFAULT_DASHSCOPE_EMBEDDING_MODEL = "text-embedding-v4"
DEFAULT_DASHSCOPE_EMBEDDING_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class InMemoryRagStore:
    def __init__(self) -> None:
        self.chunker = RecursiveChunker()
        self.summary_index = SummaryIndex()
        self.documents: dict[str, dict] = {}
        self.chunks: dict[str, Chunk] = {}
        self.term_freqs: dict[str, Counter[str]] = {}
        self.doc_freq: Counter[str] = Counter()
        self.embeddings: dict[str, list[float]] = {}

    def index_text(self, request: IndexTextRequest) -> IndexResponse:
        block = DocumentBlock(
            documentId=request.documentId,
            blockId=f"{request.documentId}-manual-1",
            fileType=request.documentType,
            blockType="text",
            sectionTitle="全文",
            contentText=request.content,
            parseEngine=request.parser,
            confidence=1.0,
            sourceTitle=request.title,
            sourcePath=request.sourcePath,
            metadata={"source": "manual-text"},
        )
        quality = evaluate_parse_quality(
            QualitySignals(native_text_chars=len(request.content), paragraph_count=1),
            high_precision=False,
        )
        return self.index_blocks(
            document_id=request.documentId,
            title=request.title,
            document_type=request.documentType,
            source=request.source,
            user_id=request.userId,
            visibility_scope=request.visibilityScope,
            language=request.language,
            parser=request.parser,
            blocks=[block],
            parse_quality=quality,
            status="READY",
            source_path=request.sourcePath,
        )

    def index_blocks(
        self,
        *,
        document_id: str,
        title: str,
        document_type: str,
        source: str,
        user_id: str,
        visibility_scope: str,
        language: str,
        parser: str,
        blocks: list[DocumentBlock],
        parse_quality: ParseQuality,
        status: str,
        source_path: str | None = None,
    ) -> IndexResponse:
        metadata = {
            "documentId": document_id,
            "title": title,
            "documentType": document_type,
            "source": source,
            "sourcePath": source_path,
            "userId": user_id,
            "visibilityScope": visibility_scope,
            "uploadTime": utc_now_iso(),
            "language": language,
            "parser": parser,
            "parseStatus": status,
            "parseQuality": parse_quality.model_dump(),
        }
        self._remove_document(document_id)
        chunks = self.chunker.split_blocks(blocks, document_id=document_id, metadata=metadata)
        summaries = self.summary_index.build(chunks)
        for chunk in chunks:
            self.chunks[chunk.chunk_id] = chunk
            tokens = tokenize(chunk.text)
            token_counts = Counter(tokens)
            self.term_freqs[chunk.chunk_id] = token_counts
            self.doc_freq.update(set(token_counts))
            self.embeddings[chunk.chunk_id] = embed_text(chunk.text)

        self.documents[document_id] = {
            **metadata,
            "chunkCount": len(chunks),
            "summaries": summaries,
        }
        return IndexResponse(
            documentId=document_id,
            title=title,
            status=status if chunks else "FAILED",
            chunkCount=len(chunks),
            parser=parser,
            documentSummary=summaries["documentSummary"],
            parseQuality=parse_quality,
        )

    def query(self, request: QueryRequest) -> QueryResponse:
        expanded_queries = expand_queries(request.question)
        filtered_chunks = self._filter_chunks(request.metadataFilter or {})
        ranked_lists: list[list[tuple[str, float]]] = []
        diagnostics: dict[str, int | list[str]] = {
            "expandedQueries": expanded_queries,
            "filteredChunkCount": len(filtered_chunks),
        }

        for query_text in expanded_queries:
            ranked_lists.append(self._bm25_search(query_text, filtered_chunks, limit=max(request.topK * 3, 10)))
            ranked_lists.append(self._vector_search(query_text, filtered_chunks, limit=max(request.topK * 3, 10)))

        fused = reciprocal_rank_fusion(ranked_lists)
        candidates = fused[: max(request.topK * 3, request.topK)]
        candidate_evidences = [
            self._to_evidence(chunk_id, score, retrieval_source="fusion")
            for chunk_id, score in candidates
        ]
        reranked = rerank_evidences(request.question, candidate_evidences, request.topK)
        diagnostics.update(reranked.diagnostics())
        generated = generate_grounded_answer(request.question, reranked.evidences)
        diagnostics.update(generated.diagnostics())
        return QueryResponse(
            answer=generated.answer,
            expandedQueries=expanded_queries,
            evidences=reranked.evidences,
            diagnostics=diagnostics,
        )

    def overview(self) -> OverviewResponse:
        last_title = None
        if self.documents:
            last_doc = list(self.documents.values())[-1]
            last_title = last_doc.get("title")
        return OverviewResponse(
            documentCount=len(self.documents),
            chunkCount=len(self.chunks),
            evidenceCount=len(self.chunks),
            lastIndexedTitle=last_title,
        )

    def list_evidences(self, document_id: str, limit: int = 20) -> list[Evidence]:
        chunk_ids = [
            chunk.chunk_id
            for chunk in sorted(
                self.chunks.values(),
                key=lambda item: int(item.metadata.get("chunkPosition") or 0),
            )
            if chunk.document_id == document_id
        ]
        return [self._to_evidence(chunk_id, 1.0, retrieval_source="summary") for chunk_id in chunk_ids[:limit]]

    def _remove_document(self, document_id: str) -> None:
        old_chunk_ids = [chunk_id for chunk_id, chunk in self.chunks.items() if chunk.document_id == document_id]
        for chunk_id in old_chunk_ids:
            old_terms = self.term_freqs.pop(chunk_id, Counter())
            for term in set(old_terms):
                self.doc_freq[term] -= 1
                if self.doc_freq[term] <= 0:
                    del self.doc_freq[term]
            self.chunks.pop(chunk_id, None)
            self.embeddings.pop(chunk_id, None)
        self.documents.pop(document_id, None)

    def _filter_chunks(self, metadata_filter: dict) -> list[Chunk]:
        if not metadata_filter:
            return list(self.chunks.values())

        result = []
        for chunk in self.chunks.values():
            metadata = chunk.metadata
            matched = True
            for key, value in metadata_filter.items():
                if value is None or value == "":
                    continue
                if isinstance(value, list):
                    if metadata.get(key) not in value:
                        matched = False
                        break
                elif metadata.get(key) != value:
                    matched = False
                    break
            if matched:
                result.append(chunk)
        return result

    def _bm25_search(self, query_text: str, chunks: list[Chunk], limit: int) -> list[tuple[str, float]]:
        query_terms = tokenize(query_text)
        if not query_terms:
            return []
        avgdl = sum(sum(self.term_freqs.get(chunk.chunk_id, Counter()).values()) for chunk in chunks) / max(len(chunks), 1)
        k1 = 1.5
        b = 0.75
        scores: list[tuple[str, float]] = []
        total_docs = max(len(self.chunks), 1)
        for chunk in chunks:
            tf = self.term_freqs.get(chunk.chunk_id, Counter())
            doc_len = sum(tf.values()) or 1
            score = 0.0
            for term in query_terms:
                freq = tf.get(term, 0)
                if freq == 0:
                    continue
                df = self.doc_freq.get(term, 0)
                idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
                score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * doc_len / max(avgdl, 1)))
            if score > 0:
                scores.append((chunk.chunk_id, score))
        return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]

    def _vector_search(self, query_text: str, chunks: list[Chunk], limit: int) -> list[tuple[str, float]]:
        query_vector = embed_text(query_text)
        scores = []
        for chunk in chunks:
            score = cosine_similarity(query_vector, self.embeddings.get(chunk.chunk_id, []))
            if score > 0:
                scores.append((chunk.chunk_id, score))
        return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]

    def _to_evidence(self, chunk_id: str, score: float, retrieval_source: str) -> Evidence:
        chunk = self.chunks[chunk_id]
        metadata = chunk.metadata
        snippet = " ".join(chunk.text.split())
        if len(snippet) > 220:
            snippet = snippet[:220].rstrip() + "..."
        title = str(metadata.get("title") or metadata.get("sourceTitle") or "未命名资料")
        section_title = str(metadata.get("sectionTitle") or metadata.get("sectionName") or "全文")
        return Evidence(
            evidenceId=chunk.chunk_id,
            documentId=chunk.document_id,
            documentTitle=title,
            blockId=as_optional_str(metadata.get("blockId")),
            blockType=as_optional_str(metadata.get("blockType")),
            pageIndex=as_optional_int(metadata.get("pageIndex")),
            slideIndex=as_optional_int(metadata.get("slideIndex")),
            startTime=as_optional_str(metadata.get("startTime")),
            endTime=as_optional_str(metadata.get("endTime")),
            sheetName=as_optional_str(metadata.get("sheetName")),
            cellRange=as_optional_str(metadata.get("cellRange")),
            sectionTitle=section_title,
            title=title,
            snippet=snippet,
            source=str(metadata.get("source") or "unknown"),
            sourcePath=as_optional_str(metadata.get("sourcePath")),
            assetPath=as_optional_str(metadata.get("assetPath")),
            playbackUrl=build_playback_url(
                document_id=chunk.document_id,
                title=title,
                metadata=metadata,
            ),
            sectionName=section_title,
            documentType=str(metadata.get("documentType") or "document"),
            score=round(score, 6),
            retrievalSource=retrieval_source,  # type: ignore[arg-type]
            parseEngine=as_optional_str(metadata.get("parseEngine") or metadata.get("parser")),
            metadata=metadata.get("blockMetadata") if isinstance(metadata.get("blockMetadata"), dict) else {},
        )


def as_optional_str(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def as_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def build_playback_url(*, document_id: str, title: str, metadata: dict[str, Any]) -> str | None:
    """根据视频 evidence 元数据生成播放定位链接。"""
    start_time = as_optional_str(metadata.get("startTime"))
    if not start_time:
        return None
    start_seconds = timestamp_to_seconds(start_time)
    media_url = first_present(metadata, "playbackUrl", "videoUrl", "mediaUrl", "sourceVideoUrl")
    if not media_url:
        source_path = as_optional_str(metadata.get("sourcePath"))
        if source_path and is_video_url(source_path):
            media_url = source_path
    if media_url:
        base_url = media_url.split("#", 1)[0]
        return f"{base_url}#t={start_seconds}"
    params = {
        "documentId": document_id,
        "title": title,
        "startTime": start_time,
    }
    end_time = as_optional_str(metadata.get("endTime"))
    source_path = as_optional_str(metadata.get("sourcePath"))
    if end_time:
        params["endTime"] = end_time
    if source_path:
        params["sourcePath"] = source_path
    return f"/videos?{urlencode(params, quote_via=quote)}"


def is_video_url(value: str) -> bool:
    return bool(re.match(r"^https?://.+\.(mp4|mov|m4v|webm|mkv|avi)(\?.*)?$", value, re.IGNORECASE))


def first_present(metadata: dict[str, Any], *keys: str) -> str | None:
    """从元数据中读取第一个非空字符串。"""
    for key in keys:
        value = as_optional_str(metadata.get(key))
        if value:
            return value
    return None


def timestamp_to_seconds(value: str) -> int:
    """将 HH:MM:SS 或 MM:SS 时间戳转为秒数。"""
    parts = [int(part) for part in value.replace(",", ".").split(".", 1)[0].split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    if len(parts) >= 3:
        hours, minutes, seconds = parts[-3:]
        return hours * 3600 + minutes * 60 + seconds
    return 0


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


def embed_text(text: str, dimensions: int | None = None) -> list[float]:
    """生成文本向量，生产环境优先使用百炼 embedding，离线测试可显式使用 hash。"""
    target_dimensions = dimensions or embedding_dimensions()
    provider = embedding_provider_name()
    cache_key = (provider, embedding_model_name(), target_dimensions, text)
    return list(cached_embedding(cache_key))


@lru_cache(maxsize=4096)
def cached_embedding(cache_key: tuple[str, str, int, str]) -> tuple[float, ...]:
    provider, model, dimensions, text = cache_key
    if provider == "hash":
        return tuple(hash_embed_text(text, dimensions))
    if provider != "dashscope":
        raise RuntimeError(f"不支持的 RAG_EMBEDDING_PROVIDER: {provider}")
    return tuple(dashscope_embed_text(text, model=model, dimensions=dimensions))


def embedding_dimensions() -> int:
    """读取当前 RAG 向量维度，默认与 text-embedding-v4 对齐为 1024。"""
    return int(os.getenv("RAG_VECTOR_DIMENSIONS", str(DEFAULT_EMBEDDING_DIMENSIONS)))


def embedding_model_name() -> str:
    """读取百炼 embedding 模型名。"""
    return os.getenv("RAG_EMBEDDING_MODEL") or os.getenv("DASHSCOPE_EMBEDDING_MODEL") or DEFAULT_DASHSCOPE_EMBEDDING_MODEL


def embedding_provider_name() -> str:
    """选择 embedding 提供方；生产默认走百炼，离线测试需显式设置 hash。"""
    configured = os.getenv("RAG_EMBEDDING_PROVIDER")
    if configured:
        return configured.strip().lower()
    return "dashscope"


def dashscope_embed_text(text: str, *, model: str, dimensions: int) -> list[float]:
    """通过百炼 OpenAI 兼容接口生成真实 embedding。"""
    api_key = os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError("使用百炼 embedding 需要配置 DASHSCOPE_API_KEY")
    base_url = (
        os.getenv("RAG_EMBEDDING_BASE_URL")
        or os.getenv("DASHSCOPE_EMBEDDING_BASE_URL")
        or DEFAULT_DASHSCOPE_EMBEDDING_BASE_URL
    ).rstrip("/")
    timeout = float(os.getenv("RAG_EMBEDDING_TIMEOUT_SECONDS", "30"))
    try:
        import httpx
    except ImportError as exc:
        raise RuntimeError("使用百炼 embedding 需要安装 httpx 依赖") from exc

    payload: dict[str, Any] = {
        "model": model,
        "input": text,
        "dimensions": dimensions,
        "encoding_format": "float",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{base_url}/embeddings", headers=headers, json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"百炼 embedding 调用失败: HTTP {response.status_code} {response.text[:500]}")
    data = response.json()
    try:
        embedding = data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("百炼 embedding 响应结构不符合预期") from exc
    if not isinstance(embedding, list) or len(embedding) != dimensions:
        actual = len(embedding) if isinstance(embedding, list) else "非数组"
        raise RuntimeError(f"百炼 embedding 维度不符合预期: expected={dimensions}, actual={actual}")
    return [float(value) for value in embedding]


def hash_embed_text(text: str, dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS) -> list[float]:
    """离线测试使用的确定性 hash embedding，不作为生产默认模型。"""
    vector = [0.0] * dimensions
    tokens = tokenize(text)
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


def expand_queries(question: str) -> list[str]:
    base = question.strip()
    variants = [
        base,
        f"{base} 关键证据",
        f"{base} 学习资料 笔记",
    ]
    if any(term in base.lower() for term in ["jd", "岗位", "招聘", "能力"]):
        variants.append(f"{base} 岗位要求 能力缺口")
    if any(term in base.lower() for term in ["简历", "resume", "项目"]):
        variants.append(f"{base} 简历证据 项目经历")
    return list(dict.fromkeys(variants))


def reciprocal_rank_fusion(ranked_lists: list[list[tuple[str, float]]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, (chunk_id, _score) in enumerate(ranked, start=1):
            scores[chunk_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def build_answer(question: str, evidences: list[Evidence]) -> str:
    if not evidences:
        return "当前知识库没有检索到足够相关的证据，请先上传或索引学习资料。"
    top = evidences[:3]
    evidence_text = "；".join(f"{item.title} / {item.sectionName}" for item in top)
    video_evidences = [
        item
        for item in top
        if item.startTime
    ]
    video_text = ""
    if video_evidences:
        locations = "；".join(
            f"{item.title} {item.startTime}-{item.endTime}" if item.endTime else f"{item.title} {item.startTime}"
            for item in video_evidences
        )
        video_text = f"视频证据：{locations}，可在证据卡片点击“从这里播放”定位。"
    return (
        f"针对“{question}”，已从个人学习证据库检索到 {len(evidences)} 条相关证据。"
        f"{video_text}"
        f"优先参考：{evidence_text}。建议基于这些资料整理回答，并在正式输出中保留证据引用。"
    )


def create_rag_store():
    database_url = os.getenv("RAG_DATABASE_URL") or os.getenv("DATABASE_URL")
    backend = os.getenv("RAG_STORE_BACKEND")
    if backend is None:
        backend = "pgvector" if database_url else "memory"

    backend = backend.lower()
    if backend == "memory":
        return InMemoryRagStore()
    if backend in {"pgvector", "postgres", "postgresql"}:
        if not database_url:
            raise RuntimeError("RAG_STORE_BACKEND=pgvector 时必须配置 RAG_DATABASE_URL 或 DATABASE_URL")
        from rag.pgvector_store import PgVectorRagStore

        return PgVectorRagStore(database_url=database_url)
    raise RuntimeError(f"不支持的 RAG_STORE_BACKEND: {backend}")
