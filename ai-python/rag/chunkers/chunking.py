from __future__ import annotations

import re
import hashlib
import os
from dataclasses import replace
from typing import Any

from rag.core.models import Chunk
from rag.observability.process_logger import logged_rag_method, process_event
from rag.core.text_sanitizer import clean_postgres_text, sanitize_for_postgres
from app.schemas.rag import DocumentBlock


ATOMIC_BLOCK_TYPES = {"table", "image", "chart", "formula", "code"}
VIDEO_PARENT_WINDOW_SECONDS = 60
TEXT_PARENT_WINDOW_CHUNKS = 3
SEGMENT_ROLE_VALUES = {
    "intro",
    "definition",
    "basic",
    "explanation",
    "example",
    "application",
    "derivation",
    "advanced",
    "review",
    "chitchat",
    "unknown",
}


class RecursiveChunker:
    def __init__(self, chunk_size: int = 700, overlap: int = 90) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.separators = ["\n## ", "\n### ", "\n\n", "\n", "\u3002", "\uff1b", ";", ".", "\uff0c", ",", " "]

    @logged_rag_method("chunk.recursive", "chunker_split_text", "按递归规则切分文本")
    def split(self, text: str, document_id: str, metadata: dict | None = None) -> list[Chunk]:
        metadata = sanitize_for_postgres(metadata or {})
        cleaned = self._normalize(text)
        raw_chunks = self._split_recursive(cleaned, self.separators)
        chunks: list[Chunk] = []
        current_section = clean_postgres_text(str(metadata.get("sectionName") or "\u5168\u6587"))
        text_parent_ids: dict[str, str] = {}
        for index, raw in enumerate(raw_chunks):
            chunk_text = raw.strip()
            if not chunk_text:
                continue
            heading = self._detect_heading(chunk_text)
            if heading:
                current_section = heading
            chunk_metadata = sanitize_for_postgres({
                **metadata,
                "chunkPosition": index,
                "sectionName": current_section,
            })
            chunk_metadata = self._with_parent_metadata(
                metadata=chunk_metadata,
                document_id=document_id,
                position=index,
                section_name=current_section,
                text_parent_ids=text_parent_ids,
            )
            chunks.append(
                Chunk(
                    chunk_id=f"{document_id}-{index}",
                    document_id=document_id,
                    text=chunk_text,
                    metadata=chunk_metadata,
                )
            )
        process_event(
            stage="chunk.recursive",
            action="chunker_split_text_result",
            message=f"文本递归切分完成，共 {len(chunks)} 块",
            context={"chunkCount": len(chunks), "textLength": len(cleaned)},
        )
        return chunks

    @logged_rag_method("chunk.recursive", "chunker_split_blocks", "按 DocumentBlock 递归切块")
    def split_blocks(
        self,
        blocks: list[DocumentBlock],
        document_id: str,
        metadata: dict | None = None,
    ) -> list[Chunk]:
        metadata = sanitize_for_postgres(metadata or {})
        chunks: list[Chunk] = []
        current_section = clean_postgres_text(str(metadata.get("sectionName") or "全文"))
        position = 0
        text_parent_ids: dict[str, str] = {}

        for block in blocks:
            block_text = self._normalize(block.contentText)
            if not block_text:
                continue

            if block.sectionTitle:
                current_section = clean_postgres_text(block.sectionTitle)
            if block.blockType == "heading":
                current_section = block_text.strip("# :：") or current_section

            block_metadata = sanitize_for_postgres({
                **metadata,
                **document_block_metadata(block),
                "sectionName": current_section,
            })
            if is_video_ocr_block(block):
                occurrence_chunks = self._split_video_ocr_occurrences(
                    block=block,
                    block_text=block_text,
                    block_metadata=block_metadata,
                    document_id=document_id,
                    start_position=position,
                )
                chunks.extend(occurrence_chunks)
                position += len(occurrence_chunks)
                continue
            if block.blockType in ATOMIC_BLOCK_TYPES:
                chunk_metadata = self._with_parent_metadata(
                    metadata={**block_metadata, "chunkPosition": position},
                    document_id=document_id,
                    position=position,
                    section_name=current_section,
                    text_parent_ids=text_parent_ids,
                )
                chunks.append(
                    Chunk(
                        chunk_id=f"{document_id}-{position}",
                        document_id=document_id,
                        text=block_text,
                        metadata=chunk_metadata,
                    )
                )
                position += 1
                continue

            raw_chunks = self._split_recursive(block_text, self.separators)
            for raw in raw_chunks:
                chunk_text = raw.strip()
                if not chunk_text:
                    continue
                heading = self._detect_heading(chunk_text)
                if heading:
                    current_section = heading
                    block_metadata = {**block_metadata, "sectionName": current_section}
                chunk_metadata = self._with_parent_metadata(
                    metadata={
                        **block_metadata,
                        "sectionName": current_section,
                        "chunkPosition": position,
                    },
                    document_id=document_id,
                    position=position,
                    section_name=current_section,
                    text_parent_ids=text_parent_ids,
                )
                chunks.append(
                    Chunk(
                        chunk_id=f"{document_id}-{position}",
                        document_id=document_id,
                        text=chunk_text,
                        metadata=chunk_metadata,
                    )
                )
                position += 1
        process_event(
            stage="chunk.recursive",
            action="chunker_split_blocks_result",
            message=f"DocumentBlock 递归切分完成，共 {len(chunks)} 块",
            context={"blockCount": len(blocks), "chunkCount": len(chunks)},
        )
        return chunks

    def _split_video_ocr_occurrences(
        self,
        *,
        block: DocumentBlock,
        block_text: str,
        block_metadata: dict[str, Any],
        document_id: str,
        start_position: int,
    ) -> list[Chunk]:
        """把聚合后的画面 OCR 按确认出现时间展开为 occurrence child。"""
        occurrence_times = extract_ocr_occurrence_times(block)
        chunks: list[Chunk] = []
        for offset, occurrence_time in enumerate(occurrence_times):
            position = start_position + offset
            occurrence_id = build_occurrence_id(document_id, block, occurrence_time, offset)
            metadata = {
                **block_metadata,
                "chunkPosition": position,
                "childKind": "ocr_occurrence",
                "occurrenceId": occurrence_id,
                "occurrenceTime": occurrence_time,
                "startTime": occurrence_time,
                "endTime": occurrence_time,
                "retrievalLayer": "child",
            }
            metadata = self._with_parent_metadata(
                metadata=metadata,
                document_id=document_id,
                position=position,
                section_name=str(block.sectionTitle or block_metadata.get("sectionName") or "视频画面"),
                text_parent_ids={},
                occurrence_time=occurrence_time,
            )
            chunks.append(
                Chunk(
                    chunk_id=f"{document_id}-{position}",
                    document_id=document_id,
                    text=render_occurrence_text(block_text, occurrence_time),
                    metadata=metadata,
                )
            )
        return chunks

    def _with_parent_metadata(
        self,
        *,
        metadata: dict[str, Any],
        document_id: str,
        position: int,
        section_name: str,
        text_parent_ids: dict[str, str],
        occurrence_time: str | None = None,
    ) -> dict[str, Any]:
        """为 raw/summary 前的子块补齐父段字段和检索层级。"""
        result = dict(metadata)
        child_kind = infer_child_kind(result)
        result["childKind"] = child_kind
        result["retrievalLayer"] = result.get("retrievalLayer") or "child"
        result["segmentRole"] = normalize_segment_role(result.get("segmentRole") or nested_metadata_value(result, "segmentRole"))
        linked_visual_ids = collect_linked_values(result, "visualGroupId", "suspectedVisualGroupId")
        if linked_visual_ids:
            result["linkedVisualGroupIds"] = linked_visual_ids
        linked_duplicate_ids = collect_linked_values(result, "duplicateGroupId", "frameDuplicateGroupIds")
        if linked_duplicate_ids:
            result["linkedDuplicateGroupIds"] = linked_duplicate_ids

        if is_video_metadata(result):
            parent = build_video_parent_metadata(document_id, result, occurrence_time=occurrence_time)
        else:
            parent = build_text_parent_metadata(document_id, result, position, section_name, text_parent_ids)
        return sanitize_for_postgres({**result, **parent})

    def _normalize(self, text: str) -> str:
        text = clean_postgres_text(text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _split_recursive(self, text: str, separators: list[str]) -> list[str]:
        if len(text) <= self.chunk_size:
            return [text] if text else []
        if not separators:
            return self._split_by_length(text)

        separator = separators[0]
        pieces = text.split(separator)
        if len(pieces) == 1:
            return self._split_recursive(text, separators[1:])

        chunks: list[str] = []
        current = ""
        for piece in pieces:
            candidate = piece if not current else current + separator + piece
            if len(candidate) <= self.chunk_size:
                current = candidate
                continue
            if current:
                chunks.extend(self._split_recursive(current, separators[1:]))
            current = piece
        if current:
            chunks.extend(self._split_recursive(current, separators[1:]))
        return self._add_overlap(chunks)

    def _split_by_length(self, text: str) -> list[str]:
        chunks = []
        start = 0
        step = max(1, self.chunk_size - self.overlap)
        while start < len(text):
            chunks.append(text[start : start + self.chunk_size])
            start += step
        return chunks

    def _add_overlap(self, chunks: list[str]) -> list[str]:
        if self.overlap <= 0 or len(chunks) <= 1:
            return chunks
        result = [chunks[0]]
        for previous, current in zip(chunks, chunks[1:]):
            prefix = previous[-self.overlap :]
            merged = prefix + current
            if len(merged) > self.chunk_size + self.overlap:
                result.extend(self._split_by_length(merged))
            else:
                result.append(merged)
        return result

    def _detect_heading(self, text: str) -> str | None:
        first_line = text.splitlines()[0].strip()
        markdown_heading = re.match(r"^#{1,6}\s+(.+)$", first_line)
        if markdown_heading:
            return markdown_heading.group(1).strip()
        if len(first_line) <= 32 and first_line.endswith(("\uff1a", ":")):
            return first_line.rstrip("\uff1a:")
        return None


def update_chunk_metadata(chunks: list[Chunk], metadata: dict) -> list[Chunk]:
    return [replace(chunk, metadata={**chunk.metadata, **metadata}) for chunk in chunks]


def document_block_metadata(block: DocumentBlock) -> dict:
    block_metadata = {
        "blockId": block.blockId,
        "fileType": block.fileType,
        "blockType": block.blockType,
        "pageIndex": block.pageIndex,
        "slideIndex": block.slideIndex,
        "startTime": block.startTime,
        "endTime": block.endTime,
        "sheetName": block.sheetName,
        "cellRange": block.cellRange,
        "sectionTitle": block.sectionTitle,
        "assetPath": block.assetPath,
        "bbox": block.bbox,
        "parseEngine": block.parseEngine,
        "confidence": block.confidence,
        "sourceTitle": block.sourceTitle,
        "sourcePath": block.sourcePath,
        "blockMetadata": block.metadata,
    }
    promoted_keys = (
        "mediaType",
        "evidenceChannel",
        "videoUrl",
        "playbackUrl",
        "mediaUrl",
        "sourceVideoUrl",
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
        "parentSegmentId",
        "parentStartTime",
        "parentEndTime",
        "parentKind",
        "childKind",
        "occurrenceId",
        "occurrenceTime",
        "retrievalLayer",
        "concepts",
        "segmentRole",
        "prerequisiteSegmentIds",
        "relatedSegmentIds",
        "matchedChildIds",
        "matchedChildKinds",
        "linkedVisualGroupIds",
        "linkedDuplicateGroupIds",
    )
    for key in promoted_keys:
        value = block.metadata.get(key)
        if value is not None and value != "":
            block_metadata[key] = value
    return block_metadata


def is_video_ocr_block(block: DocumentBlock) -> bool:
    """判断解析块是否需要按 OCR 出现时间展开。"""
    metadata = block.metadata or {}
    return metadata.get("evidenceChannel") == "frame_ocr" and bool(
        metadata.get("sourceFrameTimes") or metadata.get("timeRanges") or block.startTime
    )


def extract_ocr_occurrence_times(block: DocumentBlock) -> list[str]:
    """读取 OCR-confirmed 时间点，去重后按时间排序。"""
    metadata = block.metadata or {}
    times: list[str] = []
    raw_times = metadata.get("sourceFrameTimes")
    if isinstance(raw_times, list):
        times.extend(str(item) for item in raw_times if str(item).strip())
    raw_ranges = metadata.get("timeRanges")
    if isinstance(raw_ranges, list):
        for item in raw_ranges:
            if not isinstance(item, dict):
                continue
            value = item.get("startTime") or item.get("start") or item.get("endTime") or item.get("end")
            if value:
                times.append(str(value))
    if block.startTime:
        times.append(block.startTime)
    normalized = [seconds_to_timestamp(timestamp_to_seconds(value)) for value in times]
    return sorted(dict.fromkeys(normalized), key=timestamp_to_seconds) or ["00:00:00"]


def build_occurrence_id(document_id: str, block: DocumentBlock, occurrence_time: str, offset: int) -> str:
    """生成稳定 occurrenceId，避免同一 OCR 内容跨时段被折叠。"""
    metadata = block.metadata or {}
    basis = "|".join(
        [
            document_id,
            str(metadata.get("duplicateGroupId") or metadata.get("normalizedTextHash") or block.blockId),
            occurrence_time,
            str(offset),
        ]
    )
    digest = hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]
    return f"{document_id}-ocr-occurrence-{timestamp_to_seconds(occurrence_time)}-{digest}"


