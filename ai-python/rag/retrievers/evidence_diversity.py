from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any

from app.schemas.rag import Evidence


TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_+#.-]+")
EVIDENCE_METADATA_KEYS = {
    "mediaType",
    "evidenceChannel",
    "duplicateGroupId",
    "contentHash",
    "normalizedTextHash",
    "representativeTime",
    "timeRanges",
    "mergedFrameCount",
    "dedupStrategy",
    "frameDuplicateGroupIds",
    "frameTimeRanges",
    "sourceFrameTimes",
    "visualGroupId",
    "suspectedVisualGroupId",
    "visualDecision",
    "visualTimeRanges",
    "visualSourceFrameTimes",
    "visualHash",
    "visualHashDistance",
}


@dataclass(frozen=True)
class DiversityResult:
    evidences: list[Evidence]
    removed_count: int
    group_count: int
    policy: str
    candidate_count: int

    def diagnostics(self) -> dict[str, Any]:
        return {
            "dedupRemovedCount": self.removed_count,
            "dedupGroupCount": self.group_count,
            "diversityPolicy": self.policy,
            "rerankedCandidateCount": self.candidate_count,
        }


def dedupe_evidences_for_context(question: str, evidences: list[Evidence], top_k: int) -> DiversityResult:
    """对重排后的 evidence 做多样性过滤，避免视频近重复证据占满上下文。"""
    if not diversity_enabled() or not evidences:
        return DiversityResult(
            evidences=evidences[:top_k],
            removed_count=0,
            group_count=0,
            policy="disabled" if not diversity_enabled() else "none",
            candidate_count=len(evidences),
        )

    selected: list[Evidence] = []
    skipped: list[Evidence] = []
    seen_groups: set[str] = set()
    time_window_counts: dict[tuple[str, int, str], int] = {}
    adjacent_subtitle_hash: str | None = None
    duplicate_groups_seen: set[str] = set()

    for evidence in evidences:
        channel = evidence_channel(evidence)
        group_key = stable_group_key(evidence)
        if group_key:
            duplicate_groups_seen.add(group_key)
        if should_skip_by_group(channel, group_key, seen_groups):
            skipped.append(evidence)
            continue
        if should_skip_by_time_window(evidence, channel, time_window_counts):
            skipped.append(evidence)
            continue
        subtitle_hash = normalized_snippet_hash(evidence)
        if channel == "subtitle" and adjacent_subtitle_hash == subtitle_hash:
            skipped.append(evidence)
            continue

        selected.append(evidence)
        if group_key:
            seen_groups.add(group_key)
        register_time_window(evidence, channel, time_window_counts)
        adjacent_subtitle_hash = subtitle_hash if channel == "subtitle" else None
        if len(selected) >= top_k:
            break

    if len(selected) < top_k:
        selected_ids = {item.evidenceId for item in selected}
        for evidence in evidences:
            if evidence.evidenceId in selected_ids:
                continue
            selected.append(evidence)
            selected_ids.add(evidence.evidenceId)
            if len(selected) >= top_k:
                break

    removed_count = max(0, len(evidences) - len(selected))
    return DiversityResult(
        evidences=selected[:top_k],
        removed_count=removed_count,
        group_count=len(duplicate_groups_seen),
        policy="video_duplicate_group_and_time_window",
        candidate_count=len(evidences),
    )


def build_evidence_metadata_view(metadata: dict[str, Any]) -> dict[str, Any]:
    """返回给查询层的受控 metadata 视图，补齐去重字段但避免原样暴露全部索引元数据。"""
    block_metadata = metadata.get("blockMetadata") if isinstance(metadata.get("blockMetadata"), dict) else {}
    view: dict[str, Any] = dict(block_metadata)
    for key in EVIDENCE_METADATA_KEYS:
        if key in metadata and metadata.get(key) is not None:
            view[key] = metadata.get(key)
        elif key in block_metadata and block_metadata.get(key) is not None:
            view[key] = block_metadata.get(key)
    return view


def should_skip_by_group(channel: str, group_key: str | None, seen_groups: set[str]) -> bool:
    if not group_key:
        return False
    if channel not in {"frame_ocr", "video_segment_summary"}:
        return False
    return group_key in seen_groups


def should_skip_by_time_window(
    evidence: Evidence,
    channel: str,
    counts: dict[tuple[str, int, str], int],
) -> bool:
    if channel not in {"frame_ocr", "video_segment_summary"}:
        return False
    key = time_window_key(evidence, channel)
    limit = max_per_time_window()
    return counts.get(key, 0) >= limit


def register_time_window(
    evidence: Evidence,
    channel: str,
    counts: dict[tuple[str, int, str], int],
) -> None:
    if channel not in {"frame_ocr", "video_segment_summary"}:
        return
    key = time_window_key(evidence, channel)
    counts[key] = counts.get(key, 0) + 1


def stable_group_key(evidence: Evidence) -> str | None:
    metadata = evidence.metadata or {}
    channel = evidence_channel(evidence)
    group_id = metadata.get("duplicateGroupId")
    if group_id:
        return f"{evidence.documentId}:{channel}:{group_id}"
    frame_group_ids = metadata.get("frameDuplicateGroupIds")
    if isinstance(frame_group_ids, list) and frame_group_ids:
        return f"{evidence.documentId}:{channel}:{','.join(str(item) for item in frame_group_ids)}"
    normalized_hash = metadata.get("normalizedTextHash") or normalized_snippet_hash(evidence)
    return f"{evidence.documentId}:{channel}:{normalized_hash}" if normalized_hash else None


def evidence_channel(evidence: Evidence) -> str:
    metadata = evidence.metadata or {}
    channel = str(metadata.get("evidenceChannel") or "")
    if channel:
        return channel
    if evidence.startTime and evidence.blockType == "image":
        return "frame_ocr"
    if evidence.startTime:
        return "subtitle"
    return "text"


def time_window_key(evidence: Evidence, channel: str) -> tuple[str, int, str]:
    seconds = timestamp_to_seconds(evidence.startTime)
    window = seconds // time_window_seconds()
    return evidence.documentId, window, channel


def normalized_snippet_hash(evidence: Evidence) -> str:
    metadata = evidence.metadata or {}
    existing = metadata.get("normalizedTextHash")
    if existing:
        return str(existing)
    text = " ".join(TOKEN_PATTERN.findall(evidence.snippet.lower()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def timestamp_to_seconds(value: str | None) -> int:
    if not value:
        return 0
    try:
        parts = [int(part) for part in value.replace(",", ".").split(".", 1)[0].split(":")]
    except ValueError:
        return 0
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    if len(parts) >= 3:
        hours, minutes, seconds = parts[-3:]
        return hours * 3600 + minutes * 60 + seconds
    return 0


def diversity_enabled() -> bool:
    return os.getenv("RAG_QUERY_DIVERSITY_DEDUP_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def time_window_seconds() -> int:
    return max(30, int(os.getenv("RAG_QUERY_VIDEO_TIME_WINDOW_SECONDS", "120")))


def max_per_time_window() -> int:
    return max(1, int(os.getenv("RAG_QUERY_VIDEO_MAX_PER_TIME_WINDOW", "1")))
