from __future__ import annotations

import json
import math
import os
from collections import Counter
from typing import Any

from rag.bailian_llm import generate_grounded_answer
from rag.chunkers.chunking import RecursiveChunker
from rag.models import Chunk, utc_now_iso
from rag.loaders.parse_quality import QualitySignals, evaluate_parse_quality
from rag.process_logger import logged_rag_method, process_event
from rag.rerankers.reranking import rerank_evidences
from rag.retrievers.evidence_diversity import build_evidence_metadata_view, dedupe_evidences_for_context
from rag.retrievers.parent_aggregation import ParentAggregationChunk, aggregate_parent_evidences
from rag.retrievers.retrieval import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    build_playback_url,
    chunk_percent,
    embed_text,
    embedding_model_name,
    expand_queries,
    format_evidence_titles,
    format_metadata_filter,
    format_query_variants,
    format_ranked_hits,
    reciprocal_rank_fusion,
    tokenize,
)
from rag.indexes.summary_index import SummaryIndex
from rag.text_sanitizer import (
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
from rag.progress import RagProgressReporter


FILTER_COLUMNS = {
    "documentId": "d.document_id",
    "documentType": "d.document_type",
    "source": "d.source",
    "userId": "d.user_id",
    "visibilityScope": "d.visibility_scope",
    "language": "d.language",
    "parser": "d.parser",
    "sectionName": "c.section_name",
}


class PgVectorRagStore:
    """PostgreSQL/pgvector-backed RAG store."""

    def __init__(self, database_url: str, dimensions: int | None = None, ensure_schema: bool = True) -> None:
        self.database_url = database_url
        self.schema = os.getenv("RAG_DATABASE_SCHEMA", "learning_evidence")
        self.dimensions = dimensions or int(os.getenv("RAG_VECTOR_DIMENSIONS", str(DEFAULT_EMBEDDING_DIMENSIONS)))
        self.chunker = RecursiveChunker()
        self.summary_index = SummaryIndex()
        if ensure_schema:
            self.ensure_schema()

    @logged_rag_method("index.text", "pgvector_index_text", "pgvector 模式索引文本资料")
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

    @logged_rag_method("index.blocks", "pgvector_index_blocks", "pgvector 模式写入解析块索引")
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
        if not chunks:
            message = f"递归切块结果为空，已拒绝写入 rag_document：{document_id}"
            process_event(
                stage="index.database",
                action="pgvector_index_rejected_empty_chunks",
                message=message,
                level="ERROR",
                success=False,
                context={"documentId": document_id, "chunkCount": 0, "parser": parser, "status": status},
            )
            self._delete_orphan_document_index(document_id)
            if progress_reporter:
                progress_reporter.emit(
                    "index.failed",
                    "索引失败：递归切块结果为空，未写入 RAG 文档记录",
                    status="FAILED",
                    current_step=8,
                    total_steps=8,
                    current_chunk=0,
                    total_chunks=0,
                    percent=0,
                    detail=message,
                    parser=parser,
                )
            raise RuntimeError(message)

        if progress_reporter:
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
            action="pgvector_index_blocks_chunked",
            message=f"pgvector 准备写入 {len(chunks)} 个切块",
            context={"chunkCount": len(chunks), "summaryChildCount": len(summary_chunks), "parser": parser, "status": status},
        )

        total_chunks = len(chunks)
        prepared_chunks: list[tuple[Chunk, dict[str, int], str]] = []
        for index, chunk in enumerate(chunks, start=1):
            process_event(
                stage="embedding.chunk",
                action="pgvector_embedding_chunk",
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
            token_counts = Counter(tokenize(chunk.text))
            embedding = embed_text(chunk.text, dimensions=self.dimensions)
            prepared_chunks.append((chunk, dict(token_counts), vector_literal(embedding)))

        Json = self._json_adapter()
        process_event(
            stage="index.database",
            action="pgvector_index_transaction_start",
            message="开始事务写入 rag_document 和 rag_chunk",
            context={"chunkCount": total_chunks, "documentId": document_id},
        )
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.execute("DELETE FROM rag_document WHERE document_id = %s", (document_id,))
                    cursor.execute(
                        """
                        INSERT INTO rag_document (
                            document_id,
                            title,
                            document_type,
                            source,
                            user_id,
                            visibility_scope,
                            language,
                            parser,
                            document_summary,
                            section_summaries,
                            chunk_count
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            document_id,
                            title,
                            document_type,
                            source,
                            user_id,
                            visibility_scope,
                            language,
                            parser,
                            summaries["documentSummary"],
                            Json(summaries["sectionSummaries"]),
                            total_chunks,
                        ),
                    )
                    for index, (chunk, token_counts, embedding_literal) in enumerate(prepared_chunks, start=1):
                        process_event(
                            stage="vector.upsert.chunk",
                            action="pgvector_upsert_chunk",
                            message=f"第 {index}/{total_chunks} 块：写入向量数据库",
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
                                "vector.upsert.chunk",
                                f"第 {index}/{total_chunks} 块：写入向量数据库",
                                current_step=8,
                                total_steps=8,
                                current_chunk=index,
                                total_chunks=total_chunks,
                                chunk_id=chunk.chunk_id,
                                percent=chunk_percent(index, total_chunks, 48, 92),
                            )
                        cursor.execute(
                            """
                            INSERT INTO rag_chunk (
                                chunk_id,
                                document_id,
                                chunk_position,
                                section_name,
                                text,
                                metadata,
                                term_counts,
                                token_count,
                                embedding
                            )
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                            """,
                            (
                                chunk.chunk_id,
                                document_id,
                                int(chunk.metadata.get("chunkPosition") or 0),
                                str(chunk.metadata.get("sectionName") or "全文"),
                                chunk.text,
                                Json(chunk.metadata),
                                Json(token_counts),
                                sum(token_counts.values()),
                                embedding_literal,
                            ),
                        )
                    persisted_count = self._count_document_chunks_in_transaction(cursor, document_id)
                    if persisted_count != total_chunks:
                        raise RuntimeError(
                            "rag_document/rag_chunk 事务内计数不一致："
                            f"documentId={document_id}, expected={total_chunks}, actual={persisted_count}"
                        )
        committed_count = self._count_document_chunks(document_id)
        if committed_count != total_chunks:
            self._delete_document_index(document_id)
            raise RuntimeError(
                "rag_document/rag_chunk 提交后计数不一致，已清理本次索引："
                f"documentId={document_id}, expected={total_chunks}, actual={committed_count}"
            )
        process_event(
            stage="index.database",
            action="pgvector_index_transaction_completed",
            message="rag_document 和 rag_chunk 事务写入完成",
            context={"chunkCount": total_chunks, "documentId": document_id},
        )

        final_status = status
        if progress_reporter:
            progress_reporter.emit(
                "index.completed",
                f"索引完成：状态 {final_status}，共 {total_chunks} 个切块",
                status="COMPLETED",
                current_step=8,
                total_steps=8,
                current_chunk=total_chunks,
                total_chunks=total_chunks,
                percent=100,
                parser=parser,
                extra_context={"parseStatus": final_status, "chunkCount": total_chunks, "parser": parser},
            )
        return IndexResponse(
            documentId=document_id,
            title=title,
            status=final_status,
            chunkCount=total_chunks,
            parser=parser,
            documentSummary=summaries["documentSummary"],
            parseQuality=parse_quality,
            progressEvents=progress_reporter.events if progress_reporter else [],
        )

    @logged_rag_method("query.pipeline", "pgvector_query", "pgvector 模式执行 RAG 检索问答")
    def query(self, request: QueryRequest, progress_reporter: RagProgressReporter | None = None) -> QueryResponse:
        """执行 RAG 查询；任务接口可注入 reporter 实时读取阶段事件。"""
        progress_reporter = progress_reporter or RagProgressReporter(document_id="query", persist=False)
        progress_reporter.emit("query.expand", "正在生成 Multi-Query 查询变体", current_step=1, total_steps=7, percent=8)
        metadata_filter = request.metadataFilter or {}
        expanded_queries = expand_queries(request.question)
        progress_reporter.emit(
            "query.expand",
            f"Multi-Query 已生成 {len(expanded_queries)} 个查询变体",
            status="COMPLETED",
            current_step=1,
            total_steps=7,
            percent=14,
            detail=format_query_variants(expanded_queries),
        )
        progress_reporter.emit("query.filter", "正在按元数据过滤候选切块", current_step=2, total_steps=7, percent=18)
        filtered_chunks = self._load_filtered_chunks(metadata_filter)
        progress_reporter.emit(
            "query.filter",
            f"元数据过滤完成：保留 {len(filtered_chunks)} 个候选切块",
            status="COMPLETED",
            current_step=2,
            total_steps=7,
            percent=24,
            detail=format_metadata_filter(metadata_filter),
        )
        chunk_by_id = {row["chunk_id"]: row for row in filtered_chunks}
        ranked_lists: list[list[tuple[str, float]]] = []
        diagnostics: dict[str, int | list[str]] = {
            "expandedQueries": expanded_queries,
            "filteredChunkCount": len(filtered_chunks),
        }

        candidate_budget = max(request.topK * 4, 20)
        for query_text in expanded_queries:
            limit = candidate_budget
            progress_reporter.emit("query.bm25", f"BM25 召回：{query_text}", current_step=3, total_steps=7, percent=30)
            bm25_hits = self._bm25_search(query_text, filtered_chunks, limit=limit)
            ranked_lists.append(bm25_hits)
            progress_reporter.emit(
                "query.bm25",
                f"BM25 召回完成：{query_text}，命中 {len(bm25_hits)} 条",
                status="COMPLETED",
                current_step=3,
                total_steps=7,
                percent=36,
                detail=format_ranked_hits(
                    bm25_hits,
                    lambda chunk_id: self._to_evidence(chunk_by_id[chunk_id], 0.0, retrieval_source="bm25")
                    if chunk_id in chunk_by_id
                    else None,
                ),
            )
            progress_reporter.emit("query.vector", f"向量召回：{query_text}", current_step=4, total_steps=7, percent=45)
            vector_hits = self._vector_search(query_text, metadata_filter, limit=limit)
            ranked_lists.append(vector_hits)
            progress_reporter.emit(
                "query.vector",
                f"向量召回完成：{query_text}，命中 {len(vector_hits)} 条",
                status="COMPLETED",
                current_step=4,
                total_steps=7,
                percent=52,
                detail=format_ranked_hits(
                    vector_hits,
                    lambda chunk_id: self._to_evidence(chunk_by_id[chunk_id], 0.0, retrieval_source="vector")
                    if chunk_id in chunk_by_id
                    else None,
                ),
            )

        progress_reporter.emit("query.fusion", "正在执行 RRF/RAG-Fusion 融合排序", current_step=5, total_steps=7, percent=62)
        fused = reciprocal_rank_fusion(ranked_lists)
        diagnostics["candidateBudget"] = candidate_budget
        candidates = [
            (chunk_id, score)
            for chunk_id, score in fused[:candidate_budget]
            if chunk_id in chunk_by_id
        ]
        candidate_evidences = [
            self._to_evidence(chunk_by_id[chunk_id], score, retrieval_source="fusion")
            for chunk_id, score in candidates
        ]
        parent_aggregated = aggregate_parent_evidences(
            candidate_evidences,
            chunks=[
                ParentAggregationChunk(
                    chunk_id=str(row["chunk_id"]),
                    document_id=str(row["document_id"]),
                    text=str(row["text"]),
                    metadata=ensure_dict(row.get("metadata")),
                )
                for row in filtered_chunks
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
            total_steps=7,
            percent=70,
            detail=format_evidence_titles(candidate_evidences),
        )
        rerank_model = os.getenv("RAG_RERANK_MODEL") or "qwen3-rerank"
        progress_reporter.emit(
            "query.rerank",
            f"目前在使用 {rerank_model} 模型完成候选 evidence 重排事件",
            current_step=6,
            total_steps=7,
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
            total_steps=7,
            percent=84,
            detail=format_evidence_titles(diversified.evidences),
        )
        answer_model = os.getenv("RAG_LLM_MODEL") or "qwen-plus"
        progress_reporter.emit(
            "query.answer",
            f"目前在使用 {answer_model} 模型完成基于 evidence 生成回答事件",
            current_step=7,
            total_steps=7,
            percent=90,
            detail=f"目前在使用 {answer_model} 模型完成基于 evidence 生成回答事件",
        )
        generated = generate_grounded_answer(request.question, diversified.evidences)
        diagnostics.update(generated.diagnostics())
        progress_reporter.emit(
            "query.answer",
            "RAG 检索问答完成",
            status="COMPLETED",
            current_step=7,
            total_steps=7,
            percent=100,
            detail=f"回答模型：{generated.diagnostics().get('answerModel') or answer_model}；引用 evidence 数：{len(diversified.evidences)}",
        )
        return QueryResponse(
            answer=generated.answer,
            expandedQueries=expanded_queries,
            evidences=diversified.evidences,
            diagnostics=diagnostics,
            progressEvents=progress_reporter.events,
        )

    @logged_rag_method("overview", "pgvector_overview", "读取 pgvector RAG 概览")
    def overview(self) -> OverviewResponse:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        COUNT(1) AS document_count,
                        COALESCE(SUM(chunk_count), 0) AS chunk_count
                    FROM rag_document
                    """
                )
                counts = cursor.fetchone() or {}
                cursor.execute(
                    """
                    SELECT title
                    FROM rag_document
                    ORDER BY updated_at DESC, document_id DESC
                    LIMIT 1
                    """
                )
                last = cursor.fetchone()
        chunk_count = int(counts.get("chunk_count") or 0)
        return OverviewResponse(
            documentCount=int(counts.get("document_count") or 0),
            chunkCount=chunk_count,
            evidenceCount=chunk_count,
            lastIndexedTitle=last.get("title") if last else None,
        )

    @logged_rag_method("evidence.list", "pgvector_list_evidences", "读取 pgvector 文档 evidence")
    def list_evidences(self, document_id: str, limit: int = 20) -> list[Evidence]:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        c.chunk_id,
                        c.document_id,
                        c.chunk_position,
                        c.section_name,
                        c.text,
                        c.metadata,
                        c.term_counts,
                        c.token_count,
                        d.title,
                        d.source,
                        d.document_type
                    FROM rag_chunk c
                    JOIN rag_document d ON d.document_id = c.document_id
                    WHERE c.document_id = %s
                    ORDER BY c.chunk_position ASC
                    LIMIT %s
                    """,
                    (document_id, limit),
                )
                rows = cursor.fetchall()
        return [self._to_evidence(normalize_row(row), 1.0, retrieval_source="summary") for row in rows]

    @logged_rag_method("index.schema", "pgvector_ensure_schema", "检查 pgvector RAG 表结构")
    def ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rag_document (
                        document_id VARCHAR(120) PRIMARY KEY,
                        title VARCHAR(255) NOT NULL,
                        document_type VARCHAR(50) NOT NULL,
                        source VARCHAR(255),
                        user_id VARCHAR(120) NOT NULL,
                        visibility_scope VARCHAR(30) NOT NULL DEFAULT 'private',
                        language VARCHAR(30) NOT NULL DEFAULT 'zh-CN',
                        parser VARCHAR(80),
                        document_summary TEXT,
                        section_summaries JSONB NOT NULL DEFAULT '{}'::jsonb,
                        chunk_count INTEGER NOT NULL DEFAULT 0,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS rag_chunk (
                        chunk_id VARCHAR(180) PRIMARY KEY,
                        document_id VARCHAR(120) NOT NULL REFERENCES rag_document(document_id) ON DELETE CASCADE,
                        chunk_position INTEGER NOT NULL,
                        section_name VARCHAR(255) NOT NULL DEFAULT '全文',
                        text TEXT NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        term_counts JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        token_count INTEGER NOT NULL DEFAULT 0,
                        embedding VECTOR({self.dimensions}) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                    )
                    """
                )
                cursor.execute(
                    """
                    SELECT format_type(attribute.atttypid, attribute.atttypmod) AS column_type
                    FROM pg_attribute attribute
                    JOIN pg_class klass ON klass.oid = attribute.attrelid
                    JOIN pg_namespace namespace ON namespace.oid = klass.relnamespace
                    WHERE namespace.nspname = %s
                      AND klass.relname = 'rag_chunk'
                      AND attribute.attname = 'embedding'
                      AND NOT attribute.attisdropped
                    """,
                    (self.schema,),
                )
                embedding_column = cursor.fetchone()
                expected_column_type = f"vector({self.dimensions})"
                actual_column_type = embedding_column.get("column_type") if embedding_column else None
                if actual_column_type != expected_column_type:
                    raise RuntimeError(
                        "rag_chunk.embedding 维度与当前配置不一致："
                        f"数据库={actual_column_type}，配置={expected_column_type}。"
                        "请先执行 infra/sql/alter-database/20260617_0100_migrate_embedding_1024.sql 后重建资料索引。"
                    )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_rag_document_type
                        ON rag_document(document_type)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_rag_document_user_visibility
                        ON rag_document(user_id, visibility_scope)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_rag_chunk_document_position
                        ON rag_chunk(document_id, chunk_position)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_rag_chunk_metadata_gin
                        ON rag_chunk USING GIN (metadata)
                    """
                )
                cursor.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_rag_chunk_embedding_hnsw
                        ON rag_chunk USING hnsw (embedding vector_cosine_ops)
                    """
                )

    @logged_rag_method("index.cleanup", "pgvector_delete_document_index", "清理 pgvector 文档索引")
    def _delete_document_index(self, document_id: str) -> None:
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    self._delete_document_index_with_cursor(cursor, document_id)

    @logged_rag_method("index.cleanup", "pgvector_delete_orphan_document_index", "清理 pgvector 孤儿文档索引")
    def _delete_orphan_document_index(self, document_id: str) -> None:
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    self._delete_document_index_with_cursor(cursor, document_id)

    def _delete_document_index_with_cursor(self, cursor, document_id: str) -> None:
        cursor.execute("DELETE FROM rag_chunk WHERE document_id = %s", (document_id,))
        cursor.execute("DELETE FROM rag_document WHERE document_id = %s", (document_id,))

    def _count_document_chunks(self, document_id: str) -> int:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                return self._count_document_chunks_in_transaction(cursor, document_id)

    def _count_document_chunks_in_transaction(self, cursor, document_id: str) -> int:
        cursor.execute("SELECT COUNT(1) AS chunk_count FROM rag_chunk WHERE document_id = %s", (document_id,))
        row = cursor.fetchone() or {}
        return int(row.get("chunk_count") or 0)

    def _connect(self):
        try:
            import psycopg
            from psycopg import sql
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("使用 PostgreSQL/pgvector 需要安装 psycopg[binary] 依赖") from exc
        conn = psycopg.connect(self.database_url, row_factory=dict_row)
        with conn.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
        return conn

    def _json_adapter(self):
        try:
            from psycopg.types.json import Json
        except ImportError as exc:
            raise RuntimeError("使用 PostgreSQL/pgvector 需要安装 psycopg[binary] 依赖") from exc
        return Json

    @logged_rag_method("query.filter", "pgvector_load_filtered_chunks", "按元数据过滤 pgvector 候选切块")
    def _load_filtered_chunks(self, metadata_filter: dict[str, Any]) -> list[dict[str, Any]]:
        where_sql, params = build_filter_clause(metadata_filter)
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        c.chunk_id,
                        c.document_id,
                        c.chunk_position,
                        c.section_name,
                        c.text,
                        c.metadata,
                        c.term_counts,
                        c.token_count,
                        d.title,
                        d.source,
                        d.document_type
                    FROM rag_chunk c
                    JOIN rag_document d ON d.document_id = c.document_id
                    {where_sql}
                    ORDER BY d.updated_at DESC, c.chunk_position ASC
                    """,
                    params,
                )
                rows = cursor.fetchall()
        return [normalize_row(row) for row in rows]

    @logged_rag_method("query.bm25", "pgvector_bm25_search", "执行 pgvector BM25 召回")
    def _bm25_search(self, query_text: str, rows: list[dict[str, Any]], limit: int) -> list[tuple[str, float]]:
        query_terms = tokenize(query_text)
        if not query_terms or not rows:
            return []

        doc_freq: Counter[str] = Counter()
        for row in rows:
            doc_freq.update(set(row["term_counts"]))

        avgdl = sum(int(row.get("token_count") or 0) for row in rows) / max(len(rows), 1)
        total_docs = max(len(rows), 1)
        k1 = 1.5
        b = 0.75
        scores: list[tuple[str, float]] = []
        for row in rows:
            term_counts = row["term_counts"]
            doc_len = int(row.get("token_count") or sum(term_counts.values()) or 1)
            score = 0.0
            for term in query_terms:
                freq = int(term_counts.get(term, 0))
                if freq == 0:
                    continue
                df = doc_freq.get(term, 0)
                idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
                score += idf * (freq * (k1 + 1)) / (freq + k1 * (1 - b + b * doc_len / max(avgdl, 1)))
            if score > 0:
                scores.append((str(row["chunk_id"]), score))
        return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]

    @logged_rag_method("query.vector", "pgvector_vector_search", "执行 pgvector 向量召回")
    def _vector_search(self, query_text: str, metadata_filter: dict[str, Any], limit: int) -> list[tuple[str, float]]:
        if not tokenize(query_text):
            return []
        query_vector = vector_literal(embed_text(query_text, dimensions=self.dimensions))
        where_sql, filter_params = build_filter_clause(metadata_filter)
        params: list[Any] = [query_vector, *filter_params, query_vector, limit]
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        c.chunk_id,
                        1 - (c.embedding <=> %s::vector) AS score
                    FROM rag_chunk c
                    JOIN rag_document d ON d.document_id = c.document_id
                    {where_sql}
                    ORDER BY c.embedding <=> %s::vector
                    LIMIT %s
                    """,
                    params,
                )
                rows = cursor.fetchall()
        return [(str(row["chunk_id"]), float(row["score"] or 0.0)) for row in rows]

    def _to_evidence(self, row: dict[str, Any], score: float, retrieval_source: str) -> Evidence:
        snippet = " ".join(str(row["text"]).split())
        if len(snippet) > 220:
            snippet = snippet[:220].rstrip() + "..."
        metadata = ensure_dict(row.get("metadata"))
        title = str(row.get("title") or metadata.get("title") or "未命名资料")
        section_title = str(metadata.get("sectionTitle") or row.get("section_name") or "全文")
        return Evidence(
            evidenceId=str(row["chunk_id"]),
            documentId=str(row["document_id"]),
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
            source=str(row.get("source") or "unknown"),
            sourcePath=as_optional_str(metadata.get("sourcePath")),
            assetPath=as_optional_str(metadata.get("assetPath")),
            playbackUrl=build_playback_url(
                document_id=str(row["document_id"]),
                title=title,
                metadata=metadata,
            ),
            sectionName=section_title,
            documentType=str(row.get("document_type") or "document"),
            score=round(score, 6),
            retrievalSource=retrieval_source,  # type: ignore[arg-type]
            parseEngine=as_optional_str(metadata.get("parseEngine") or metadata.get("parser")),
            metadata=build_evidence_metadata_view(metadata),
        )


def build_filter_clause(metadata_filter: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    for key, value in metadata_filter.items():
        if value is None or value == "" or value == []:
            continue
        expression = FILTER_COLUMNS.get(key)
        if expression is None:
            expression = "c.metadata ->> %s"
            params.append(key)

        if isinstance(value, list):
            placeholders = ", ".join(["%s"] * len(value))
            clauses.append(f"{expression} IN ({placeholders})")
            params.extend(str(item) for item in value)
        else:
            clauses.append(f"{expression} = %s")
            params.append(str(value))

    if not clauses:
        return "", []
    return "WHERE " + " AND ".join(clauses), params


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized["metadata"] = ensure_dict(normalized.get("metadata"))
    normalized["term_counts"] = ensure_dict(normalized.get("term_counts"))
    return normalized


def ensure_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


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


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