def render_occurrence_text(block_text: str, occurrence_time: str) -> str:
    """给 occurrence child 增加明确时间上下文。"""
    stripped = strip_repeated_time_line(block_text)
    return f"OCR 出现时间：{occurrence_time}\n{stripped}".strip()


def strip_repeated_time_line(text: str) -> str:
    """去掉聚合块里过长的重复出现时间行，降低向量化噪声。"""
    lines = []
    for line in text.splitlines():
        if line.strip().startswith("重复出现时间："):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def infer_child_kind(metadata: dict[str, Any]) -> str:
    """根据 evidenceChannel/blockType 推断子块类型。"""
    configured = metadata.get("childKind")
    if configured:
        return str(configured)
    channel = metadata.get("evidenceChannel")
    if channel == "video_segment_summary":
        return "video_segment_summary"
    if channel == "frame_ocr":
        return "ocr_occurrence" if metadata.get("occurrenceId") else "raw"
    return "raw"


def normalize_segment_role(value: Any) -> str:
    """规范化父段角色枚举，未知值统一落到 unknown。"""
    if value is None or value == "":
        return "unknown"
    normalized = str(value).strip().lower()
    return normalized if normalized in SEGMENT_ROLE_VALUES else "unknown"


def nested_metadata_value(metadata: dict[str, Any], key: str) -> Any:
    nested = metadata.get("blockMetadata")
    if isinstance(nested, dict):
        return nested.get(key)
    return None


