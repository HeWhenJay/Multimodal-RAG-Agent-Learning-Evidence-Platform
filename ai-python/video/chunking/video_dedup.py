from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any

from app.schemas.rag import DocumentBlock
from rag.observability.process_logger import logged_rag_method, process_event
from rag.observability.progress import RagProgressReporter


TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_+#.-]+")
FRAME_HEADING_PATTERN = re.compile(r"^视频画面\s+\d{1,2}:\d{2}(?::\d{2})?\s*$")
TRIGGER_PRIORITY = {
    "ppt_flip": 5,
    "initial_slide": 4,
    "ambiguous_visual": 3,
    "visual_verification": 3,
    "new_visual": 2,
    "interval": 1,
}


@dataclass(frozen=True)
class FrameBlockInfo:
    block: DocumentBlock
    normalized_text: str
    content_hash: str
    normalized_hash: str
    seconds: int
    time_text: str
    slide_key: str | None


@dataclass
class FrameBlockGroup:
    items: list[FrameBlockInfo]
    representative: FrameBlockInfo

    @property
    def last_seconds(self) -> int:
        return max(item.seconds for item in self.items)

    @property
    def first_seconds(self) -> int:
        return min(item.seconds for item in self.items)

    @property
    def slide_key(self) -> str | None:
        return self.representative.slide_key


@logged_rag_method("parse.video.dedup", "dedupe_video_frame_blocks", "合并视频画面 OCR 近重复块")
def dedupe_video_frame_blocks(
    blocks: list[DocumentBlock],
    document_id: str,
    *,
    progress_reporter: RagProgressReporter | None = None,
) -> tuple[list[DocumentBlock], dict[str, Any]]:
    """合并同一视频中高度相似的 frame_ocr 证据，保留时间范围和代表画面。"""
    frame_blocks = [block for block in blocks if block.metadata.get("evidenceChannel") == "frame_ocr"]
    passthrough_blocks = [block for block in blocks if block.metadata.get("evidenceChannel") != "frame_ocr"]
    if not dedup_enabled() or len(frame_blocks) <= 1:
        annotated = [annotate_single_frame(block, document_id) for block in frame_blocks]
        return [*passthrough_blocks, *annotated], build_stats(len(frame_blocks), len(frame_blocks), 0, enabled=dedup_enabled())

    infos = [build_frame_info(block) for block in frame_blocks]
    groups: list[FrameBlockGroup] = []
    for info in sorted(infos, key=lambda item: item.seconds):
        target = find_merge_group(info, groups)
        if target is None:
            groups.append(FrameBlockGroup(items=[info], representative=info))
            continue
        target.items.append(info)
        target.representative = choose_representative([target.representative, info])

    deduped = [build_group_block(group, document_id) for group in groups]
    deduped.sort(key=lambda block: timestamp_to_seconds(block.startTime))
    stats = build_stats(len(frame_blocks), len(deduped), sum(1 for group in groups if len(group.items) > 1), enabled=True)
    process_event(
        stage="parse.video.dedup",
        action="video_frame_ocr_dedup_completed",
        message=f"视频画面 OCR 去重完成：{stats['originalFrameCount']} -> {stats['dedupedFrameCount']}",
        context=stats,
    )
    if progress_reporter:
        progress_reporter.emit(
            "parse.video.dedup",
            f"视频画面 OCR 去重完成：{stats['originalFrameCount']} 帧合并为 {stats['dedupedFrameCount']} 个证据",
            current_step=4,
            total_steps=8,
            percent=27,
            detail=f"合并组数：{stats['dedupGroupCount']}，移除重复：{stats['dedupRemovedCount']}",
        )
    return [*passthrough_blocks, *deduped], stats


def find_merge_group(info: FrameBlockInfo, groups: list[FrameBlockGroup]) -> FrameBlockGroup | None:
    """按时间、课件页和文本相似度寻找可合并的已有组。"""
    for group in reversed(groups):
        if can_merge(info, group):
            return group
    return None


