from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.schemas.rag import DocumentBlock, ParseQuality


@dataclass(frozen=True)
class ParsedDocument:
    text: str
    parser: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ParsedBlockDocument:
    blocks: list[DocumentBlock]
    parser: str
    status: str
    parse_quality: ParseQuality
    document_summary: str = ""
    section_summaries: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
