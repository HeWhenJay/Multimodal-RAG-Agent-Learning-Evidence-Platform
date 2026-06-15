from __future__ import annotations

from collections import defaultdict

from rag.models import Chunk


class SummaryIndex:
    def build(self, chunks: list[Chunk]) -> dict:
        by_section: dict[str, list[str]] = defaultdict(list)
        for chunk in chunks:
            section = str(chunk.metadata.get("sectionName") or "全文")
            by_section[section].append(chunk.text)

        section_summaries = {
            section: self._summarize_text("\n".join(texts), max_chars=180)
            for section, texts in by_section.items()
        }
        document_summary = self._summarize_text("\n".join(chunk.text for chunk in chunks), max_chars=260)
        return {
            "documentSummary": document_summary,
            "sectionSummaries": section_summaries,
        }

    def _summarize_text(self, text: str, max_chars: int) -> str:
        compact = " ".join(text.split())
        if len(compact) <= max_chars:
            return compact
        sentence_end = max(compact.rfind("。", 0, max_chars), compact.rfind(".", 0, max_chars))
        if sentence_end > 40:
            return compact[: sentence_end + 1]
        return compact[:max_chars].rstrip() + "..."