def can_merge(info: FrameBlockInfo, group: FrameBlockGroup) -> bool:
    """判断当前画面 OCR 是否能并入已有重复组。"""
    representative = group.representative
    if info.block.documentId != representative.block.documentId:
        return False
    if info.block.metadata.get("evidenceChannel") != representative.block.metadata.get("evidenceChannel"):
        return False
    if not same_slide_or_time_bucket(info, group):
        return False
    if abs(info.seconds - group.last_seconds) > max_gap_seconds() and not same_visual_group(info, group):
        return False
    if len(info.normalized_text) < min_text_chars() or len(representative.normalized_text) < min_text_chars():
        return info.normalized_hash == representative.normalized_hash
    return text_similarity(info.normalized_text, representative.normalized_text) >= text_threshold()


def same_slide_or_time_bucket(info: FrameBlockInfo, group: FrameBlockGroup) -> bool:
    """优先按 OCR 后确认的课件页或视觉组收窄范围；最终仍由文本相似度决定。"""
    if info.slide_key is not None or group.slide_key is not None:
        return info.slide_key is not None and info.slide_key == group.slide_key
    info_visual_group = info.block.metadata.get("visualGroupId")
    group_visual_group = group.representative.block.metadata.get("visualGroupId")
    if info_visual_group or group_visual_group:
        if info_visual_group is not None and info_visual_group == group_visual_group:
            return True
    window = fallback_time_window_seconds()
    return info.seconds // window == group.first_seconds // window


def same_visual_group(info: FrameBlockInfo, group: FrameBlockGroup) -> bool:
    """判断两个 OCR 块是否属于同一候选视觉组。"""
    info_visual_group = info.block.metadata.get("visualGroupId")
    group_visual_group = group.representative.block.metadata.get("visualGroupId")
    return bool(info_visual_group and info_visual_group == group_visual_group)


def choose_representative(items: list[FrameBlockInfo]) -> FrameBlockInfo:
    """选择更适合展示和向量化的代表帧。"""
    return sorted(
        items,
        key=lambda item: (
            TRIGGER_PRIORITY.get(str(item.block.metadata.get("frameTrigger") or ""), 0),
            item.block.confidence,
            len(item.normalized_text),
            -item.seconds,
        ),
        reverse=True,
    )[0]


def build_group_block(group: FrameBlockGroup, document_id: str) -> DocumentBlock:
    """根据重复组构造最终入库的代表 DocumentBlock。"""
    if len(group.items) == 1:
        return annotate_single_frame(group.items[0].block, document_id, info=group.items[0])

    representative = choose_representative(group.items)
    times = merge_source_frame_times(group.items)
    time_ranges = merge_time_ranges(group.items)
    start_time = times[0]
    end_time = times[-1]
    duplicate_group_id = duplicate_group_id_for(document_id, representative.normalized_hash, start_time)
    content_text = "\n".join(
        [
            f"视频画面聚合 {start_time} - {end_time}",
            strip_frame_heading(representative.block.contentText),
            f"重复出现时间：{'、'.join(times)}",
        ]
    )
    metadata = {
        **representative.block.metadata,
        "dedupStrategy": "video_frame_ocr_text_jaccard",
        "duplicateGroupId": duplicate_group_id,
        "contentHash": representative.content_hash,
        "normalizedTextHash": representative.normalized_hash,
        "representativeTime": representative.time_text,
        "timeRanges": time_ranges,
        "sourceFrameTimes": times,
        "visualTimeRanges": merge_visual_time_ranges(group.items),
        "visualSourceFrameTimes": merge_visual_source_frame_times(group.items),
        "mergedFrameCount": len(group.items),
        "sourceFrameBlockIds": [item.block.blockId for item in group.items],
    }
    return representative.block.model_copy(
        update={
            "startTime": start_time,
            "endTime": end_time,
            "sectionTitle": f"视频画面聚合 {start_time} - {end_time}",
            "contentText": content_text,
            "metadata": metadata,
        }
    )


