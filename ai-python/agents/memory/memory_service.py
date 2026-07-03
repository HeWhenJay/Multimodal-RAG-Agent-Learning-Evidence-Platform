from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.schemas.agent_memory import (
    MemoryCandidate,
    MemoryConflictRequest,
    MemoryConflictResponse,
    MemoryExtractRequest,
    MemoryExtractResponse,
    MemoryIndexDeleteRequest,
    MemoryIndexDeleteResponse,
    MemoryIndexUpsertRequest,
    MemoryIndexUpsertResponse,
    MemoryQueryRequest,
    MemoryQueryResponse,
    MemoryQueryResult,
)
from rag.indexes.pgvector_store import vector_literal
from rag.retrievers.query_expansion import local_expand_queries
from rag.retrievers.retrieval import embed_text, embedding_provider_name, hash_embed_text, reciprocal_rank_fusion, tokenize


IN_MEMORY_INDEX: dict[str, dict[str, Any]] = {}


class AgentMemoryService:
    """Agent 记忆智能服务，只处理候选、冲突、索引和检索。"""

    def __init__(self) -> None:
        self.database_url = os.getenv("RAG_DATABASE_URL") or os.getenv("DATABASE_URL")
        self.schema = os.getenv("RAG_DATABASE_SCHEMA", "learning_evidence")
        self.dimensions = int(os.getenv("RAG_VECTOR_DIMENSIONS", "1024"))

    def query(self, request: MemoryQueryRequest) -> MemoryQueryResponse:
        """在 Java 已授权范围内执行记忆混合检索。"""
        expanded_queries = local_expand_queries(request.query, count=5)
        ranked_lists: list[list[tuple[str, float]]] = []
        retrieval_limit = max(request.topK * 4, 10)
        vector_hit_ids: set[str] = set()

        if self.database_url:
            rows = self._load_memory_rows(request, limit=200)
            for query in expanded_queries:
                ranked_lists.append(self._bm25_search(query, rows, limit=retrieval_limit))
                vector_hits = self._pgvector_search(query, request, limit=retrieval_limit)
                vector_hit_ids.update(memory_id for memory_id, _score in vector_hits)
                ranked_lists.append(vector_hits)
            fused = reciprocal_rank_fusion(ranked_lists)
            fused_memory_ids = [memory_id for memory_id, _score in fused]
            final_rows = self._load_memory_rows_by_ids(request, fused_memory_ids)
            row_by_id = {str(row["memoryId"]): row for row in final_rows}
        else:
            rows = self._load_memory_rows(request, limit=200)
            for query in expanded_queries:
                ranked_lists.append(self._bm25_search(query, rows, limit=retrieval_limit))
                vector_hits = self._vector_search(query, rows, limit=retrieval_limit)
                vector_hit_ids.update(memory_id for memory_id, _score in vector_hits)
                ranked_lists.append(vector_hits)
            fused = reciprocal_rank_fusion(ranked_lists)
            row_by_id = {str(row["memoryId"]): row for row in rows}

        scored: list[dict[str, Any]] = []
        for rank, (memory_id, fusion_score) in enumerate(fused, start=1):
            row = row_by_id.get(memory_id)
            if not row:
                continue
            score = self._final_score(row, fusion_score, rank)
            scored.append({**row, "score": round(score, 6)})
        scored.sort(key=lambda item: item["score"], reverse=True)
        memories = [self._to_query_result(item) for item in scored[: request.topK]]
        return MemoryQueryResponse(
            memories=memories,
            diagnostics={
                "expandedQueries": expanded_queries,
                "candidateCount": len(rows),
                "bm25CandidateCount": len(rows),
                "vectorHitCount": len(vector_hit_ids),
                "finalCandidateCount": len(row_by_id),
                "rankedListCount": len(ranked_lists),
                "retrievalProvider": "pgvector" if self.database_url else "memory",
                "embeddingProvider": embedding_provider_name() if self.database_url else "hash",
                "pgvectorUsed": bool(self.database_url),
                "vectorDimensions": self.dimensions,
            },
        )

    def extract(self, request: MemoryExtractRequest) -> MemoryExtractResponse:
        """从任务快照中确定性提炼待确认记忆候选。"""
        candidates: list[MemoryCandidate] = []
        goal = text_value(request.taskInput.get("goal"))
        match_summary = text_value(request.draft.get("matchSummary") or request.final.get("matchSummary"))
        if goal and match_summary:
            content = f"用户最近的 Agent 任务目标：{truncate(goal, 180)}；任务输出摘要：{truncate(match_summary, 220)}"
            candidates.append(
                self._candidate(
                    request,
                    memory_type="EPISODIC",
                    namespace="agent_task",
                    subject_key="recent_task_insight",
                    content=content,
                    summary=truncate(match_summary, 160),
                    confidence=0.62,
                    importance=0.56,
                )
            )
        for gap in normalized_gaps(request.draft.get("gaps") or request.final.get("gaps"))[:3]:
            skill = text_value(gap.get("skill")) or "岗位能力"
            priority = text_value(gap.get("priority")) or "MEDIUM"
            suggestion = text_value(gap.get("suggestion")) or f"补充 {skill} 的学习证据"
            candidates.append(
                self._candidate(
                    request,
                    memory_type="SEMANTIC",
                    namespace="career_profile",
                    subject_key=f"weak_skill.{safe_key(skill)}",
                    content=f"用户当前岗位适配中 {skill} 证据偏弱或缺失，优先级 {priority}；建议：{truncate(suggestion, 180)}。",
                    summary=f"{skill} 证据偏弱，建议补充相关学习证据。",
                    confidence=0.66,
                    importance=0.72 if priority == "HIGH" else 0.62,
                )
            )
        explicit_preference = explicit_memory_text(goal)
        if explicit_preference:
            candidates.append(
                self._candidate(
                    request,
                    memory_type="PREFERENCE",
                    namespace="user_preference",
                    subject_key="explicit_instruction",
                    content=explicit_preference,
                    summary=truncate(explicit_preference, 120),
                    confidence=0.78,
                    importance=0.76,
                )
            )
        unique = dedupe_candidates(candidates)
        return MemoryExtractResponse(candidates=unique, conflicts=[], provider="deterministic-memory-extractor")

    def conflicts(self, request: MemoryConflictRequest) -> MemoryConflictResponse:
        """对新候选和旧记忆做最小确定性冲突判断。"""
        candidate_text = request.candidate.content.strip()
        for old in request.existingMemories:
            same_subject = (
                text_value(old.get("namespace")) == request.candidate.namespace
                and text_value(old.get("subjectKey")) == request.candidate.subjectKey
            )
            old_text = text_value(old.get("content") or old.get("summary"))
            if same_subject and old_text == candidate_text:
                return MemoryConflictResponse(relationType="DUPLICATES", decision="IGNORE", reason="同一主题下内容完全重复。", confidence=0.9)
            if same_subject and semantic_negation_conflict(old_text, candidate_text):
                return MemoryConflictResponse(relationType="CONFLICTS_WITH", decision="REVIEW_REQUIRED", reason="同一主题下出现可能相反的偏好或事实。", confidence=0.7)
        return MemoryConflictResponse(relationType="REFINES", decision="ADD_NEW", reason="未发现明确重复或冲突，建议作为新候选保存。", confidence=0.62)

    def upsert_index(self, request: MemoryIndexUpsertRequest) -> MemoryIndexUpsertResponse:
        """写入或更新一条已由 Java 授权的记忆索引。"""
        retrieval_text = request.retrievalText or f"{request.namespace}\n{request.subjectKey}\n{request.summary}"
        term_counts = dict(Counter(tokenize(retrieval_text)))
        metadata = {
            "userId": request.userId,
            "memoryType": request.memoryType,
            "namespace": request.namespace,
            "scopeType": request.scopeType,
            "scopeId": request.scopeId,
            "subjectKey": request.subjectKey,
            "status": "ACTIVE",
            "confidence": request.confidence,
            "importance": request.importance,
            "sensitivityLevel": request.sensitivityLevel,
        }
        if not self.database_url:
            embedding = hash_embed_text(retrieval_text, self.dimensions)
            IN_MEMORY_INDEX[request.memoryId] = {
                "memoryId": request.memoryId,
                "userId": request.userId,
                "memoryType": request.memoryType,
                "namespace": request.namespace,
                "scopeType": request.scopeType,
                "scopeId": request.scopeId,
                "subjectKey": request.subjectKey,
                "summary": request.summary,
                "content": request.content,
                "retrievalText": retrieval_text,
                "termCounts": term_counts,
                "embedding": embedding,
                "status": "ACTIVE",
                "confidence": request.confidence,
                "importance": request.importance,
                "deletedAt": None,
                "updatedAt": now_iso(),
            }
            return MemoryIndexUpsertResponse(
                memoryId=request.memoryId,
                indexed=True,
                status="ACTIVE",
                diagnostics={"backend": "memory", "embeddingProvider": "hash", "vectorDimensions": self.dimensions},
            )
        embedding = embed_text(retrieval_text, dimensions=self.dimensions)
        self._upsert_pgvector(request, retrieval_text, term_counts, embedding, metadata)
        return MemoryIndexUpsertResponse(
            memoryId=request.memoryId,
            indexed=True,
            status="ACTIVE",
            diagnostics={
                "backend": "pgvector",
                "embeddingProvider": embedding_provider_name(),
                "vectorDimensions": self.dimensions,
            },
        )

    def delete_index(self, request: MemoryIndexDeleteRequest) -> MemoryIndexDeleteResponse:
        """删除或停用一条记忆索引。"""
        if not self.database_url:
            deleted = IN_MEMORY_INDEX.pop(request.memoryId, None) is not None
            return MemoryIndexDeleteResponse(memoryId=request.memoryId, deleted=deleted, diagnostics={"backend": "memory"})
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM agent_memory_embedding WHERE memory_id = %s AND user_id = %s",
                        (request.memoryId, request.userId),
                    )
                    deleted = cursor.rowcount > 0
        return MemoryIndexDeleteResponse(memoryId=request.memoryId, deleted=deleted, diagnostics={"backend": "pgvector"})

    def _candidate(
        self,
        request: MemoryExtractRequest,
        *,
        memory_type: str,
        namespace: str,
        subject_key: str,
        content: str,
        summary: str,
        confidence: float,
        importance: float,
    ) -> MemoryCandidate:
        source_hash = sha256("|".join([request.userId, request.taskId, namespace, subject_key, content]))
        return MemoryCandidate(
            memoryType=memory_type,
            namespace=namespace,
            scopeType="USER",
            subjectKey=subject_key,
            content=content,
            summary=summary,
            sourceTaskId=request.taskId,
            sourceHash=source_hash,
            confidence=confidence,
            importance=importance,
            sensitivityLevel="LOW",
        )

    def _memory_filter_clause(
        self,
        request: MemoryQueryRequest,
        *,
        item_alias: str = "item",
        embedding_alias: str = "embedding",
    ) -> tuple[str, list[Any]]:
        """生成 PostgreSQL 记忆查询的授权、状态和范围过滤条件。"""
        where_parts = [
            f"{item_alias}.user_id = %s",
            f"{item_alias}.status = 'ACTIVE'",
            f"{item_alias}.deleted_at IS NULL",
            f"({item_alias}.valid_until IS NULL OR {item_alias}.valid_until > CURRENT_TIMESTAMP)",
            f"COALESCE({item_alias}.sensitivity_level, 'LOW') != 'HIGH'",
            f"{embedding_alias}.status = 'ACTIVE'",
            f"{embedding_alias}.deleted_at IS NULL",
        ]
        params: list[Any] = [request.userId]
        if request.namespaces:
            where_parts.append(f"{item_alias}.namespace = ANY(%s)")
            params.append(request.namespaces)
        if request.memoryTypes:
            where_parts.append(f"{item_alias}.memory_type = ANY(%s)")
            params.append(request.memoryTypes)
        scope_clause, scope_params = build_scope_clause(request.allowedScopes, item_alias=item_alias)
        if scope_clause:
            where_parts.append(scope_clause)
            params.extend(scope_params)
        return " AND ".join(where_parts), params

    def _load_memory_rows(self, request: MemoryQueryRequest, limit: int = 200) -> list[dict[str, Any]]:
        """加载 BM25 所需的记忆元数据与词频，不读取 pgvector embedding。"""
        if not self.database_url:
            return [
                normalize_memory_row(row)
                for row in IN_MEMORY_INDEX.values()
                if row.get("userId") == request.userId
                and row.get("status") == "ACTIVE"
                and row.get("deletedAt") is None
                and namespace_allowed(row, request)
                and scope_allowed(row, request)
            ]
        where_sql, params = self._memory_filter_clause(request)
        params.append(limit)
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        item.id AS memory_id,
                        item.user_id,
                        item.memory_type,
                        item.namespace,
                        item.scope_type,
                        item.scope_id,
                        item.subject_key,
                        item.summary,
                        item.content,
                        item.status,
                        item.confidence,
                        item.importance,
                        item.deleted_at,
                        item.updated_at,
                        embedding.retrieval_text,
                        embedding.term_counts
                    FROM agent_memory_embedding embedding
                    JOIN agent_memory_item item ON item.id = embedding.memory_id
                    WHERE {where_sql}
                    ORDER BY item.updated_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                rows = cursor.fetchall()
        return [normalize_memory_row(row) for row in rows]

    def _load_memory_rows_by_ids(self, request: MemoryQueryRequest, memory_ids: list[str]) -> list[dict[str, Any]]:
        """按融合后的记忆 ID 回填最终组装字段，避免 pgvector-only 命中丢失。"""
        unique_ids = list(dict.fromkeys(memory_ids))
        if not unique_ids:
            return []
        if not self.database_url:
            rows = [
                row
                for row in IN_MEMORY_INDEX.values()
                if str(row.get("memoryId")) in unique_ids
                and row.get("userId") == request.userId
                and row.get("status") == "ACTIVE"
                and row.get("deletedAt") is None
                and namespace_allowed(row, request)
                and scope_allowed(row, request)
            ]
            return [normalize_memory_row(row) for row in rows]
        where_sql, params = self._memory_filter_clause(request)
        params.extend([unique_ids, len(unique_ids)])
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        item.id AS memory_id,
                        item.user_id,
                        item.memory_type,
                        item.namespace,
                        item.scope_type,
                        item.scope_id,
                        item.subject_key,
                        item.summary,
                        item.content,
                        item.status,
                        item.confidence,
                        item.importance,
                        item.deleted_at,
                        item.updated_at,
                        embedding.retrieval_text,
                        embedding.term_counts
                    FROM agent_memory_embedding embedding
                    JOIN agent_memory_item item ON item.id = embedding.memory_id
                    WHERE {where_sql}
                      AND item.id = ANY(%s)
                    ORDER BY item.updated_at DESC
                    LIMIT %s
                    """,
                    params,
                )
                rows = cursor.fetchall()
        return [normalize_memory_row(row) for row in rows]

    def _bm25_search(self, query: str, rows: list[dict[str, Any]], limit: int) -> list[tuple[str, float]]:
        terms = tokenize(query)
        if not terms or not rows:
            return []
        doc_freq: Counter[str] = Counter()
        for row in rows:
            doc_freq.update(set(row["termCounts"]))
        avgdl = sum(sum(row["termCounts"].values()) for row in rows) / max(len(rows), 1)
        total_docs = max(len(rows), 1)
        scores: list[tuple[str, float]] = []
        for row in rows:
            term_counts = row["termCounts"]
            doc_len = sum(term_counts.values()) or 1
            score = 0.0
            for term in terms:
                freq = int(term_counts.get(term, 0))
                if freq <= 0:
                    continue
                df = doc_freq.get(term, 0)
                idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
                score += idf * (freq * 2.5) / (freq + 1.5 * (1 - 0.75 + 0.75 * doc_len / max(avgdl, 1)))
            if score > 0:
                scores.append((str(row["memoryId"]), score))
        return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]

    def _vector_search(self, query: str, rows: list[dict[str, Any]], limit: int) -> list[tuple[str, float]]:
        """内存后端使用 hash embedding 余弦相似度作为离线降级向量召回。"""
        query_vector = hash_embed_text(query, self.dimensions)
        scores = []
        for row in rows:
            vector = row.get("embedding")
            if not vector:
                vector = hash_embed_text(row["retrievalText"], self.dimensions)
            score = cosine_similarity(query_vector, vector)
            if score > 0:
                scores.append((str(row["memoryId"]), score))
        return sorted(scores, key=lambda item: item[1], reverse=True)[:limit]

    def _pgvector_search(self, query: str, request: MemoryQueryRequest, limit: int) -> list[tuple[str, float]]:
        """PostgreSQL 后端使用 pgvector 距离算子执行真实向量召回。"""
        query_vector = vector_literal(embed_text(query, dimensions=self.dimensions))
        where_sql, params = self._memory_filter_clause(request)
        execute_params: list[Any] = [query_vector, *params, query_vector, limit]
        with self._connect() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT
                        item.id AS memory_id,
                        1 - (embedding.embedding <=> %s::vector) AS score
                    FROM agent_memory_embedding embedding
                    JOIN agent_memory_item item ON item.id = embedding.memory_id
                    WHERE {where_sql}
                    ORDER BY embedding.embedding <=> %s::vector
                    LIMIT %s
                    """,
                    execute_params,
                )
                rows = cursor.fetchall()
        results: list[tuple[str, float]] = []
        for row in rows:
            memory_id = row.get("memory_id") or row.get("memoryId")
            if memory_id is None:
                continue
            results.append((str(memory_id), float(row.get("score") or 0.0)))
        return results

    def _final_score(self, row: dict[str, Any], fusion_score: float, rank: int) -> float:
        relevance = min(1.0, fusion_score * 20)
        importance = float(row.get("importance") or 0.5)
        confidence = float(row.get("confidence") or 0.5)
        recency = recency_score(row.get("updatedAt"))
        scope = scope_priority(row.get("scopeType"))
        return 0.40 * relevance + 0.20 * importance + 0.15 * recency + 0.15 * confidence + 0.10 * scope - rank * 0.0001

    def _to_query_result(self, row: dict[str, Any]) -> MemoryQueryResult:
        return MemoryQueryResult(
            memoryId=str(row["memoryId"]),
            userId=str(row["userId"]),
            memoryType=str(row["memoryType"]),
            namespace=str(row["namespace"]),
            scopeType=str(row["scopeType"]),
            scopeId=row.get("scopeId"),
            subjectKey=str(row["subjectKey"]),
            summary=str(row["summary"]),
            status=str(row["status"]),
            confidence=float(row.get("confidence") or 0.5),
            importance=float(row.get("importance") or 0.5),
            score=float(row.get("score") or 0.0),
            deletedAt=row.get("deletedAt"),
        )

    def _upsert_pgvector(
        self,
        request: MemoryIndexUpsertRequest,
        retrieval_text: str,
        term_counts: dict[str, int],
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> None:
        Json = self._json_adapter()
        with self._connect() as conn:
            with conn.transaction():
                with conn.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM agent_memory_embedding WHERE memory_id = %s AND user_id = %s",
                        (request.memoryId, request.userId),
                    )
                    cursor.execute(
                        """
                        INSERT INTO agent_memory_embedding (
                            id,
                            memory_id,
                            user_id,
                            chunk_id,
                            retrieval_text,
                            term_counts,
                            embedding,
                            metadata,
                            status,
                            deleted_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s, 'ACTIVE', NULL)
                        """,
                        (
                            f"agent-memory-embedding-{uuid.uuid4().hex}",
                            request.memoryId,
                            request.userId,
                            f"{request.memoryId}-chunk-1",
                            retrieval_text,
                            Json(term_counts),
                            vector_literal(embedding),
                            Json(metadata),
                        ),
                    )

    def _connect(self):
        try:
            import psycopg
            from psycopg import sql
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("使用 PostgreSQL Agent 记忆索引需要安装 psycopg[binary]") from exc
        conn = psycopg.connect(self.database_url, row_factory=dict_row)
        with conn.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(self.schema)))
            cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
        return conn

    def _json_adapter(self):
        try:
            from psycopg.types.json import Json
        except ImportError as exc:
            raise RuntimeError("使用 PostgreSQL Agent 记忆索引需要安装 psycopg[binary]") from exc
        return Json


