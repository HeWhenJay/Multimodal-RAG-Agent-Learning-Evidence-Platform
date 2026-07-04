from __future__ import annotations

import hashlib
import math
import os
import re
from urllib.parse import quote, urlencode
from collections import Counter, defaultdict
from functools import lru_cache
from typing import Any

from rag.generation.bailian_llm import generate_grounded_answer
from rag.chunkers.chunking import RecursiveChunker
from rag.core.models import Chunk, utc_now_iso
from rag.loaders.parse_quality import QualitySignals, evaluate_parse_quality
from rag.core.metadata_filters import (
    MetadataFilterPlan,
    build_metadata_filter_plan,
    format_metadata_filter_plan,
    matches_metadata_filter,
)
from rag.observability.model_logging import log_model_call
from rag.observability.process_logger import logged_rag_method, process_event
from rag.observability.progress import RagProgressReporter
from rag.rerankers.reranking import rerank_evidences
from rag.retrievers.answer_guard import evaluate_answer_guard, refusal_short_message
from rag.retrievers.evidence_diversity import build_evidence_metadata_view, dedupe_evidences_for_context
from rag.retrievers.parent_aggregation import ParentAggregationChunk, aggregate_parent_evidences
from rag.retrievers.query_expansion import (
    expand_queries,
    expand_queries_with_diagnostics,
    format_query_expansion_detail,
    format_query_variants,
)
from rag.indexes.summary_index import SummaryIndex
from rag.core.text_sanitizer import (
    clean_postgres_text,
    sanitize_chunks,
    sanitize_document_blocks,
    sanitize_for_postgres,
    sanitize_parse_quality,
)
from app.schemas.rag import (
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

    @logged_rag_method("index.text", "memory_index_text", "内存模式索引文本资料")
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

    @logged_rag_method("index.blocks", "memory_index_blocks", "内存模式写入解析块索引")
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
        progress_reporter: RagProgressReporter | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> IndexResponse:
        document_id = clean_postgres_text(document_id)
        title = clean_postgres_text(title)
        document_type = clean_postgres_text(document_type)
        source = clean_postgres_text(source)
        user_id = clean_postgres_text(user_id)
        visibility_scope = clean_postgres_text(visibility_scope)
        language = clean_postgres_text(language)
        parser = clean_postgres_text(parser)
        status = clean_postgres_text(status)
        source_path = clean_postgres_text(source_path) if source_path else source_path
        if progress_reporter:
            progress_reporter.emit("sanitize.blocks", "正在清洗解析块正文和元数据", current_step=4, total_steps=8, percent=28)
        parse_quality = sanitize_parse_quality(parse_quality)
        blocks = sanitize_document_blocks(blocks)
        metadata_parse_quality = parse_quality.model_copy(update={"messages": []}).model_dump()
        metadata_parse_quality["messageCount"] = len(parse_quality.messages)
        metadata = sanitize_for_postgres({
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
            "parseQuality": metadata_parse_quality,
        })
        if extra_metadata:
            metadata.update(sanitize_for_postgres(extra_metadata))
        self._remove_document(document_id)
        if progress_reporter:
            progress_reporter.emit("chunk.recursive", "正在按标题、段落、句子和长度预算执行递归切块", current_step=5, total_steps=8, percent=32)
        chunks = sanitize_chunks(self.chunker.split_blocks(blocks, document_id=document_id, metadata=metadata))
        if progress_reporter:
            progress_reporter.emit(
                "chunk.recursive",
                f"当前文件被切分为 {len(chunks)} 块",
                status="COMPLETED",
                current_step=5,
                total_steps=8,
                current_chunk=0,
                total_chunks=len(chunks),
                percent=38,
            )
            progress_reporter.emit("summary.index", "正在生成文档摘要和章节摘要索引", current_step=6, total_steps=8, percent=42)
        summaries = sanitize_for_postgres(self.summary_index.build(chunks))
        summary_chunks = sanitize_chunks(
            self.summary_index.build_parent_summary_chunks(
                chunks,
                document_id=document_id,
                start_position=len(chunks),
            )
        )
        chunks = [*chunks, *summary_chunks]
        process_event(
            stage="index.blocks",
            action="memory_index_blocks_chunked",
            message=f"内存模式准备写入 {len(chunks)} 个切块",
            context={"chunkCount": len(chunks), "summaryChildCount": len(summary_chunks), "parser": parser, "status": status},
        )
        total_chunks = len(chunks)
        for index, chunk in enumerate(chunks, start=1):
            process_event(
                stage="embedding.chunk",
                action="memory_embedding_chunk",
                message=f"第 {index}/{total_chunks} 块：生成 embedding",
                context={
                    "chunkIndex": index,
                    "totalChunks": total_chunks,
                    "chunkId": chunk.chunk_id,
                    "documentId": document_id,
                },
            )
            if progress_reporter:
                progress_reporter.emit(
                    "embedding.chunk",
                    f"第 {index}/{total_chunks} 块：目前在使用 {embedding_model_name()} 模型完成切块向量生成事件",
                    current_step=7,
                    total_steps=8,
                    current_chunk=index,
                    total_chunks=total_chunks,
                    chunk_id=chunk.chunk_id,
                    percent=chunk_percent(index, total_chunks, 45, 86),
                    detail=f"目前在使用 {embedding_model_name()} 模型完成切块向量生成事件",
                )
            self.chunks[chunk.chunk_id] = chunk
            tokens = tokenize(chunk.text)
            token_counts = Counter(tokens)
            self.term_freqs[chunk.chunk_id] = token_counts
            self.doc_freq.update(set(token_counts))
            self.embeddings[chunk.chunk_id] = embed_text(chunk.text)
            process_event(
                stage="memory.upsert.chunk",
                action="memory_upsert_chunk",
                message=f"第 {index}/{total_chunks} 块：写入内存检索索引",
                context={
                    "chunkIndex": index,
                    "totalChunks": total_chunks,
                    "chunkId": chunk.chunk_id,
                    "tokenCount": sum(token_counts.values()),
                    "documentId": document_id,
                },
            )
            if progress_reporter:
                progress_reporter.emit(
                    "memory.upsert.chunk",
                    f"第 {index}/{total_chunks} 块：写入内存检索索引",
                    current_step=8,
                    total_steps=8,
                    current_chunk=index,
                    total_chunks=total_chunks,
                    chunk_id=chunk.chunk_id,
                    percent=chunk_percent(index, total_chunks, 48, 92),
                )

        self.documents[document_id] = {
            **metadata,
            "chunkCount": len(chunks),
            "summaries": summaries,
        }
        final_status = status if chunks else "FAILED"
        if progress_reporter:
            progress_reporter.emit(
                "index.completed" if chunks else "index.failed",
                f"索引完成：状态 {final_status}，共 {len(chunks)} 个切块",
                status="COMPLETED" if chunks else "FAILED",
                current_step=8,
                total_steps=8,
                current_chunk=len(chunks),
                total_chunks=len(chunks),
                percent=100 if chunks else 0,
                parser=parser,
                extra_context={"parseStatus": final_status, "chunkCount": len(chunks), "parser": parser},
            )
        return IndexResponse(
            documentId=document_id,
            title=title,
            status=final_status,
            chunkCount=len(chunks),
            parser=parser,
            documentSummary=summaries["documentSummary"],
            parseQuality=parse_quality,
            progressEvents=progress_reporter.events if progress_reporter else [],
        )

    @logged_rag_method("query.pipeline", "memory_query", "内存模式执行 RAG 检索问答")
    def query(self, request: QueryRequest, progress_reporter: RagProgressReporter | None = None) -> QueryResponse:
        """执行 RAG 查询；任务接口可注入 reporter 实时读取阶段事件。"""
        progress_reporter = progress_reporter or RagProgressReporter(document_id="query", persist=False)
        progress_reporter.emit("query.expand", "正在生成 Multi-Query 查询变体", current_step=1, total_steps=8, percent=8)
        query_expansion = expand_queries_with_diagnostics(request.question)
        expanded_queries = query_expansion.queries
        progress_reporter.emit(
            "query.expand",
            f"Multi-Query 已生成 {len(expanded_queries)} 个查询变体，生成方式：{query_expansion.provider}",
            status="COMPLETED",
            current_step=1,
            total_steps=8,
            percent=14,
            detail=format_query_expansion_detail(query_expansion),
        )
        filter_plan = build_metadata_filter_plan(request.metadataFilter)
        progress_reporter.emit("query.filter", "正在按元数据过滤候选切块", current_step=2, total_steps=8, percent=18)
        scoped_chunks = self._filter_chunks(filter_plan.system_filter)
        filtered_chunks = [
            chunk
            for chunk in scoped_chunks
            if matches_metadata_filter(chunk.metadata, MetadataFilterPlan(business_filter=filter_plan.business_filter))
        ]
        progress_reporter.emit(
            "query.filter",
            f"元数据过滤完成：保留 {len(filtered_chunks)} 个候选切块",
            status="COMPLETED",
            current_step=2,
            total_steps=8,
            percent=24,
            detail=format_metadata_filter_plan(filter_plan, total_count=len(scoped_chunks), filtered_count=len(filtered_chunks)),
        )
        ranked_lists: list[list[tuple[str, float]]] = []
        diagnostics: dict[str, Any] = {
            "expandedQueries": expanded_queries,
            **query_expansion.diagnostics(),
            "totalCandidateChunkCount": len(scoped_chunks),
            "filteredChunkCount": len(filtered_chunks),
            **filter_plan.diagnostics(),
        }

        candidate_budget = max(request.topK * request.candidateMultiplier, 20)
        for query_text in expanded_queries:
            progress_reporter.emit("query.bm25", f"BM25 召回：{query_text}", current_step=3, total_steps=8, percent=30)
            bm25_hits = self._bm25_search(query_text, filtered_chunks, limit=candidate_budget)
            ranked_lists.append(bm25_hits)
            progress_reporter.emit(
                "query.bm25",
                f"BM25 召回完成：{query_text}，命中 {len(bm25_hits)} 条",
                status="COMPLETED",
                current_step=3,
                total_steps=8,
                percent=36,
                detail=format_ranked_hits(bm25_hits, lambda chunk_id: self._to_evidence(chunk_id, 0.0, retrieval_source="bm25")),
            )
            progress_reporter.emit("query.vector", f"向量召回：{query_text}", current_step=4, total_steps=8, percent=45)
            vector_hits = self._vector_search(query_text, filtered_chunks, limit=candidate_budget)
            ranked_lists.append(vector_hits)
            progress_reporter.emit(
                "query.vector",
                f"向量召回完成：{query_text}，命中 {len(vector_hits)} 条",
                status="COMPLETED",
                current_step=4,
                total_steps=8,
                percent=52,
                detail=format_ranked_hits(vector_hits, lambda chunk_id: self._to_evidence(chunk_id, 0.0, retrieval_source="vector")),
            )

        progress_reporter.emit("query.fusion", "正在执行 RRF/RAG-Fusion 融合排序", current_step=5, total_steps=8, percent=62)
        fused = reciprocal_rank_fusion(ranked_lists)
        diagnostics["candidateBudget"] = candidate_budget
        candidates = fused[:candidate_budget]
        candidate_evidences = [
            self._to_evidence(chunk_id, score, retrieval_source="fusion")
            for chunk_id, score in candidates
        ]
        parent_aggregated = aggregate_parent_evidences(
            candidate_evidences,
            chunks=[
                ParentAggregationChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    text=chunk.text,
                    metadata=chunk.metadata,
                )
                for chunk in filtered_chunks
            ],
            limit=candidate_budget,
        )
        candidate_evidences = parent_aggregated.evidences
        diagnostics.update(parent_aggregated.diagnostics())
        progress_reporter.emit(
            "query.fusion",
            f"RAG-Fusion 完成：融合 {len(ranked_lists)} 个召回列表，父段聚合后得到 {len(candidate_evidences)} 个候选",
            status="COMPLETED",
            current_step=5,
            total_steps=8,
            percent=70,
            detail=format_evidence_titles(candidate_evidences),
        )
        rerank_model = os.getenv("RAG_RERANK_MODEL") or "qwen3-rerank"
        progress_reporter.emit(
            "query.rerank",
            f"目前在使用 {rerank_model} 模型完成候选 evidence 重排事件",
            current_step=6,
            total_steps=8,
            percent=78,
            detail=f"目前在使用 {rerank_model} 模型完成候选 evidence 重排事件",
        )
        reranked = rerank_evidences(request.question, candidate_evidences, candidate_budget)
        diagnostics.update(reranked.diagnostics())
        diversified = dedupe_evidences_for_context(request.question, reranked.evidences, request.topK)
        diagnostics.update(diversified.diagnostics())
        progress_reporter.emit(
            "query.rerank",
            f"重排完成：输入 {len(candidate_evidences)} 条，保留 {len(diversified.evidences)} 条最终 evidence",
            status="COMPLETED",
            current_step=6,
            total_steps=8,
            percent=84,
            detail=format_evidence_titles(diversified.evidences),
        )
        guard = evaluate_answer_guard(
            question=request.question,
            expanded_queries=expanded_queries,
            evidences=diversified.evidences,
            diagnostics=diagnostics,
        )
        diagnostics["answerGuard"] = guard.diagnostics()
        supporting_evidence_ids = set(guard.supportingEvidenceIds)
        supporting_evidences = [
            item
            for item in diversified.evidences
            if item.evidenceId in supporting_evidence_ids
        ]
        progress_reporter.emit(
            "query.guard",
            guard.message if guard.answerStatus == "ANSWERED" else f"回答准入拒答：{guard.refusalReason}，可回答分 {guard.confidence:.4f}",
            status="COMPLETED",
            current_step=7,
            total_steps=8,
            percent=88,
            detail=(
                f"answerStatus={guard.answerStatus}；refusalReason={guard.refusalReason}；"
                f"confidence={guard.confidence:.4f}；supportingEvidenceCount={len(guard.supportingEvidenceIds)}；"
                f"candidateCount={len(diversified.evidences)}；thresholds={guard.thresholds}"
            ),
        )
        if guard.answerStatus == "REFUSED":
            progress_reporter.emit(
                "query.answer",
                "证据不足，已跳过 LLM 回答生成",
                status="COMPLETED",
                current_step=8,
                total_steps=8,
                percent=100,
                detail=f"refusalReason={guard.refusalReason}；未调用回答模型",
            )
            return QueryResponse(
                answer=guard.message,
                answerStatus=guard.answerStatus,
                refusalReason=guard.refusalReason,
                refusalPolicy=guard.refusalPolicy,
                confidence=guard.confidence,
                supportingEvidenceIds=[],
                refusalMessage=refusal_short_message(guard.refusalReason),
                expandedQueries=expanded_queries,
                evidences=[],
                diagnostics=diagnostics,
                progressEvents=progress_reporter.events,
            )

        answer_model = os.getenv("RAG_LLM_MODEL") or "qwen-plus"
        progress_reporter.emit(
            "query.answer",
            f"目前在使用 {answer_model} 模型完成基于 evidence 生成回答事件",
            current_step=8,
            total_steps=8,
            percent=92,
            detail=f"目前在使用 {answer_model} 模型完成基于 evidence 生成回答事件",
        )
        generated = generate_grounded_answer(request.question, supporting_evidences)
        diagnostics.update(generated.diagnostics())
        progress_reporter.emit(
            "query.answer",
            "RAG 检索问答完成",
            status="COMPLETED",
            current_step=8,
            total_steps=8,
            percent=100,
            detail=f"回答模型：{generated.diagnostics().get('answerModel') or answer_model}；引用 evidence 数：{len(supporting_evidences)}",
        )
        return QueryResponse(
            answer=generated.answer,
            answerStatus=guard.answerStatus,
            refusalReason=guard.refusalReason,
            refusalPolicy=guard.refusalPolicy,
            confidence=guard.confidence,
            supportingEvidenceIds=guard.supportingEvidenceIds,
            refusalMessage=None,
            expandedQueries=expanded_queries,
            evidences=supporting_evidences,
            diagnostics=diagnostics,
            progressEvents=progress_reporter.events,
        )

    @logged_rag_method("overview", "memory_overview", "读取内存模式 RAG 概览")
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

    @logged_rag_method("evidence.list", "memory_list_evidences", "读取内存模式文档 evidence")
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

    def promote_staged_index(
        self,
        *,
        canonical_document_id: str,
        staging_document_id: str,
        job_id: str,
        request_version: int,
        expected_chunk_count: int | None = None,
    ) -> dict[str, Any]:
        """内存模式下幂等复制 staging chunks 为 canonical chunks。"""
        staging_chunks = [
            chunk
            for chunk in sorted(self.chunks.values(), key=lambda item: int(item.metadata.get("chunkPosition") or 0))
            if chunk.document_id == staging_document_id
        ]
        if not staging_chunks:
            raise RuntimeError(f"staging 索引不存在或切块为空: {staging_document_id}")
        expected_count = expected_chunk_count or len(staging_chunks)
        if expected_count != len(staging_chunks):
            raise RuntimeError("staging 切块数与 Java result 不一致")
        canonical_chunks = [chunk for chunk in self.chunks.values() if chunk.document_id == canonical_document_id]
        if canonical_chunks:
            try:
                canonical_version = int(canonical_chunks[0].metadata.get("requestVersion") or 0)
            except Exception:
                canonical_version = 0
            if canonical_version > int(request_version):
                raise RuntimeError(
                    "拒绝用旧版本 staging 覆盖新 canonical："
                    f"canonicalVersion={canonical_version}, requestVersion={request_version}, documentId={canonical_document_id}"
                )
        if len(canonical_chunks) == expected_count and canonical_chunks:
            metadata = canonical_chunks[0].metadata
            if (
                str(metadata.get("sourceJobId") or metadata.get("jobId") or "") == str(job_id)
                and str(metadata.get("requestVersion") or "") == str(request_version)
                and str(metadata.get("stagingDocumentId") or "") == str(staging_document_id)
            ):
                return {
                    "alreadyPromoted": True,
                    "canonicalChunkCount": len(canonical_chunks),
                    "stagingChunkCount": len(staging_chunks),
                }
        self._remove_document(canonical_document_id)
        staging_doc = dict(self.documents.get(staging_document_id) or {})
        staging_doc["documentId"] = canonical_document_id
        staging_doc["visibilityScope"] = "private"
        self.documents[canonical_document_id] = staging_doc
        for chunk in staging_chunks:
            metadata = dict(chunk.metadata)
            metadata.update(
                {
                    "documentId": canonical_document_id,
                    "canonicalDocumentId": canonical_document_id,
                    "stagingDocumentId": staging_document_id,
                    "sourceJobId": job_id,
                    "jobId": job_id,
                    "requestVersion": request_version,
                    "visibilityScope": "private",
                }
            )
            new_chunk_id = chunk.chunk_id.replace(staging_document_id, canonical_document_id, 1)
            promoted = Chunk(
                chunk_id=new_chunk_id,
                document_id=canonical_document_id,
                text=chunk.text,
                metadata=metadata,
            )
            self.chunks[new_chunk_id] = promoted
            self.term_freqs[new_chunk_id] = Counter(tokenize(promoted.text))
            self.doc_freq.update(set(self.term_freqs[new_chunk_id]))
            self.embeddings[new_chunk_id] = list(self.embeddings.get(chunk.chunk_id, []))
        if staging_document_id in self.documents:
            self.documents[staging_document_id]["visibilityScope"] = "staging_promoted"
        return {
            "alreadyPromoted": False,
            "canonicalChunkCount": len(staging_chunks),
            "stagingChunkCount": len(staging_chunks),
        }

    @logged_rag_method("index.cleanup", "memory_remove_document", "清理旧内存索引")
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

    @logged_rag_method("query.filter", "memory_filter_chunks", "按元数据过滤内存候选切块")
    def _filter_chunks(self, metadata_filter: dict) -> list[Chunk]:
        if not metadata_filter:
            return list(self.chunks.values())

        filter_plan = build_metadata_filter_plan(metadata_filter)
        result = []
        for chunk in self.chunks.values():
            if matches_metadata_filter(chunk.metadata, filter_plan):
                result.append(chunk)
        return result

    @logged_rag_method("query.bm25", "memory_bm25_search", "执行内存 BM25 召回")
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

    @logged_rag_method("query.vector", "memory_vector_search", "执行内存向量召回")
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
            metadata=build_evidence_metadata_view(metadata),
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
    with log_model_call(
        stage="embedding.chunk",
        action="dashscope_embedding",
        model_name=model,
        event="文本向量生成",
        extra_context={"dimensions": dimensions, "textLength": len(text)},
    ):
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