def annotate_single_frame(
    block: DocumentBlock,
    document_id: str,
    *,
    info: FrameBlockInfo | None = None,
) -> DocumentBlock:
    """给未合并的 frame_ocr 也补齐去重元数据，便于查询阶段统一处理。"""
    info = info or build_frame_info(block)
    start_time = block.startTime or info.time_text
    source_frame_times = block_source_frame_times(block) or [info.time_text]
    time_ranges = block_time_ranges(block) or [{"startTime": info.time_text, "endTime": info.time_text}]
    metadata = {
        **block.metadata,
        "dedupStrategy": "video_frame_ocr_singleton",
        "duplicateGroupId": duplicate_group_id_for(document_id, info.normalized_hash, start_time),
        "contentHash": info.content_hash,
        "normalizedTextHash": info.normalized_hash,
        "representativeTime": info.time_text,
        "timeRanges": time_ranges,
        "sourceFrameTimes": source_frame_times,
        "visualTimeRanges": block_visual_time_ranges(block),
        "visualSourceFrameTimes": block_visual_source_frame_times(block),
        "mergedFrameCount": 1,
        "sourceFrameBlockIds": [block.blockId],
    }
    return block.model_copy(update={"metadata": metadata})


def build_frame_info(block: DocumentBlock) -> FrameBlockInfo:
    normalized = normalize_frame_text(block.contentText)
    time_text = block.startTime or str(block.metadata.get("frameTime") or "00:00:00")
    return FrameBlockInfo(
        block=block,
        normalized_text=normalized,
        content_hash=hash_text(block.contentText),
        normalized_hash=hash_text(normalized),
        seconds=timestamp_to_seconds(time_text),
        time_text=seconds_to_timestamp(timestamp_to_seconds(time_text)),
        slide_key=slide_group_key(block),
    )


def normalize_frame_text(text: str) -> str:
    """清理 OCR 文本中的画面标题和排版噪声，但保留代码与关键词。"""
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        stripped = line.strip()
        if not stripped or FRAME_HEADING_PATTERN.match(stripped):
            continue
        if stripped in {"```", "```python", "```text"}:
            continue
        lines.append(stripped)
    cleaned = " ".join(lines)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip().lower()


