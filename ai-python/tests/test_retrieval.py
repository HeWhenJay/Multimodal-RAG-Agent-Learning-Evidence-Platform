from rag.retrievers.retrieval import cached_embedding, embed_text, embedding_provider_name
from rag.retrievers.retrieval import InMemoryRagStore
from rag.progress import RagProgressReporter
from rag.bailian_llm import append_evidence_reference_summary, deterministic_grounded_answer
from rag.indexes.pgvector_store import build_filter_clause, vector_literal
from rag.rerankers.reranking import local_rerank
from rag.loaders.parse_quality import QualitySignals, evaluate_parse_quality
from app.schemas.rag import DocumentBlock, IndexTextRequest, QueryRequest


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


def test_rag_store_indexes_and_queries_with_evidence():
    store = InMemoryRagStore()
    store.index_text(
        IndexTextRequest(
            documentId="doc-spring",
            title="Spring Boot 学习笔记",
            documentType="markdown",
            source="unit-test",
            userId="unit-user",
            content="## 自动配置\nSpring Boot 自动配置通过条件注解和 starter 降低配置成本。\n## 事务\n事务需要关注传播行为。",
        )
    )

    response = store.query(QueryRequest(question="Spring Boot 自动配置如何工作？", topK=3))

    assert response.evidences
    assert response.evidences[0].documentId == "doc-spring"
    assert "自动配置" in response.answer
    assert response.evidences[0].retrievalSource == "rerank"
    assert response.diagnostics["answerProvider"] == "local"
    assert response.diagnostics["rerankProvider"] == "local"
    assert len(response.expandedQueries) >= 3


def test_query_progress_reporter_streams_multi_query_details():
    store = InMemoryRagStore()
    store.index_text(
        IndexTextRequest(
            documentId="doc-query-progress",
            title="查询进度笔记",
            documentType="markdown",
            source="unit-test",
            userId="unit-user",
            content="## 鸡蛋做法\n鸡蛋可以水煮、煎蛋、炒蛋，也可以做蒸蛋。",
        )
    )
    streamed_events = []
    reporter = RagProgressReporter(document_id="query", persist=False, on_emit=streamed_events.append)

    response = store.query(
        QueryRequest(question="鸡蛋怎么做", topK=2, metadataFilter={"userId": "unit-user"}),
        progress_reporter=reporter,
    )

    expand_events = [event for event in streamed_events if event.stageCode == "query.expand"]
    bm25_events = [event for event in streamed_events if event.stageCode == "query.bm25" and event.status == "COMPLETED"]
    assert response.progressEvents == streamed_events
    assert expand_events[-1].detail is not None
    assert "鸡蛋怎么做 学习资料 笔记" in expand_events[-1].detail
    assert len(bm25_events) == len(response.expandedQueries)


def test_pgvector_filter_clause_supports_columns_and_metadata():
    where_sql, params = build_filter_clause(
        {
            "documentType": "markdown",
            "sectionName": ["自动配置", "事务"],
            "customTag": "spring",
        }
    )

    assert "d.document_type = %s" in where_sql
    assert "c.section_name IN (%s, %s)" in where_sql
    assert "c.metadata ->> %s = %s" in where_sql
    assert params == ["markdown", "自动配置", "事务", "customTag", "spring"]


def test_vector_literal_matches_pgvector_input_format():
    assert vector_literal([0.1, -0.25, 1.0]) == "[0.10000000,-0.25000000,1.00000000]"


def test_embedding_defaults_to_1024_dimensions(monkeypatch):
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "hash")
    monkeypatch.setenv("RAG_VECTOR_DIMENSIONS", "1024")
    cached_embedding.cache_clear()

    embedding = embed_text("RAG-Fusion 混合检索")

    assert len(embedding) == 1024


