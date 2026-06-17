from rag.document_parsers import DocumentParserRouter
from rag.retrieval import InMemoryRagStore
from rag.video_processing import FrameImage, build_video_segment_summary_blocks, select_ppt_slide_frames
from schemas.rag import DocumentBlock


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
    assert any(message.startswith("video.audio.extract:") for message in parsed.parse_quality.messages)
    assert any(message.startswith("video.frame.extract:") for message in parsed.parse_quality.messages)
    assert any(message.startswith("video.fallback:") for message in parsed.parse_quality.messages)


# 校验 PPT 翻页检测会保留画面变化明显的候选帧。
def test_ppt_flip_detection_selects_changed_frame(tmp_path, monkeypatch):
    from PIL import Image

    monkeypatch.setenv("RAG_VIDEO_PPT_FLIP_DIFF_THRESHOLD", "0.05")
    first = tmp_path / "frame-0001.jpg"
    similar = tmp_path / "frame-0002.jpg"
    changed = tmp_path / "frame-0003.jpg"
    Image.new("RGB", (120, 80), "white").save(first)
    Image.new("RGB", (120, 80), (248, 248, 248)).save(similar)
    Image.new("RGB", (120, 80), "black").save(changed)

    selected, warnings = select_ppt_slide_frames(
        [
            FrameImage(time_seconds=0, path=first),
            FrameImage(time_seconds=5, path=similar),
            FrameImage(time_seconds=10, path=changed),
        ],
        keep_interval_seconds=60,
        max_frames=3,
    )

    assert warnings == []
    assert [frame.trigger for frame in selected] == ["initial_slide", "ppt_flip"]
    assert selected[1].slide_index == 2
    assert selected[1].diff_score is not None and selected[1].diff_score >= 0.05


# 校验视频片段摘要会合并字幕证据和关键帧 OCR 证据。
def test_video_segment_summary_combines_subtitle_and_frame_ocr():
    transcript_block = DocumentBlock(
        documentId="doc-video-summary",
        blockId="doc-video-summary-subtitle-1",
        fileType="mp4",
        blockType="text",
        startTime="00:00:05",
        endTime="00:00:35",
        sectionTitle="00:00:05 - 00:00:35",
        contentText="这一段讲 RAG-Fusion 如何融合 BM25 和向量召回结果。",
        parseEngine="bailian-asr-transcript",
        sourceTitle="RAG 课程视频",
        sourcePath="https://example.com/rag-course.mp4",
        metadata={"mediaType": "video", "evidenceChannel": "subtitle"},
    )
    frame_block = DocumentBlock(
        documentId="doc-video-summary",
        blockId="doc-video-summary-frame-1",
        fileType="mp4",
        blockType="image",
        slideIndex=1,
        startTime="00:00:10",
        sectionTitle="视频画面 00:00:10",
        contentText="视频画面 00:00:10\nPPT 标题：RAG-Fusion 检索流程",
        parseEngine="bailian-qwen-ocr",
        sourceTitle="RAG 课程视频",
        sourcePath="https://example.com/rag-course.mp4",
        metadata={"mediaType": "video", "evidenceChannel": "frame_ocr"},
    )

    summary_blocks, warnings = build_video_segment_summary_blocks(
        document_id="doc-video-summary",
        file_type="mp4",
        source_title="RAG 课程视频",
        source_path="https://example.com/rag-course.mp4",
        transcript_blocks=[transcript_block],
        frame_blocks=[frame_block],
    )

    assert warnings == []
    assert len(summary_blocks) == 1
    summary = summary_blocks[0]
    assert summary.metadata["evidenceChannel"] == "video_segment_summary"
    assert summary.metadata["sourceBlockIds"] == ["doc-video-summary-subtitle-1"]
    assert summary.metadata["frameBlockIds"] == ["doc-video-summary-frame-1"]
    assert summary.metadata["videoUrl"] == "https://example.com/rag-course.mp4"
    assert "字幕要点" in summary.contentText
    assert "画面线索" in summary.contentText
