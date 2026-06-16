from __future__ import annotations

import json
import math
import os
from collections import Counter
from typing import Any

from rag.chunking import RecursiveChunker
from rag.models import Chunk, utc_now_iso
from rag.retrieval import (
    build_answer,
    embed_text,
    expand_queries,
    reciprocal_rank_fusion,
    tokenize,
)
from rag.summary_index import SummaryIndex
from schemas.rag import (
    Evidence,
    IndexResponse,
    IndexTextRequest,
    OverviewResponse,
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
        self.dimensions = dimensions or int(os.getenv("RAG_VECTOR_DIMENSIONS", "128"))
        self.chunker = RecursiveChunker()
        self.summary_index = SummaryIndex()
        if ensure_schema:
            self.ensure_schema()

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
        chunks = self.chunker.split(request.content, document_id=document_id, metadata=metadata)
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
                        request.title,
                        request.documentType,
                        request.source,
                        request.userId,
                        request.visibilityScope,
                        request.language,
                        request.parser,
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
            title=request.title,
            status="INDEXED",
            chunkCount=len(chunks),
            parser=request.parser,
            documentSummary=summaries["documentSummary"],
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
        selected = [(chunk_id, score) for chunk_id, score in fused[: request.topK] if chunk_id in chunk_by_id]
        evidences = [self._to_evidence(chunk_by_id[chunk_id], score) for chunk_id, score in selected]
        answer = build_answer(request.question, evidences)
        return QueryResponse(
            answer=answer,
            expandedQueries=expanded_queries,
            evidences=evidences,
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

    def ensure_schema(self) -> None:
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS rag_document (
                        document_id VARCHAR(120) PRIMARY KEY,
                        title VARCHAR(255) NOT NULL,
                        document_type VARCHAR(50) NOT NULL,
                        source VARCHAR(255),
                        user_id VARCHAR(120) NOT NULL DEFAULT 'demo-user',
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
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("使用 PostgreSQL/pgvector 需要安装 psycopg[binary] 依赖") from exc
        return psycopg.connect(self.database_url, row_factory=dict_row)

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

    def _to_evidence(self, row: dict[str, Any], score: float) -> Evidence:
        snippet = " ".join(str(row["text"]).split())
        if len(snippet) > 220:
            snippet = snippet[:220].rstrip() + "..."
        return Evidence(
            evidenceId=str(row["chunk_id"]),
            documentId=str(row["document_id"]),
            title=str(row.get("title") or "未命名资料"),
            snippet=snippet,
            source=str(row.get("source") or "unknown"),
            sectionName=str(row.get("section_name") or "全文"),
            documentType=str(row.get("document_type") or "document"),
            score=round(score, 6),
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


def vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
