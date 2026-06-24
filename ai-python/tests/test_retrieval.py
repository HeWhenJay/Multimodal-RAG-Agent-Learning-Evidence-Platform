from rag.retrievers.retrieval import cached_embedding, embed_text, embedding_provider_name
from rag.retrievers.retrieval import InMemoryRagStore
from rag.retrievers.answer_guard import evaluate_answer_guard
from rag.progress import RagProgressReporter
from rag.retrievers.evidence_diversity import dedupe_evidences_for_context
from rag.retrievers.parent_aggregation import ParentAggregationChunk, aggregate_parent_evidences
from rag.retrievers.query_expansion import expand_queries_with_diagnostics
from rag.bailian_llm import append_evidence_reference_summary, build_evidence_location_link, clean_evidence_location, deterministic_grounded_answer
from rag.indexes.pgvector_store import build_filter_clause, vector_literal
from rag.rerankers.reranking import local_rerank
from rag.loaders.parse_quality import QualitySignals, evaluate_parse_quality
from app.schemas.rag import DocumentBlock, Evidence, IndexTextRequest, QueryRequest


def make_evidence(
    evidence_id: str,
    snippet: str,
    *,
    score: float = 0.5,
    metadata: dict | None = None,
) -> Evidence:
    """构造 answer guard 单测所需的最小 evidence。"""
    return Evidence(
        evidenceId=evidence_id,
        documentId="doc-guard",
        documentTitle="Guard 测试资料",
        title="Guard 测试资料",
        snippet=snippet,
        source="unit-test",
        sectionName="测试章节",
        documentType="markdown",
        score=score,
        retrievalSource="rerank",
        metadata=metadata or {},
    )


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
    assert response.answerStatus == "ANSWERED"
    assert response.supportingEvidenceIds == [item.evidenceId for item in response.evidences]
    assert "自动配置" in response.answer
    assert response.evidences[0].retrievalSource == "rerank"
    assert response.diagnostics["answerProvider"] == "local"
    assert response.diagnostics["rerankProvider"] == "local"
    assert response.diagnostics["queryExpansionProvider"] == "local"
    assert response.diagnostics["answerGuard"]["answerStatus"] == "ANSWERED"
    assert len(response.expandedQueries) >= 3


def test_rag_store_refuses_when_no_evidence_without_calling_llm(monkeypatch):
    def forbid_generate(*args, **kwargs):
        raise AssertionError("拒答场景不应调用 LLM 回答生成")

    monkeypatch.setattr("rag.retrievers.retrieval.generate_grounded_answer", forbid_generate)
    store = InMemoryRagStore()

    response = store.query(QueryRequest(question="当前知识库没有资料时应该怎么办？", topK=3))

    assert response.answerStatus == "REFUSED"
    assert response.refusalReason == "NO_EVIDENCE"
    assert response.evidences == []
    assert response.supportingEvidenceIds == []
    assert response.refusalMessage == "知识库暂无证据"
    assert response.diagnostics["answerGuard"]["answerStatus"] == "REFUSED"
    assert response.diagnostics["answerGuard"]["refusalReason"] == "NO_EVIDENCE"


def test_rag_store_refuses_when_filtered_out_without_calling_llm(monkeypatch):
    def forbid_generate(*args, **kwargs):
        raise AssertionError("拒答场景不应调用 LLM 回答生成")

    monkeypatch.setattr("rag.retrievers.retrieval.generate_grounded_answer", forbid_generate)
    store = InMemoryRagStore()
    store.index_text(
        IndexTextRequest(
            documentId="doc-filter",
            title="过滤测试笔记",
            documentType="markdown",
            source="unit-test",
            userId="unit-user",
            content="## 过滤测试\n这里只是用来验证 metadataFilter 过滤结果。",
        )
    )

    response = store.query(
        QueryRequest(
            question="这个问题会命中现有资料吗？",
            topK=3,
            metadataFilter={"documentType": "not-exists"},
        )
    )

    assert response.answerStatus == "REFUSED"
    assert response.refusalReason == "FILTERED_OUT"
    assert response.evidences == []
    assert response.supportingEvidenceIds == []
    assert response.diagnostics["answerGuard"]["refusalReason"] == "FILTERED_OUT"


