from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


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


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