def build_scope_clause(scopes: list[Any], *, item_alias: str = "item") -> tuple[str, list[Any]]:
    """按 Java 传入的 allowedScopes 生成记忆可见范围过滤条件。"""
    if not scopes:
        return f"{item_alias}.scope_type = 'USER'", []
    clauses: list[str] = []
    params: list[Any] = []
    for scope in scopes:
        scope_type = getattr(scope, "scopeType", None)
        scope_id = getattr(scope, "scopeId", None)
        if scope_type == "USER":
            clauses.append(f"({item_alias}.scope_type = 'USER' AND {item_alias}.scope_id IS NULL)")
        elif scope_type and scope_id:
            clauses.append(f"({item_alias}.scope_type = %s AND {item_alias}.scope_id = %s)")
            params.extend([scope_type, scope_id])
    if not clauses:
        return f"{item_alias}.scope_type = 'USER'", []
    return "(" + " OR ".join(clauses) + ")", params


def normalize_memory_row(row: dict[str, Any]) -> dict[str, Any]:
    metadata = ensure_dict(row.get("metadata"))
    term_counts = ensure_dict(row.get("term_counts") or row.get("termCounts"))
    memory_id = row.get("memory_id") or row.get("memoryId")
    retrieval_text = row.get("retrieval_text") or row.get("retrievalText") or row.get("summary") or ""
    return {
        "memoryId": str(memory_id),
        "userId": str(row.get("user_id") or row.get("userId")),
        "memoryType": str(row.get("memory_type") or row.get("memoryType") or metadata.get("memoryType") or "SEMANTIC"),
        "namespace": str(row.get("namespace") or metadata.get("namespace") or "agent_task"),
        "scopeType": str(row.get("scope_type") or row.get("scopeType") or metadata.get("scopeType") or "USER"),
        "scopeId": row.get("scope_id") or row.get("scopeId") or metadata.get("scopeId"),
        "subjectKey": str(row.get("subject_key") or row.get("subjectKey") or metadata.get("subjectKey") or "memory"),
        "summary": str(row.get("summary") or ""),
        "content": str(row.get("content") or ""),
        "retrievalText": str(retrieval_text),
        "termCounts": {str(key): int(value) for key, value in term_counts.items()},
        "embedding": row.get("embedding"),
        "status": str(row.get("status") or "ACTIVE"),
        "confidence": float(row.get("confidence") or metadata.get("confidence") or 0.5),
        "importance": float(row.get("importance") or metadata.get("importance") or 0.5),
        "deletedAt": iso_or_none(row.get("deleted_at") or row.get("deletedAt")),
        "updatedAt": iso_or_none(row.get("updated_at") or row.get("updatedAt")),
    }


