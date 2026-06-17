from rag.chunkers.chunking import RecursiveChunker


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