def collect_linked_values(metadata: dict[str, Any], *keys: str) -> list[str]:
    """把视觉组或重复组字段规范为去重字符串列表。"""
    values: list[str] = []
    for key in keys:
        value = metadata.get(key)
        if value is None:
            nested = metadata.get("blockMetadata")
            value = nested.get(key) if isinstance(nested, dict) else None
        if isinstance(value, list):
            values.extend(str(item) for item in value if str(item).strip())
        elif value is not None and str(value).strip():
            values.append(str(value))
    return list(dict.fromkeys(values))


def is_video_metadata(metadata: dict[str, Any]) -> bool:
    """判断 chunk metadata 是否属于视频 evidence。"""
    if metadata.get("mediaType") == "video":
        return True
    if metadata.get("evidenceChannel") in {"subtitle", "frame_ocr", "video_segment_summary", "video_metadata"}:
        return True
    return bool(metadata.get("startTime") or metadata.get("occurrenceTime"))


def build_video_parent_metadata(
    document_id: str,
    metadata: dict[str, Any],
    *,
    occurrence_time: str | None,
) -> dict[str, Any]:
    """按视频时间窗口构建父段 metadata。"""
    start_time = occurrence_time or str(metadata.get("startTime") or metadata.get("parentStartTime") or "00:00:00")
    end_time = str(metadata.get("endTime") or start_time)
    start_seconds = timestamp_to_seconds(start_time)
    end_seconds = max(start_seconds, timestamp_to_seconds(end_time))
    window = video_parent_window_seconds()
    parent_start_seconds = (start_seconds // window) * window
    parent_end_seconds = max(parent_start_seconds + window, end_seconds)
    parent_start = seconds_to_timestamp(parent_start_seconds)
    parent_end = seconds_to_timestamp(parent_end_seconds)
    parent_id = metadata.get("parentSegmentId") or f"{document_id}-parent-video-{parent_start_seconds:06d}-{parent_end_seconds:06d}"
    return {
        "parentSegmentId": parent_id,
        "parentStartTime": metadata.get("parentStartTime") or parent_start,
        "parentEndTime": metadata.get("parentEndTime") or parent_end,
        "parentKind": metadata.get("parentKind") or "video_segment",
    }


def build_text_parent_metadata(
    document_id: str,
    metadata: dict[str, Any],
    position: int,
    section_name: str,
    text_parent_ids: dict[str, str],
) -> dict[str, Any]:
    """按标题章节或段落窗口构建文本父段 metadata。"""
    existing_parent = metadata.get("parentSegmentId")
    if existing_parent:
        return {
            "parentSegmentId": existing_parent,
            "parentStartTime": metadata.get("parentStartTime"),
            "parentEndTime": metadata.get("parentEndTime"),
            "parentKind": metadata.get("parentKind") or "text_section",
        }
    section = section_name.strip() or str(metadata.get("sectionName") or "全文")
    has_heading = section and section != "全文"
    if has_heading:
        parent_id = text_parent_ids.setdefault(section, f"{document_id}-parent-text-{stable_slug(section)}")
        parent_kind = "text_section"
    else:
        window = position // text_parent_window_chunks()
        parent_id = f"{document_id}-parent-text-window-{window:04d}"
        parent_kind = "text_window"
    return {
        "parentSegmentId": parent_id,
        "parentStartTime": None,
        "parentEndTime": None,
        "parentKind": parent_kind,
    }


def stable_slug(value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
    return digest


def video_parent_window_seconds() -> int:
    return max(30, int(os.getenv("RAG_PARENT_VIDEO_WINDOW_SECONDS", str(VIDEO_PARENT_WINDOW_SECONDS))))


def text_parent_window_chunks() -> int:
    return max(1, int(os.getenv("RAG_PARENT_TEXT_WINDOW_CHUNKS", str(TEXT_PARENT_WINDOW_CHUNKS))))


def timestamp_to_seconds(value: str | None) -> int:
    """将 HH:MM:SS 或 MM:SS 时间戳转为秒数。"""
    if not value:
        return 0
    try:
        parts = [int(part) for part in str(value).replace(",", ".").split(".", 1)[0].split(":")]
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
    """将秒数格式化为 HH:MM:SS。"""
    seconds = max(0, int(seconds))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remain = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{remain:02d}"