def test_answer_guard_refuses_low_confidence_candidates():
    decision = evaluate_answer_guard(
        question="酸面包二次发酵温湿度曲线",
        expanded_queries=["酸面包二次发酵温湿度曲线"],
        evidences=[
            make_evidence(
                "doc-guard-chunk-1",
                "RAG-Fusion 使用 Multi-Query 和 RRF 融合多路召回结果，用于提升检索覆盖。",
                score=0.09,
            )
        ],
        diagnostics={"totalCandidateChunkCount": 3, "filteredChunkCount": 3, "rerankProvider": "local"},
    )

    assert decision.answerStatus == "REFUSED"
    assert decision.refusalReason == "LOW_CONFIDENCE"
    assert decision.supportingEvidenceIds == []


def test_answer_guard_refuses_weak_snippet_candidate():
    decision = evaluate_answer_guard(
        question="RAGAS 的四个指标怎么计算？",
        expanded_queries=["RAGAS 的四个指标怎么计算？"],
        evidences=[make_evidence("doc-guard-chunk-2", "目录", score=0.8)],
        diagnostics={"totalCandidateChunkCount": 3, "filteredChunkCount": 3, "rerankProvider": "dashscope"},
    )

    assert decision.answerStatus == "REFUSED"
    assert decision.refusalReason == "WEAK_SNIPPET"


def test_answer_guard_refuses_only_summary_child_candidates():
    decision = evaluate_answer_guard(
        question="Transformer 自注意力细节是什么？",
        expanded_queries=["Transformer 自注意力细节是什么？"],
        evidences=[
            make_evidence(
                "doc-guard-summary-0001",
                "父段摘要：这里概括了 Transformer 和自注意力机制，但没有原始推导细节。",
                score=0.8,
                metadata={"childKind": "summary", "retrievalLayer": "child"},
            )
        ],
        diagnostics={"totalCandidateChunkCount": 3, "filteredChunkCount": 3, "rerankProvider": "dashscope"},
    )

    assert decision.answerStatus == "REFUSED"
    assert decision.refusalReason == "ONLY_DIAGNOSTIC_CANDIDATES"
    assert decision.candidateEvidenceSummaries[0]["diagnosticOnly"] is True


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
    assert "生成方式：local" in expand_events[-1].detail
    assert "鸡蛋怎么做 步骤 方法" in expand_events[-1].detail
    assert len(bm25_events) == len(response.expandedQueries)


def test_local_multi_query_expansion_adapts_review_intent():
    result = expand_queries_with_diagnostics("我忘了自注意力机制，帮我复习一下")

    assert result.provider == "local"
    assert result.queries[0] == "我忘了自注意力机制，帮我复习一下"
    assert 3 <= len(result.queries) <= 5
    assert any("核心概念" in query for query in result.queries)
    assert any("公式" in query for query in result.queries)
    assert all("学习资料 笔记" not in query for query in result.queries)


def test_dashscope_multi_query_expansion_parses_json_array(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                "[\"我忘了自注意力机制，帮我复习一下\", "
                                "\"自注意力机制的核心概念是什么？\", "
                                "\"Q K V 如何计算注意力权重？\", "
                                "\"自注意力有哪些常见例子和易混点？\"]"
                            )
                        }
                    }
                ]
            }

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
            captured["payload"] = json
            return FakeResponse()

    import httpx

    monkeypatch.setenv("RAG_QUERY_EXPANSION_PROVIDER", "dashscope")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.delenv("RAG_QUERY_EXPANSION_MODEL", raising=False)
    monkeypatch.delenv("RAG_LLM_MODEL", raising=False)
    monkeypatch.setattr(httpx, "Client", FakeClient)

    result = expand_queries_with_diagnostics("我忘了自注意力机制，帮我复习一下")

    assert result.provider == "dashscope"
    assert result.model == "qwen-plus"
    assert result.fallback_reason is None
    assert result.queries == [
        "我忘了自注意力机制，帮我复习一下",
        "自注意力机制的核心概念是什么？",
        "Q K V 如何计算注意力权重？",
        "自注意力有哪些常见例子和易混点？",
    ]
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert "只输出 JSON 字符串数组" in captured["payload"]["messages"][0]["content"]


