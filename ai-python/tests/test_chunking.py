from rag.chunking import RecursiveChunker


def test_recursive_chunker_keeps_metadata_and_overlap():
    text = "## Spring\n" + "自动配置依赖条件注解。" * 80 + "\n\n## RAG\n" + "递归切块保留段落结构。" * 80
    chunker = RecursiveChunker(chunk_size=180, overlap=20)

    chunks = chunker.split(text, document_id="doc-1", metadata={"title": "测试文档"})

    assert len(chunks) > 2
    assert chunks[0].document_id == "doc-1"
    assert chunks[0].metadata["title"] == "测试文档"
    lengths = [len(chunk.text) for chunk in chunks]
    assert max(lengths) <= 220, lengths
