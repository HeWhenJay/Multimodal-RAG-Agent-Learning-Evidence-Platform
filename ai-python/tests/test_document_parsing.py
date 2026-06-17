from rag.document_parsers import DocumentParserRouter
from rag.retrieval import InMemoryRagStore


def test_markdown_file_routes_to_document_blocks():
    parser = DocumentParserRouter()

    parsed = parser.parse_bytes(
        content=(
            "# RAG 入库\n\n"
            "原始文件先转换为 DocumentBlock。\n\n"
            "| 阶段 | 说明 |\n"
            "| --- | --- |\n"
            "| 解析 | 保留结构 |\n"
        ).encode("utf-8"),
        filename="rag-note.md",
        document_id="doc-block",
        source_title="RAG 入库说明",
        document_type="markdown",
        source_path="uploads/rag/rag-note.md",
    )

    assert parsed.status == "READY"
    assert any(block.blockType == "heading" for block in parsed.blocks)
    assert any(block.blockType == "table" for block in parsed.blocks)
    assert all(block.documentId == "doc-block" for block in parsed.blocks)


def test_index_blocks_preserves_evidence_metadata():
    parser = DocumentParserRouter()
    parsed = parser.parse_text(
        document_id="doc-evidence",
        title="证据结构",
        document_type="markdown",
        source_path="uploads/rag/evidence.md",
        content="## Evidence\nRAG 检索必须返回 blockId、sectionTitle 和 sourcePath。",
        parser="unit-test",
    )
    store = InMemoryRagStore()

    store.index_blocks(
        document_id="doc-evidence",
        title="证据结构",
        document_type="markdown",
        source="unit-test",
        user_id="unit-user",
        visibility_scope="private",
        language="zh-CN",
        parser=parsed.parser,
        blocks=parsed.blocks,
        parse_quality=parsed.parse_quality,
        status=parsed.status,
        source_path="uploads/rag/evidence.md",
    )
    evidences = store.list_evidences("doc-evidence", limit=5)

    assert evidences
    assert evidences[0].documentTitle == "证据结构"
    assert evidences[0].blockId
    assert evidences[0].sectionTitle
    assert evidences[0].sourcePath == "uploads/rag/evidence.md"


def test_subtitle_file_preserves_video_timestamp_metadata():
    parser = DocumentParserRouter()
    parsed = parser.parse_bytes(
        content=(
            "1\n"
            "01:23:10,000 --> 01:25:42,000\n"
            "这里讲到了 RAG-Fusion、Multi-Query 和 RRF 融合排序。\n"
        ).encode("utf-8"),
        filename="course-rag.srt",
        document_id="doc-video",
        source_title="某课程视频",
        document_type="srt",
        source_path="uploads/rag/course-rag.srt",
    )

    assert parsed.status == "READY"
    assert parsed.blocks[0].startTime == "01:23:10"
    assert parsed.blocks[0].endTime == "01:25:42"
    assert parsed.blocks[0].metadata["evidenceChannel"] == "subtitle"


def test_subtitle_file_preserves_video_url_header():
    parser = DocumentParserRouter()
    parsed = parser.parse_bytes(
        content=(
            "videoUrl: https://example.com/course.mp4\n\n"
            "1\n"
            "01:23:10,000 --> 01:25:42,000\n"
            "这里讲到了 RAG-Fusion。\n"
        ).encode("utf-8"),
        filename="course-rag.srt",
        document_id="doc-video-url",
        source_title="某课程视频",
        document_type="srt",
        source_path="uploads/rag/course-rag.srt",
    )

    assert parsed.blocks[0].metadata["videoUrl"] == "https://example.com/course.mp4"


def test_raw_video_file_creates_traceable_partial_metadata(monkeypatch):
    monkeypatch.setenv("RAG_ASR_PROVIDER", "local")
    parser = DocumentParserRouter()

    parsed = parser.parse_bytes(
        content=b"not-a-real-video",
        filename="course-rag.mp4",
        document_id="doc-raw-video",
        source_title="课程原始视频",
        document_type="mp4",
        source_path="https://example.com/course-rag.mp4",
    )

    assert parsed.status == "PARTIAL"
    assert parsed.blocks
    assert parsed.blocks[0].metadata["mediaType"] == "video"
    assert parsed.blocks[0].metadata["evidenceChannel"] == "video_metadata"
    assert parsed.blocks[0].metadata["videoUrl"] == "https://example.com/course-rag.mp4"