def namespace_allowed(row: dict[str, Any], request: MemoryQueryRequest) -> bool:
    if request.namespaces and row.get("namespace") not in request.namespaces:
        return False
    if request.memoryTypes and row.get("memoryType") not in request.memoryTypes:
        return False
    return True


def scope_allowed(row: dict[str, Any], request: MemoryQueryRequest) -> bool:
    if not request.allowedScopes:
        return row.get("scopeType") == "USER"
    for scope in request.allowedScopes:
        if row.get("scopeType") == scope.scopeType and (scope.scopeType == "USER" or row.get("scopeId") == scope.scopeId):
            return True
    return False


def normalized_gaps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def dedupe_candidates(candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
    seen: set[str] = set()
    unique: list[MemoryCandidate] = []
    for candidate in candidates:
        key = candidate.sourceHash or sha256(candidate.content)
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def explicit_memory_text(goal: str) -> str:
    if not goal:
        return ""
    triggers = ["记住", "以后都", "以后请", "不要再", "偏好", "按这个来"]
    if not any(trigger in goal for trigger in triggers):
        return ""
    return f"用户显式偏好或约束：{truncate(goal, 220)}"


def semantic_negation_conflict(left: str, right: str) -> bool:
    negative_terms = ["不要", "不再", "避免", "禁止"]
    positive_terms = ["喜欢", "优先", "以后都", "保持"]
    return (any(term in left for term in negative_terms) and any(term in right for term in positive_terms)) or (
        any(term in right for term in negative_terms) and any(term in left for term in positive_terms)
    )


def recency_score(value: Any) -> float:
    if not value:
        return 0.5
    try:
        updated = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        days = max((datetime.now(timezone.utc) - updated.astimezone(timezone.utc)).total_seconds() / 86400, 0)
        return math.exp(-days / 30)
    except Exception:
        return 0.5


def scope_priority(scope_type: Any) -> float:
    return {
        "TASK": 1.0,
        "SESSION": 0.95,
        "MATERIAL": 0.8,
        "PROJECT": 0.7,
        "USER": 0.55,
        "SYSTEM": 0.35,
    }.get(str(scope_type), 0.4)


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


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


def text_value(value: Any) -> str:
    return "" if value is None else str(value).strip()


def safe_key(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    ascii_part = "".join(ch for ch in value.lower() if ch.isascii() and ch.isalnum())
    return ascii_part[:24] or digest


def truncate(value: str, max_length: int) -> str:
    return value if len(value) <= max_length else value[:max_length]


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def iso_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
