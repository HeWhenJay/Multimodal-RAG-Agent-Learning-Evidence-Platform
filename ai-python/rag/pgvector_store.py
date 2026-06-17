from __future__ import annotations

import json
import math
import os
from collections import Counter
from typing import Any

from rag.bailian_llm import generate_grounded_answer
from rag.chunking import RecursiveChunker
from rag.models import Chunk, utc_now_iso
from rag.parse_quality import QualitySignals, evaluate_parse_quality
from rag.reranking import rerank_evidences
from rag.retrieval import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    build_playback_url,
    embed_text,
    expand_queries,
    reciprocal_rank_fusion,
    tokenize,
)
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
        chunks = self.chunker.split_blocks(blocks, document_id=document_id, metadata=metadata)
        summaries = self.summary_index.build(chunks)

        Json = self._json_adapter()
        with self._connect() as conn:
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
                        len(chunks),
                    ),
                )
                for chunk in chunks:
                    token_counts = Counter(tokenize(chunk.text))
                    embedding = embed_text(chunk.text, dimensions=self.dimensions)
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
                            Json(dict(token_counts)),
                            sum(token_counts.values()),
                            vector_literal(embedding),
                        ),
                    )

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
        metadata_filter = request.metadataFilter or {}
        expanded_queries = expand_queries(request.question)
        filtered_chunks = self._load_filtered_chunks(metadata_filter)
        chunk_by_id = {row["chunk_id"]: row for row in filtered_chunks}
        ranked_lists: list[list[tuple[str, float]]] = []
        diagnostics: dict[str, int | list[str]] = {
            "expandedQueries": expanded_queries,
            "filteredChunkCount": len(filtered_chunks),
        }

        for query_text in expanded_queries:
            limit = max(request.topK * 3, 10)
            ranked_lists.append(self._bm25_search(query_text, filtered_chunks, limit=limit))
            ranked_lists.append(self._vector_search(query_text, metadata_filter, limit=limit))

        fused = reciprocal_rank_fusion(ranked_lists)
        candidates = [
            (chunk_id, score)
            for chunk_id, score in fused[: max(request.topK * 3, request.topK)]
            if chunk_id in chunk_by_id
        ]
        candidate_evidences = [
            self._to_evidence(chunk_by_id[chunk_id], score, retrieval_source="fusion")
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
            metadata=metadata.get("blockMetadata") if isinstance(metadata.get("blockMetadata"), dict) else {},
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
