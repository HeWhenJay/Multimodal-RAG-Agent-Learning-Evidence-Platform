from __future__ import annotations

from collections import defaultdict

from rag.models import Chunk
from rag.process_logger import logged_rag_method, process_event


class SummaryIndex:
    @logged_rag_method("summary.index", "summary_index_build", "生成文档摘要和章节摘要")
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
        process_event(
            stage="summary.index",
            action="summary_index_build_result",
            message="摘要索引生成完成",
            context={"chunkCount": len(chunks), "sectionCount": len(section_summaries)},
        )
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
