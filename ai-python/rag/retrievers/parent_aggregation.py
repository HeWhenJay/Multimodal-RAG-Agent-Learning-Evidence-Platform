from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas.rag import Evidence


@dataclass(frozen=True)
class ParentAggregationChunk:
    chunk_id: str
    document_id: str
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ParentAggregationResult:
    evidences: list[Evidence]
    matched_child_ids: list[str]
    expanded_parent_ids: list[str]
    prerequisite_added_ids: list[str]
    enabled: bool

    def diagnostics(self) -> dict[str, Any]:
        return {
            "parentAggregation": {
                "enabled": self.enabled,
                "matchedChildCount": len(self.matched_child_ids),
                "expandedParentCount": len(self.expanded_parent_ids),
                "prerequisiteExpansionEnabled": False,
            },
            "matchedChildIds": self.matched_child_ids,
            "expandedParentIds": self.expanded_parent_ids,
            "prerequisiteAddedIds": self.prerequisite_added_ids,
        }


def aggregate_parent_evidences(
    evidences: list[Evidence],
    *,
    chunks: list[ParentAggregationChunk],
    limit: int,
) -> ParentAggregationResult:
    """把命中的 child evidence 聚合到父段上下文，供 memory 和 pgvector 共用。"""
    chunk_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    chunks_by_parent: dict[tuple[str, str], list[ParentAggregationChunk]] = {}
    for chunk in chunks:
        parent_id = parent_segment_id(chunk.metadata)
        if not parent_id:
            continue
        chunks_by_parent.setdefault((chunk.document_id, parent_id), []).append(chunk)

    matched_child_ids: list[str] = []
    expanded_parent_ids: list[str] = []
    selected: list[Evidence] = []
    seen_parent_keys: set[tuple[str, str]] = set()
    seen_evidence_ids: set[str] = set()

    for evidence in evidences:
        chunk = chunk_by_id.get(evidence.evidenceId)
        metadata = chunk.metadata if chunk else (evidence.metadata or {})
        parent_id = parent_segment_id(metadata)
        if not parent_id:
            child = evidence.model_copy(update={"metadata": {**(evidence.metadata or {}), "retrievalLayer": "child"}})
            if child.evidenceId not in seen_evidence_ids:
                selected.append(child)
                seen_evidence_ids.add(child.evidenceId)
            continue

        matched_child_ids.append(evidence.evidenceId)
        parent_key = (evidence.documentId, parent_id)
        if parent_key in seen_parent_keys:
            continue
        seen_parent_keys.add(parent_key)
        expanded_parent_ids.append(parent_id)
        parent_chunks = sorted(
            chunks_by_parent.get(parent_key) or ([chunk] if chunk else []),
            key=chunk_sort_key,
        )
        selected.append(build_parent_evidence(evidence, parent_id, parent_chunks))
        if len(selected) >= limit:
            break

    if len(selected) < limit:
        for evidence in evidences:
            if evidence.evidenceId in seen_evidence_ids:
                continue
            if any(evidence.evidenceId in (item.metadata or {}).get("matchedChildIds", []) for item in selected):
                continue
            selected.append(evidence)
            seen_evidence_ids.add(evidence.evidenceId)
            if len(selected) >= limit:
                break

    return ParentAggregationResult(
        evidences=selected[:limit],
        matched_child_ids=list(dict.fromkeys(matched_child_ids)),
        expanded_parent_ids=list(dict.fromkeys(expanded_parent_ids)),
        prerequisite_added_ids=[],
        enabled=True,
    )


def build_parent_evidence(
    best_child: Evidence,
    parent_id: str,
    parent_chunks: list[ParentAggregationChunk],
) -> Evidence:
    """根据父段所有子块生成一个可 rerank 的父段 evidence。"""
    best_metadata = parent_chunks[0].metadata if parent_chunks else (best_child.metadata or {})
    matched_child_ids = [chunk.chunk_id for chunk in parent_chunks]
    matched_child_kinds = list(dict.fromkeys(str(chunk.metadata.get("childKind") or "raw") for chunk in parent_chunks))
    parent_start = optional_str(best_metadata.get("parentStartTime")) or best_child.startTime
    parent_end = optional_str(best_metadata.get("parentEndTime")) or best_child.endTime
    evidence_start = best_child.startTime or parent_start
    evidence_end = best_child.endTime or parent_end
    section_name = parent_section_name(best_child, best_metadata, parent_start, parent_end)
    metadata = {
        **(best_child.metadata or {}),
        "parentSegmentId": parent_id,
        "parentStartTime": parent_start,
        "parentEndTime": parent_end,
        "parentKind": best_metadata.get("parentKind"),
        "retrievalLayer": "parent_aggregated",
        "matchedChildIds": matched_child_ids,
        "matchedChildKinds": matched_child_kinds,
    }
    return best_child.model_copy(
        update={
            "evidenceId": parent_id,
            "blockId": parent_id,
            "blockType": "text",
            "startTime": evidence_start,
            "endTime": evidence_end,
            "sectionTitle": section_name,
            "sectionName": section_name,
            "snippet": parent_snippet(parent_chunks, fallback=best_child.snippet),
            "score": best_child.score,
            "retrievalSource": best_child.retrievalSource,
            "metadata": metadata,
        }
    )


def parent_segment_id(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("parentSegmentId")
    return str(value) if value else None


def chunk_sort_key(chunk: ParentAggregationChunk) -> tuple[int, str]:
    try:
        position = int(chunk.metadata.get("chunkPosition") or 0)
    except (TypeError, ValueError):
        position = 0
    return position, chunk.chunk_id


def parent_snippet(chunks: list[ParentAggregationChunk], *, fallback: str) -> str:
    """拼接父段上下文，控制长度避免 evidence 文本过长。"""
    if not chunks:
        return fallback
    summary_texts = [clean_text(chunk.text) for chunk in chunks if chunk.metadata.get("childKind") in {"summary", "video_segment_summary"}]
    raw_texts = [clean_text(chunk.text) for chunk in chunks if chunk.metadata.get("childKind") not in {"summary", "video_segment_summary"}]
    merged = " ".join([*summary_texts[:1], *raw_texts])
    if len(merged) > 620:
        return merged[:620].rstrip() + "..."
    return merged or fallback


def parent_section_name(
    evidence: Evidence,
    metadata: dict[str, Any],
    parent_start: str | None,
    parent_end: str | None,
) -> str:
    if metadata.get("parentKind") == "video_segment" and parent_start:
        return f"视频父段 {parent_start} - {parent_end or parent_start}"
    return str(metadata.get("sectionTitle") or metadata.get("sectionName") or evidence.sectionName or "全文")


def clean_text(value: str) -> str:
    return " ".join(str(value).split())


def optional_str(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)
