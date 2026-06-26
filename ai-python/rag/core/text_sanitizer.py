from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.schemas.rag import DocumentBlock, ParseQuality
from rag.core.models import Chunk


POSTGRES_NUL = "\x00"


def clean_postgres_text(value: str) -> str:
    """清理 PostgreSQL 文本字段无法保存的 NUL 字符。"""
    return value.replace(POSTGRES_NUL, "")


def sanitize_for_postgres(value: Any) -> Any:
    """递归清理即将进入 TEXT/VARCHAR/JSONB 的字符串值。"""
    if isinstance(value, str):
        return clean_postgres_text(value)
    if isinstance(value, dict):
        return {
            sanitize_for_postgres(key): sanitize_for_postgres(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_for_postgres(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_for_postgres(item) for item in value)
    return value


def sanitize_parse_quality(quality: ParseQuality) -> ParseQuality:
    """清理解析质量消息，避免 warning 文本进入 JSONB 时报错。"""
    messages = [clean_postgres_text(message) for message in quality.messages]
    return quality.model_copy(update={"messages": messages})


def sanitize_document_blocks(blocks: list[DocumentBlock]) -> list[DocumentBlock]:
    """清理解析块中的文本字段和元数据。"""
    string_fields = (
        "documentId",
        "blockId",
        "fileType",
        "blockType",
        "startTime",
        "endTime",
        "sheetName",
        "cellRange",
        "sectionTitle",
        "contentText",
        "contentHtml",
        "assetPath",
        "parseEngine",
        "sourceTitle",
        "sourcePath",
    )
    cleaned_blocks: list[DocumentBlock] = []
    for block in blocks:
        updates: dict[str, Any] = {}
        for field_name in string_fields:
            value = getattr(block, field_name)
            if isinstance(value, str):
                cleaned = clean_postgres_text(value)
                if cleaned != value:
                    updates[field_name] = cleaned
        metadata = sanitize_for_postgres(block.metadata)
        if metadata != block.metadata:
            updates["metadata"] = metadata
        cleaned_blocks.append(block.model_copy(update=updates) if updates else block)
    return cleaned_blocks


def sanitize_chunks(chunks: list[Chunk]) -> list[Chunk]:
    """清理切块结果，作为入库前的最后一道保护。"""
    cleaned_chunks: list[Chunk] = []
    for chunk in chunks:
        cleaned_chunk_id = clean_postgres_text(chunk.chunk_id)
        cleaned_document_id = clean_postgres_text(chunk.document_id)
        cleaned_text = clean_postgres_text(chunk.text)
        cleaned_metadata = sanitize_for_postgres(chunk.metadata)
        if (
            cleaned_chunk_id != chunk.chunk_id
            or cleaned_document_id != chunk.document_id
            or cleaned_text != chunk.text
            or cleaned_metadata != chunk.metadata
        ):
            cleaned_chunks.append(
                replace(
                    chunk,
                    chunk_id=cleaned_chunk_id,
                    document_id=cleaned_document_id,
                    text=cleaned_text,
                    metadata=cleaned_metadata,
                )
            )
        else:
            cleaned_chunks.append(chunk)
    return cleaned_chunks