def test_pgvector_filter_clause_supports_whitelist_and_ignores_unknown_keys():
    where_sql, params = build_filter_clause(
        {
            "documentType": "markdown",
            "sectionName": ["自动配置", "事务"],
            "sectionKeyword": "RAG",
            "pageIndex": [1, "2"],
            "customTag": "spring",
        }
    )

    assert "d.document_type = %s" in where_sql
    assert "c.section_name IN (%s, %s)" in where_sql
    assert "(c.section_name ILIKE %s OR c.metadata ->> 'sectionTitle' ILIKE %s)" in where_sql
    assert "c.metadata ->> 'pageIndex' IN (%s, %s)" in where_sql
    assert "customTag" not in where_sql
    assert params == ["markdown", "自动配置", "事务", "%RAG%", "%RAG%", "1", "2"]


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


def test_answer_reference_summary_links_location_to_source_path():
    """证据位置链接应打开原始来源文件，并复用原 Markdown 目录锚点。"""
    evidence = Evidence(
        evidenceId="material-2-17",
        documentId="material-2",
        documentTitle="01_transform_attention.md",
        sectionTitle="[1.1 自注意力机制到底在做什么](http://localhost:5178/#_1-1-自注意力机制到底在做什么)",
        title="01_transform_attention.md",
        snippet="自注意力会基于 Query、Key、Value 计算上下文权重。",
        source="upload",
        sourcePath="https://itxiang-sky-out.oss-cn-chengdu.aliyuncs.com/learning-evidence/1/markdown/20260620/attention.md",
        sectionName="[1.1 自注意力机制到底在做什么](#_1-1-自注意力机制到底在做什么)",
        documentType="markdown",
        score=0.8116,
    )
    answer = append_evidence_reference_summary("根据资料回答。", [evidence])

    assert "位置：[1.1 自注意力机制到底在做什么](https://itxiang-sky-out.oss-cn-chengdu.aliyuncs.com/learning-evidence/1/markdown/20260620/attention.md#_1-1-自注意力机制到底在做什么)" in answer
    assert "localhost:5178" not in answer
    assert clean_evidence_location("[**章节标题**](#anchor)") == "章节标题"
    assert build_evidence_location_link("[章节](#anchor)", "oss://private/path.md") == ""


def test_memory_metadata_filter_supports_in_section_keyword_and_page_index():
    store = InMemoryRagStore()
    blocks = [
        DocumentBlock(
            documentId="doc-filter-contract",
            blockId="doc-filter-contract-page-1",
            fileType="pdf",
            blockType="text",
            pageIndex=1,
            sectionTitle="RAG 元数据过滤",
            contentText="元数据过滤可以先缩小候选范围，再执行 BM25 和向量召回。",
            parseEngine="unit-parser",
            sourceTitle="过滤契约笔记",
        ),
        DocumentBlock(
            documentId="doc-filter-contract",
            blockId="doc-filter-contract-page-2",
            fileType="pdf",
            blockType="text",
            pageIndex=2,
            sectionTitle="普通章节",
            contentText="这里描述默认查询，不包含高级过滤关键词。",
            parseEngine="unit-parser",
            sourceTitle="过滤契约笔记",
        ),
    ]
    store.index_blocks(
        document_id="doc-filter-contract",
        title="过滤契约笔记",
        document_type="pdf",
        source="upload",
        user_id="unit-user",
        visibility_scope="private",
        language="zh-CN",
        parser="unit-parser",
        blocks=blocks,
        parse_quality=evaluate_parse_quality(QualitySignals(native_text_chars=120)),
        status="READY",
    )

    response = store.query(
        QueryRequest(
            question="元数据过滤怎么缩小候选范围？",
            topK=3,
            metadataFilter={
                "documentType": ["pdf", "markdown"],
                "sectionKeyword": "rag",
                "pageIndex": "1",
                "unknown": "ignored",
            },
        )
    )
    number_response = store.query(
        QueryRequest(
            question="元数据过滤怎么缩小候选范围？",
            topK=3,
            metadataFilter={"pageIndex": 1},
        )
    )

    assert response.evidences
    assert {evidence.pageIndex for evidence in response.evidences} == {1}
    assert response.diagnostics["totalCandidateChunkCount"] >= response.diagnostics["filteredChunkCount"]
    assert response.diagnostics["effectiveMetadataFilter"]["sectionKeyword"] == "rag"
    assert "unknown" in response.diagnostics["ignoredMetadataFilterKeys"]
    assert number_response.evidences
    assert {evidence.pageIndex for evidence in number_response.evidences} == {1}


