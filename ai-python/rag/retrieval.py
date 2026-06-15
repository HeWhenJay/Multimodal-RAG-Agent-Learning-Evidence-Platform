from __future__ import annotations

import hashlib
import math
import re
from collections import Counter, defaultdict

from rag.chunking import RecursiveChunker
from rag.models import Chunk, utc_now_iso
from rag.summary_index import SummaryIndex
from schemas.rag import (
    Evidence,
    IndexResponse,
    IndexTextRequest,
    OverviewResponse,
    QueryRequest,
    QueryResponse,
)


TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_+#.-]+")


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
        document_id = request.documentId
        metadata = {
            "documentId": document_id,
            "title": request.title,
            "documentType": request.documentType,
            "source": request.source,
            "userId": request.userId,
            "visibilityScope": request.visibilityScope,
            "uploadTime": utc_now_iso(),
            "language": request.language,
            "parser": request.parser,
        }
        self._remove_document(document_id)
        chunks = self.chunker.split(request.content, document_id=document_id, metadata=metadata)
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
            title=request.title,
            status="INDEXED",
            chunkCount=len(chunks),
            parser=request.parser,
            documentSummary=summaries["documentSummary"],
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
        selected = fused[: request.topK]
        evidences = [self._to_evidence(chunk_id, score) for chunk_id, score in selected]
        answer = build_answer(request.question, evidences)
        return QueryResponse(
            answer=answer,
            expandedQueries=expanded_queries,
            evidences=evidences,
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

    def _to_evidence(self, chunk_id: str, score: float) -> Evidence:
        chunk = self.chunks[chunk_id]
        metadata = chunk.metadata
        snippet = " ".join(chunk.text.split())
        if len(snippet) > 220:
            snippet = snippet[:220].rstrip() + "..."
        return Evidence(
            evidenceId=chunk.chunk_id,
            documentId=chunk.document_id,
            title=str(metadata.get("title") or "未命名资料"),
            snippet=snippet,
            source=str(metadata.get("source") or "unknown"),
            sectionName=str(metadata.get("sectionName") or "全文"),
            documentType=str(metadata.get("documentType") or "document"),
            score=round(score, 6),
        )


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


def embed_text(text: str, dimensions: int = 128) -> list[float]:
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
    return sum(a * b for a, b in zip(left, right))


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
    return (
        f"针对“{question}”，已从个人学习证据库检索到 {len(evidences)} 条相关证据。"
        f"优先参考：{evidence_text}。建议基于这些资料整理回答，并在正式输出中保留证据引用。"
    )

