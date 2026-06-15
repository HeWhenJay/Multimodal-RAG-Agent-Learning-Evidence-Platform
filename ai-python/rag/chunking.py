from __future__ import annotations

import re
from dataclasses import replace

from rag.models import Chunk


class RecursiveChunker:
    def __init__(self, chunk_size: int = 700, overlap: int = 90) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.separators = ["\n## ", "\n### ", "\n\n", "\n", "\u3002", "\uff1b", ";", ".", "\uff0c", ",", " "]

    def split(self, text: str, document_id: str, metadata: dict | None = None) -> list[Chunk]:
        metadata = metadata or {}
        cleaned = self._normalize(text)
        raw_chunks = self._split_recursive(cleaned, self.separators)
        chunks: list[Chunk] = []
        current_section = metadata.get("sectionName") or "\u5168\u6587"
        for index, raw in enumerate(raw_chunks):
            chunk_text = raw.strip()
            if not chunk_text:
                continue
            heading = self._detect_heading(chunk_text)
            if heading:
                current_section = heading
            chunk_metadata = {
                **metadata,
                "chunkPosition": index,
                "sectionName": current_section,
            }
            chunks.append(
                Chunk(
                    chunk_id=f"{document_id}-{index}",
                    document_id=document_id,
                    text=chunk_text,
                    metadata=chunk_metadata,
                )
            )
        return chunks

    def _normalize(self, text: str) -> str:
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