def test_video_metadata_filter_matches_promoted_block_metadata():
    store = InMemoryRagStore()
    blocks = [
        DocumentBlock(
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
        ),
        DocumentBlock(
            documentId="doc-video-filter",
            blockId="doc-video-filter-frame-1",
            fileType="mp4",
            blockType="image",
            startTime="00:00:30",
            sectionTitle="视频画面 00:00:30",
            contentText="关键帧 OCR：RAG-Fusion 会把 BM25 和向量召回融合。",
            parseEngine="unit-frame-ocr",
            sourceTitle="视频检索课",
            sourcePath="https://example.com/rag-course.mp4",
            metadata={
                "mediaType": "video",
                "evidenceChannel": "frame_ocr",
                "videoUrl": "https://example.com/rag-course.mp4",
            },
        ),
    ]
    store.index_blocks(
        document_id="doc-video-filter",
        title="视频检索课",
        document_type="srt",
        source="unit-test",
        user_id="unit-user",
        visibility_scope="private",
        language="zh-CN",
        parser="unit-subtitle",
        blocks=blocks,
        parse_quality=evaluate_parse_quality(QualitySignals(native_text_chars=sum(len(block.contentText) for block in blocks))),
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

    frame_response = store.query(
        QueryRequest(
            question="关键帧 OCR 讲了什么？",
            topK=3,
            metadataFilter={"evidenceChannel": "frame_ocr"},
        )
    )

    assert frame_response.evidences
    assert {evidence.metadata.get("evidenceChannel") for evidence in frame_response.evidences} == {"frame_ocr"}


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
    assert response.diagnostics["parentAggregation"]["expandedParentCount"] >= 1
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


def test_summary_child_can_be_recalled_and_aggregated_to_parent():
    """summary child 应进入召回，并在进入 rerank 前聚合为父段 evidence。"""
    store = InMemoryRagStore()
    block = DocumentBlock(
        documentId="doc-summary-child",
        blockId="doc-summary-child-raw",
        fileType="md",
        blockType="text",
        sectionTitle="索引增强",
        contentText="这段只描述父子索引会保留小块召回和大块上下文。",
        parseEngine="unit-markdown",
        sourceTitle="索引增强笔记",
    )
    store.index_blocks(
        document_id="doc-summary-child",
        title="索引增强笔记",
        document_type="markdown",
        source="unit-test",
        user_id="unit-user",
        visibility_scope="private",
        language="zh-CN",
        parser="unit-markdown",
        blocks=[block],
        parse_quality=evaluate_parse_quality(QualitySignals(native_text_chars=100)),
        status="READY",
    )

    summary_chunks = [chunk for chunk in store.chunks.values() if chunk.metadata.get("childKind") == "summary"]
    assert summary_chunks
    response = store.query(QueryRequest(question="父段摘要", topK=3))

    assert response.evidences
    assert response.evidences[0].retrievalSource in {"fusion", "rerank"}
    assert response.evidences[0].metadata["retrievalLayer"] == "parent_aggregated"
    assert response.diagnostics["parentAggregation"]["enabled"] is True
    assert response.diagnostics["matchedChildIds"]
    assert response.diagnostics["expandedParentIds"]
    assert response.diagnostics["prerequisiteAddedIds"] == []


def test_retrieval_source_stays_enum_and_layer_lives_in_metadata():
    """父段聚合不能把 retrievalSource 改成非法枚举。"""
    store = InMemoryRagStore()
    store.index_text(
        IndexTextRequest(
            documentId="doc-layer",
            title="检索层级笔记",
            documentType="markdown",
            source="unit-test",
            userId="unit-user",
            content="## 检索层级\n父段聚合后的层级信息应写入 metadata.retrievalLayer。",
        )
    )

    evidence = store.query(QueryRequest(question="父段聚合层级信息在哪里？", topK=1)).evidences[0]

    assert evidence.retrievalSource == "rerank"
    assert evidence.metadata["retrievalLayer"] == "parent_aggregated"


def test_parent_aggregation_helper_is_shared_contract():
    """helper 单测覆盖 memory/pgvector 共用的父段聚合契约。"""
    store = InMemoryRagStore()
    store.index_text(
        IndexTextRequest(
            documentId="doc-parent-helper",
            title="父段 helper 笔记",
            documentType="markdown",
            source="unit-test",
            userId="unit-user",
            content="## helper\nmemory 和 pgvector 需要共用父段聚合 helper。",
        )
    )
    child = next(chunk for chunk in store.chunks.values() if chunk.metadata.get("childKind") == "raw")
    evidence = store._to_evidence(child.chunk_id, 0.5, retrieval_source="fusion")

    result = aggregate_parent_evidences(
        [evidence],
        chunks=[
            ParentAggregationChunk(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                text=chunk.text,
                metadata=chunk.metadata,
            )
            for chunk in store.chunks.values()
        ],
        limit=5,
    )

    assert result.evidences[0].evidenceId == child.metadata["parentSegmentId"]
    assert result.evidences[0].metadata["matchedChildIds"]
    assert result.diagnostics()["parentAggregation"]["prerequisiteExpansionEnabled"] is False


def test_video_ocr_occurrence_not_folded_by_diversity(monkeypatch):
    """相同 OCR 内容在不同 occurrence 下不应被 diversity 折叠。"""
    monkeypatch.setenv("RAG_QUERY_DIVERSITY_DEDUP_ENABLED", "true")
    monkeypatch.setenv("RAG_PARENT_VIDEO_WINDOW_SECONDS", "60")
    monkeypatch.setenv("RAG_QUERY_VIDEO_TIME_WINDOW_SECONDS", "60")
    store = InMemoryRagStore()
    block = DocumentBlock(
        documentId="doc-occurrence-diversity",
        blockId="doc-occurrence-diversity-frame",
        fileType="mp4",
        blockType="image",
        startTime="00:00:10",
        endTime="00:01:30",
        sectionTitle="视频画面聚合 00:00:10 - 00:01:30",
        contentText="视频画面聚合 00:00:10 - 00:01:30\nRAG-Fusion 使用 RRF 融合 BM25 和向量检索结果。",
        parseEngine="video-frame-ocr",
        sourceTitle="RAG 课程视频",
        metadata={
            "mediaType": "video",
            "evidenceChannel": "frame_ocr",
            "duplicateGroupId": "same-frame-ocr-group",
            "normalizedTextHash": "same-normalized-hash",
            "sourceFrameTimes": ["00:00:10", "00:01:30"],
            "timeRanges": [
                {"startTime": "00:00:10", "endTime": "00:00:10"},
                {"startTime": "00:01:30", "endTime": "00:01:30"},
            ],
        },
    )
    store.index_blocks(
        document_id="doc-occurrence-diversity",
        title="RAG 课程视频",
        document_type="mp4",
        source="unit-test",
        user_id="unit-user",
        visibility_scope="private",
        language="zh-CN",
        parser="unit-video",
        blocks=[block],
        parse_quality=evaluate_parse_quality(QualitySignals(native_text_chars=120)),
        status="READY",
    )
    occurrence_evidences = [
        store._to_evidence(chunk.chunk_id, 1.0, retrieval_source="fusion")
        for chunk in store.chunks.values()
        if chunk.metadata.get("childKind") == "ocr_occurrence"
    ]

    result = dedupe_evidences_for_context("RAG-Fusion 如何融合？", occurrence_evidences, top_k=2)

    assert len(occurrence_evidences) == 2
    assert len(result.evidences) == 2
    assert result.removed_count == 0
