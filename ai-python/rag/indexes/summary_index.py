from __future__ import annotations

from collections import defaultdict
from typing import Any

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

    @logged_rag_method("summary.index", "summary_index_build_parent_chunks", "生成父段摘要子块")
    def build_parent_summary_chunks(
        self,
        chunks: list[Chunk],
        *,
        document_id: str,
        start_position: int,
    ) -> list[Chunk]:
        """按父段生成可召回 summary child，供 BM25 和向量索引共同使用。"""
        grouped: dict[str, list[Chunk]] = defaultdict(list)
        parent_order: list[str] = []
        for chunk in chunks:
            metadata = chunk.metadata or {}
            if metadata.get("childKind") in {"summary", "video_segment_summary"}:
                continue
            parent_id = str(metadata.get("parentSegmentId") or "")
            if not parent_id:
                continue
            if parent_id not in grouped:
                parent_order.append(parent_id)
            grouped[parent_id].append(chunk)

        summary_chunks: list[Chunk] = []
        for index, parent_id in enumerate(parent_order, start=1):
            parent_chunks = grouped[parent_id]
            source_text = "\n".join(chunk.text for chunk in parent_chunks if chunk.text.strip())
            summary = self._summarize_text(source_text, max_chars=260)
            if not summary:
                continue
            first_metadata = dict(parent_chunks[0].metadata)
            metadata = self._summary_metadata(
                first_metadata,
                parent_chunks=parent_chunks,
                chunk_position=start_position + len(summary_chunks),
            )
            summary_chunks.append(
                Chunk(
                    chunk_id=f"{document_id}-summary-{index:04d}",
                    document_id=document_id,
                    text=f"父段摘要：{summary}",
                    metadata=metadata,
                )
            )
        process_event(
            stage="summary.index",
            action="summary_index_parent_chunks_result",
            message=f"父段摘要子块生成完成，共 {len(summary_chunks)} 块",
            context={"sourceChunkCount": len(chunks), "summaryChunkCount": len(summary_chunks)},
        )
        return summary_chunks

    def _summary_metadata(
        self,
        metadata: dict[str, Any],
        *,
        parent_chunks: list[Chunk],
        chunk_position: int,
    ) -> dict[str, Any]:
        """构造 summary child metadata，保留父段与来源子块关系。"""
        child_kinds = list(dict.fromkeys(str(chunk.metadata.get("childKind") or "raw") for chunk in parent_chunks))
        return {
            **metadata,
            "chunkPosition": chunk_position,
            "blockId": f"{metadata.get('parentSegmentId')}-summary",
            "blockType": "text",
            "childKind": "summary",
            "retrievalLayer": "child",
            "summarySourceChildIds": [chunk.chunk_id for chunk in parent_chunks],
            "summarySourceChildKinds": child_kinds,
        }

    def _summarize_text(self, text: str, max_chars: int) -> str:
        compact = " ".join(text.split())
        if len(compact) <= max_chars:
            return compact
        sentence_end = max(compact.rfind("。", 0, max_chars), compact.rfind(".", 0, max_chars))
        if sentence_end > 40:
            return compact[: sentence_end + 1]
        return compact[:max_chars].rstrip() + "..."
