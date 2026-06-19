from app.schemas.rag import DocumentBlock
from rag.chunkers.chunking import RecursiveChunker
from rag.indexes.summary_index import SummaryIndex


def assert_no_postgres_nul(value):
    """递归确认测试数据中没有真实 NUL 字符。"""
    nul = chr(0)
    if isinstance(value, str):
        assert nul not in value
    elif isinstance(value, dict):
        for key, item in value.items():
            assert_no_postgres_nul(key)
            assert_no_postgres_nul(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            assert_no_postgres_nul(item)


def test_recursive_chunker_keeps_metadata_and_overlap():
    text = "## Spring\n" + "自动配置依赖条件注解。" * 80 + "\n\n## RAG\n" + "递归切块保留段落结构。" * 80
    chunker = RecursiveChunker(chunk_size=180, overlap=20)

    chunks = chunker.split(text, document_id="doc-1", metadata={"title": "测试文档"})

    assert len(chunks) > 2
    assert chunks[0].document_id == "doc-1"
    assert chunks[0].metadata["title"] == "测试文档"
    lengths = [len(chunk.text) for chunk in chunks]
    assert max(lengths) <= 220, lengths


def test_recursive_chunker_removes_postgres_nul_from_text_and_metadata():
    nul = chr(0)
    chunker = RecursiveChunker(chunk_size=80, overlap=10)

    chunks = chunker.split(
        f"第一段{nul}包含空字符。\n\n第二段继续。",
        document_id="doc-nul",
        metadata={"title": f"标题{nul}", "sectionName": f"章节{nul}"},
    )

    assert chunks
    assert all(nul not in chunk.text for chunk in chunks)
    for chunk in chunks:
        assert_no_postgres_nul(chunk.metadata)


def test_markdown_heading_parents_and_summary_children_have_parent_segment_id():
    """Markdown 两个 heading 应生成不同父段，raw/summary child 都绑定 parentSegmentId。"""
    blocks = [
        DocumentBlock(
            documentId="doc-md-parent",
            blockId="doc-md-parent-h1",
            fileType="md",
            blockType="heading",
            sectionTitle="RAG 基础",
            contentText="## RAG 基础",
            parseEngine="unit-markdown",
            sourceTitle="父子索引笔记",
        ),
        DocumentBlock(
            documentId="doc-md-parent",
            blockId="doc-md-parent-p1",
            fileType="md",
            blockType="text",
            sectionTitle="RAG 基础",
            contentText="RAG 需要先加载资料，再递归切块和向量化。",
            parseEngine="unit-markdown",
            sourceTitle="父子索引笔记",
        ),
        DocumentBlock(
            documentId="doc-md-parent",
            blockId="doc-md-parent-h2",
            fileType="md",
            blockType="heading",
            sectionTitle="检索融合",
            contentText="## 检索融合",
            parseEngine="unit-markdown",
            sourceTitle="父子索引笔记",
        ),
        DocumentBlock(
            documentId="doc-md-parent",
            blockId="doc-md-parent-p2",
            fileType="md",
            blockType="text",
            sectionTitle="检索融合",
            contentText="BM25 和向量检索可以用 RRF 做 RAG-Fusion。",
            parseEngine="unit-markdown",
            sourceTitle="父子索引笔记",
        ),
    ]
    chunker = RecursiveChunker(chunk_size=120, overlap=0)

    chunks = chunker.split_blocks(blocks, document_id="doc-md-parent", metadata={"title": "父子索引笔记"})
    summary_chunks = SummaryIndex().build_parent_summary_chunks(
        chunks,
        document_id="doc-md-parent",
        start_position=len(chunks),
    )

    raw_parent_ids = {chunk.metadata["parentSegmentId"] for chunk in chunks if chunk.metadata["childKind"] == "raw"}
    assert len(raw_parent_ids) == 2
    assert all(chunk.metadata["parentKind"] == "text_section" for chunk in chunks)
    assert summary_chunks
    assert {chunk.metadata["childKind"] for chunk in summary_chunks} == {"summary"}
    assert {chunk.metadata["parentSegmentId"] for chunk in summary_chunks} == raw_parent_ids


def test_plain_text_without_heading_falls_back_to_parent_windows(monkeypatch):
    """无 heading 文本应按段落窗口退化为父段，避免缺失 parentSegmentId。"""
    monkeypatch.setenv("RAG_PARENT_TEXT_WINDOW_CHUNKS", "2")
    text = "\n\n".join([f"第 {index} 段说明递归切块和 metadata 过滤。" for index in range(1, 6)])
    chunker = RecursiveChunker(chunk_size=40, overlap=0)

    chunks = chunker.split(text, document_id="doc-window", metadata={"title": "无标题资料"})

    parent_ids = [chunk.metadata["parentSegmentId"] for chunk in chunks]
    assert len(set(parent_ids)) >= 2
    assert all(chunk.metadata["parentKind"] == "text_window" for chunk in chunks)
    assert all(chunk.metadata["childKind"] == "raw" for chunk in chunks)


def test_video_ocr_time_ranges_expand_to_occurrence_children(monkeypatch):
    """视频重复 OCR 在不同时刻应展开为不同 occurrence，并挂到各自父段。"""
    monkeypatch.setenv("RAG_PARENT_VIDEO_WINDOW_SECONDS", "60")
    block = DocumentBlock(
        documentId="doc-video-occurrence",
        blockId="doc-video-occurrence-frame",
        fileType="mp4",
        blockType="image",
        startTime="00:00:10",
        endTime="00:01:30",
        sectionTitle="视频画面聚合 00:00:10 - 00:01:30",
        contentText="视频画面聚合 00:00:10 - 00:01:30\nRAG-Fusion 使用 RRF 融合检索结果。\n重复出现时间：00:00:10、00:01:30",
        parseEngine="video-frame-ocr",
        sourceTitle="RAG 课程视频",
        metadata={
            "mediaType": "video",
            "evidenceChannel": "frame_ocr",
            "duplicateGroupId": "doc-video-occurrence-frame-ocr-group",
            "sourceFrameTimes": ["00:00:10", "00:01:30"],
            "timeRanges": [
                {"startTime": "00:00:10", "endTime": "00:00:10"},
                {"startTime": "00:01:30", "endTime": "00:01:30"},
            ],
        },
    )

    chunks = RecursiveChunker(chunk_size=300, overlap=0).split_blocks(
        [block],
        document_id="doc-video-occurrence",
        metadata={"title": "RAG 课程视频", "documentType": "mp4"},
    )

    assert len(chunks) == 2
    assert {chunk.metadata["childKind"] for chunk in chunks} == {"ocr_occurrence"}
    assert len({chunk.metadata["occurrenceId"] for chunk in chunks}) == 2
    assert {chunk.metadata["occurrenceTime"] for chunk in chunks} == {"00:00:10", "00:01:30"}
    assert len({chunk.metadata["parentSegmentId"] for chunk in chunks}) == 2