def strip_frame_heading(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and FRAME_HEADING_PATTERN.match(lines[0]):
        return "\n".join(lines[1:]).strip()
    if lines and lines[0].startswith("视频画面"):
        return "\n".join(lines[1:]).strip()
    return text.strip()


def slide_group_key(block: DocumentBlock) -> str | None:
    metadata = block.metadata or {}
    value = metadata.get("detectedSlideIndex")
    if value is None:
        value = block.slideIndex
    if value is None or value == "":
        return None
    return str(value)


def text_similarity(left: str, right: str) -> float:
    return max(jaccard(char_ngrams(left, 3), char_ngrams(right, 3)), jaccard(tokenize(left), tokenize(right)))


def char_ngrams(text: str, width: int) -> set[str]:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return set()
    if len(compact) <= width:
        return {compact}
    return {compact[index : index + width] for index in range(len(compact) - width + 1)}


def tokenize(text: str) -> set[str]:
    return set(TOKEN_PATTERN.findall(text.lower()))


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(len(left | right), 1)


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def duplicate_group_id_for(document_id: str, normalized_hash: str, start_time: str | None) -> str:
    start_seconds = timestamp_to_seconds(start_time)
    bucket = start_seconds // fallback_time_window_seconds()
    return f"{document_id}-frame-ocr-{normalized_hash[:12]}-{bucket}"


def merge_time_ranges(items: list[FrameBlockInfo]) -> list[dict[str, str]]:
    """只合并 OCR-confirmed timeRanges，不提升视觉重复时间。"""
    ranges: list[dict[str, str]] = []
    for item in items:
        ranges.extend(block_time_ranges(item.block) or [{"startTime": item.time_text, "endTime": item.time_text}])
    return unique_ranges(ranges)


def merge_source_frame_times(items: list[FrameBlockInfo]) -> list[str]:
    """只合并已 OCR 确认的 sourceFrameTimes。"""
    times: list[str] = []
    for item in items:
        times.extend(block_source_frame_times(item.block) or [item.time_text])
    return sorted(dict.fromkeys(times), key=timestamp_to_seconds)


def merge_visual_time_ranges(items: list[FrameBlockInfo]) -> list[dict[str, str]]:
    ranges: list[dict[str, str]] = []
    for item in items:
        ranges.extend(block_visual_time_ranges(item.block))
    return unique_ranges(ranges)


def merge_visual_source_frame_times(items: list[FrameBlockInfo]) -> list[str]:
    times: list[str] = []
    for item in items:
        times.extend(block_visual_source_frame_times(item.block))
    return sorted(dict.fromkeys(times), key=timestamp_to_seconds)


def block_time_ranges(block: DocumentBlock) -> list[dict[str, str]]:
    return normalize_ranges(block.metadata.get("timeRanges") if block.metadata else None)


def block_source_frame_times(block: DocumentBlock) -> list[str]:
    raw_times = block.metadata.get("sourceFrameTimes") if block.metadata else None
    if not isinstance(raw_times, list):
        return []
    return [seconds_to_timestamp(timestamp_to_seconds(str(item))) for item in raw_times if str(item).strip()]


def block_visual_time_ranges(block: DocumentBlock) -> list[dict[str, str]]:
    return normalize_ranges(block.metadata.get("visualTimeRanges") if block.metadata else None)


def block_visual_source_frame_times(block: DocumentBlock) -> list[str]:
    raw_times = block.metadata.get("visualSourceFrameTimes") if block.metadata else None
    if not isinstance(raw_times, list):
        return []
    return [seconds_to_timestamp(timestamp_to_seconds(str(item))) for item in raw_times if str(item).strip()]


def normalize_ranges(raw_ranges: Any) -> list[dict[str, str]]:
    ranges: list[dict[str, str]] = []
    if not isinstance(raw_ranges, list):
        return ranges
    for item in raw_ranges:
        if not isinstance(item, dict):
            continue
        start = seconds_to_timestamp(timestamp_to_seconds(str(item.get("startTime") or item.get("start") or "")))
        end = seconds_to_timestamp(timestamp_to_seconds(str(item.get("endTime") or item.get("end") or item.get("startTime") or "")))
        ranges.append({"startTime": start, "endTime": end})
    return ranges


def unique_ranges(ranges: list[dict[str, str]]) -> list[dict[str, str]]:
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in sorted(ranges, key=lambda value: (timestamp_to_seconds(value["startTime"]), timestamp_to_seconds(value["endTime"]))):
        key = (item["startTime"], item["endTime"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def build_stats(original_count: int, deduped_count: int, group_count: int, *, enabled: bool) -> dict[str, Any]:
    return {
        "dedupEnabled": enabled,
        "originalFrameCount": original_count,
        "dedupedFrameCount": deduped_count,
        "dedupGroupCount": group_count,
        "dedupRemovedCount": max(0, original_count - deduped_count),
        "dedupStrategy": "video_frame_ocr",
    }


def dedup_enabled() -> bool:
    return os.getenv("RAG_VIDEO_OCR_DEDUP_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def text_threshold() -> float:
    return float(os.getenv("RAG_VIDEO_OCR_DEDUP_TEXT_THRESHOLD", "0.86"))


def max_gap_seconds() -> int:
    return max(0, int(os.getenv("RAG_VIDEO_OCR_DEDUP_MAX_GAP_SECONDS", "180")))


def min_text_chars() -> int:
    return max(0, int(os.getenv("RAG_VIDEO_OCR_DEDUP_MIN_TEXT_CHARS", "30")))


def fallback_time_window_seconds() -> int:
    return max(30, int(os.getenv("RAG_QUERY_VIDEO_TIME_WINDOW_SECONDS", "120")))


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


def seconds_to_timestamp(seconds: int) -> str:
    safe_seconds = max(0, seconds)
    hours = safe_seconds // 3600
    minutes = (safe_seconds % 3600) // 60
    second = safe_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{second:02d}"