def format_metadata_filter(metadata_filter: dict[str, Any]) -> str:
    """格式化查询元数据过滤条件，避免前端只看到过滤阶段名称。"""
    visible_items = [
        f"{key}={value}"
        for key, value in metadata_filter.items()
        if value is not None and value != "" and value != []
    ]
    return "过滤条件：" + ("；".join(visible_items) if visible_items else "无")


def format_ranked_hits(
    hits: list[tuple[str, float]],
    evidence_loader,
    *,
    limit: int = 3,
) -> str:
    """格式化召回命中的 Top evidence 标题和分数。"""
    if not hits:
        return "未命中候选 evidence"
    lines: list[str] = []
    for index, (chunk_id, score) in enumerate(hits[:limit], start=1):
        try:
            evidence = evidence_loader(chunk_id)
            lines.append(f"{index}. {evidence.title} / {evidence.sectionName} / 分数 {score:.4f}")
        except Exception:
            lines.append(f"{index}. {chunk_id} / 分数 {score:.4f}")
    if len(hits) > limit:
        lines.append(f"另有 {len(hits) - limit} 条候选")
    return "；".join(lines)


def format_evidence_titles(evidences: list[Evidence], *, limit: int = 5) -> str:
    """格式化候选 evidence，用于融合、重排和回答阶段详情。"""
    if not evidences:
        return "无候选 evidence"
    lines = [
        f"{index}. {item.title} / {item.sectionName} / 分数 {item.score:.4f}"
        for index, item in enumerate(evidences[:limit], start=1)
    ]
    if len(evidences) > limit:
        lines.append(f"另有 {len(evidences) - limit} 条候选")
    return "；".join(lines)


def reciprocal_rank_fusion(ranked_lists: list[list[tuple[str, float]]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for ranked in ranked_lists:
        for rank, (chunk_id, _score) in enumerate(ranked, start=1):
            scores[chunk_id] += 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: item[1], reverse=True)


def chunk_percent(index: int, total: int, start: int, end: int) -> int:
    if total <= 0:
        return start
    return min(end, start + round(index * (end - start) / total))


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
        from rag.indexes.pgvector_store import PgVectorRagStore

        return PgVectorRagStore(database_url=database_url)
    raise RuntimeError(f"不支持的 RAG_STORE_BACKEND: {backend}")
