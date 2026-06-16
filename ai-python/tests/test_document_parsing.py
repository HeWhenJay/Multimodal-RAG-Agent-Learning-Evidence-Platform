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
        user_id="demo-user",
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