def test_embedding_provider_defaults_to_dashscope(monkeypatch):
    monkeypatch.delenv("RAG_EMBEDDING_PROVIDER", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    assert embedding_provider_name() == "dashscope"


def test_dashscope_embedding_request_uses_1024_dimensions(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {"data": [{"embedding": [0.001] * 1024}]}

    class FakeClient:
        def __init__(self, timeout):
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def post(self, url, headers, json):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("RAG_VECTOR_DIMENSIONS", "1024")
    cached_embedding.cache_clear()

    embedding = embed_text("RAG-Fusion 混合检索")

    assert len(embedding) == 1024
    assert captured["url"].endswith("/embeddings")
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["model"] == "text-embedding-v4"
    assert captured["json"]["dimensions"] == 1024


def test_local_rerank_keeps_evidence_id_and_scores():
    store = InMemoryRagStore()
    store.index_text(
        IndexTextRequest(
            documentId="doc-rerank",
            title="RAG 重排笔记",
            documentType="markdown",
            source="unit-test",
            userId="unit-user",
            content="## 重排序\nRerank 会在 RAG-Fusion 后把更相关的证据排在前面。",
        )
    )
    evidences = store.query(QueryRequest(question="RAG-Fusion 后如何重排序？", topK=3)).evidences
    ranked = local_rerank("RAG-Fusion 后如何重排序？", evidences, 1)

    assert len(ranked) == 1
    assert ranked[0].evidenceId
    assert ranked[0].retrievalSource == "rerank"


def test_deterministic_answer_keeps_evidence_citation():
    store = InMemoryRagStore()
    store.index_text(
        IndexTextRequest(
            documentId="doc-answer",
            title="回答引用笔记",
            documentType="markdown",
            source="unit-test",
            userId="unit-user",
            content="## 引用\n回答必须保留 evidenceId 引用。",
        )
    )
    evidence = store.query(QueryRequest(question="回答为什么要保留引用？", topK=1)).evidences[0]
    answer = deterministic_grounded_answer("回答为什么要保留引用？", [evidence])

    assert f"[{evidence.evidenceId}]" in answer


def test_answer_reference_summary_keeps_source_location_and_score():
    store = InMemoryRagStore()
    store.index_text(
        IndexTextRequest(
            documentId="doc-reference-summary",
            title="引用摘要笔记",
            documentType="markdown",
            source="unit-test",
            userId="unit-user",
            sourcePath="uploads/rag/reference.md",
            content="## 引用结构\n回答需要保留来源、章节和分数。",
        )
    )
    evidence = store.query(QueryRequest(question="回答要保留哪些引用字段？", topK=1)).evidences[0]
    answer = append_evidence_reference_summary("根据资料回答。", [evidence])

    assert "证据引用：" in answer
    assert evidence.evidenceId in answer
    assert "引用摘要笔记" in answer
    assert "uploads/rag/reference.md" in answer
    assert "分数：" in answer


def test_video_metadata_filter_matches_promoted_block_metadata():
    store = InMemoryRagStore()
    block = DocumentBlock(
        documentId="doc-video-filter",
        blockId="doc-video-filter-subtitle-1",
        fileType="srt",
        blockType="text",
        startTime="00:00:10",
        endTime="00:00:20",
        sectionTitle="视频字幕",
        contentText="这里讲到了 RAG-Fusion 和 Multi-Query 检索。",
        parseEngine="unit-subtitle",
        sourceTitle="视频检索课",
        sourcePath="https://example.com/rag-course.mp4",
        metadata={
            "mediaType": "video",
            "evidenceChannel": "subtitle",
            "videoUrl": "https://example.com/rag-course.mp4",
        },
    )
    store.index_blocks(
        document_id="doc-video-filter",
        title="视频检索课",
        document_type="srt",
        source="unit-test",
        user_id="unit-user",
        visibility_scope="private",
        language="zh-CN",
        parser="unit-subtitle",
        blocks=[block],
        parse_quality=evaluate_parse_quality(QualitySignals(native_text_chars=len(block.contentText))),
        status="READY",
        source_path="https://example.com/rag-course.mp4",
    )

    response = store.query(
        QueryRequest(
            question="RAG-Fusion 怎么检索？",
            topK=3,
            metadataFilter={"mediaType": "video", "evidenceChannel": "subtitle"},
        )
    )

    assert response.evidences
    assert response.evidences[0].startTime == "00:00:10"
    assert response.evidences[0].playbackUrl == "https://example.com/rag-course.mp4#t=10"


def test_query_diversity_filters_duplicate_video_frame_ocr(monkeypatch):
    monkeypatch.setenv("RAG_QUERY_DIVERSITY_DEDUP_ENABLED", "true")
    store = InMemoryRagStore()
    duplicate_metadata = {
        "mediaType": "video",
        "evidenceChannel": "frame_ocr",
        "duplicateGroupId": "doc-video-diversity-frame-group-1",
        "normalizedTextHash": "same-normalized-hash",
        "timeRanges": [{"startTime": "00:06:00", "endTime": "00:06:00"}],
        "mergedFrameCount": 2,
    }
    blocks = [
        DocumentBlock(
            documentId="doc-video-diversity",
            blockId="doc-video-diversity-frame-1",
            fileType="mp4",
            blockType="image",
            startTime="00:06:00",
            sectionTitle="视频画面 00:06:00",
            contentText="视频画面 00:06:00\nRAG-Fusion 使用 RRF 融合 BM25 和向量检索结果。",
            parseEngine="bailian-qwen-ocr",
            sourceTitle="RAG 课程视频",
            sourcePath="https://example.com/rag-course.mp4",
            metadata=duplicate_metadata,
        ),
        DocumentBlock(
            documentId="doc-video-diversity",
            blockId="doc-video-diversity-frame-2",
            fileType="mp4",
            blockType="image",
            startTime="00:06:30",
            sectionTitle="视频画面 00:06:30",
            contentText="视频画面 00:06:30\nRAG-Fusion 使用 RRF 融合 BM25 和向量检索结果。",
            parseEngine="bailian-qwen-ocr",
            sourceTitle="RAG 课程视频",
            sourcePath="https://example.com/rag-course.mp4",
            metadata={**duplicate_metadata, "timeRanges": [{"startTime": "00:06:30", "endTime": "00:06:30"}]},
        ),
        DocumentBlock(
            documentId="doc-video-diversity",
            blockId="doc-video-diversity-summary",
            fileType="mp4",
            blockType="text",
            startTime="00:07:30",
            endTime="00:08:00",
            sectionTitle="视频片段摘要 00:07:30 - 00:08:00",
            contentText="视频片段摘要：RAG-Fusion 会通过 RRF 将多路查询的 BM25 和向量召回排名融合。",
            parseEngine="video-segment-summary",
            sourceTitle="RAG 课程视频",
            sourcePath="https://example.com/rag-course.mp4",
            metadata={"mediaType": "video", "evidenceChannel": "video_segment_summary"},
        ),
    ]
    store.index_blocks(
        document_id="doc-video-diversity",
        title="RAG 课程视频",
        document_type="mp4",
        source="unit-test",
        user_id="unit-user",
        visibility_scope="private",
        language="zh-CN",
        parser="unit-video",
        blocks=blocks,
        parse_quality=evaluate_parse_quality(QualitySignals(native_text_chars=200)),
        status="READY",
        source_path="https://example.com/rag-course.mp4",
    )

    response = store.query(QueryRequest(question="RAG-Fusion 如何融合 BM25 和向量召回？", topK=2))

    group_ids = [
        evidence.metadata.get("duplicateGroupId")
        for evidence in response.evidences
        if evidence.metadata.get("evidenceChannel") == "frame_ocr"
    ]
    assert group_ids.count("doc-video-diversity-frame-group-1") <= 1
    assert response.diagnostics["candidateBudget"] == 20
    assert response.diagnostics["dedupRemovedCount"] >= 1
    assert response.diagnostics["diversityPolicy"] == "video_duplicate_group_and_time_window"


def test_index_blocks_removes_postgres_nul_before_storage():
    nul = chr(0)
    store = InMemoryRagStore()
    block = DocumentBlock(
        documentId="doc-nul",
        blockId="doc-nul-block",
        fileType="pptx",
        blockType="text",
        sectionTitle=f"NaiveRAG{nul}流程",
        contentText=f"PPTX 解析文本中可能夹带{nul}空字符。",
        parseEngine=f"unit-test{nul}",
        sourceTitle=f"课程{nul}PPT",
        sourcePath=f"uploads/rag/course{nul}.pptx",
        metadata={"slideTitle": f"标题{nul}", "nested": {"note": f"备注{nul}"}},
    )
    store.index_blocks(
        document_id="doc-nul",
        title=f"课程{nul}PPT",
        document_type="pptx",
        source="unit-test",
        user_id="unit-user",
        visibility_scope="private",
        language="zh-CN",
        parser=f"unit-test{nul}",
        blocks=[block],
        parse_quality=evaluate_parse_quality(QualitySignals(native_text_chars=len(block.contentText))).model_copy(
            update={"messages": [f"warning{nul}"]}
        ),
        status="READY",
        source_path=f"uploads/rag/course{nul}.pptx",
    )

    document = store.documents["doc-nul"]
    chunk = next(iter(store.chunks.values()))

    assert nul not in chunk.text
    assert_no_postgres_nul(document)
    assert_no_postgres_nul(chunk.metadata)
    assert chunk.metadata["parseQuality"]["messages"] == []
    assert chunk.metadata["parseQuality"]["messageCount"] == 1
