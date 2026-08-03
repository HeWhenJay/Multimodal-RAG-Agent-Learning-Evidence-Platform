"""官方 LangExtract 复习知识适配与 A/B 决策测试。"""

from types import SimpleNamespace

import langextract as lx

from app.review.langextract_curator import (
    CuratorCandidate,
    EvidenceTextSpan,
    ModelUsageAudit,
    build_source_document,
    deduplicate_curator_candidates,
    grounded_candidate,
)
from app.schemas.rag import Evidence
from rag.evaluation.review_curator_ab_common import (
    CuratorArmResult,
    CuratorCaseResult,
    decide_langextract,
    final_duplicate_rate,
    match_expected_points,
    texts_are_near_duplicates,
)


def evidence(evidence_id: str, snippet: str) -> Evidence:
    """构造 A/B 字符范围测试所需的最小 evidence。"""
    return Evidence(
        evidenceId=evidence_id,
        documentId="material-29",
        documentTitle="Kafka 高性能",
        title="Kafka 高性能",
        snippet=snippet,
        source="upload",
        sectionName="视频字幕",
        documentType="mp4",
        score=1.0,
        retrievalSource="summary",
    )


def test_source_document_preserves_evidence_character_ranges() -> None:
    """完整资料拼接后必须能把字符区间稳定映射回原 evidence。"""
    text, spans = build_source_document(
        [
            evidence("e-1", "消息分区突破单机限制。"),
            evidence("e-2", "顺序写减少磁盘寻址。"),
        ]
    )

    assert text == "消息分区突破单机限制。\n\n顺序写减少磁盘寻址。"
    assert spans == (
        EvidenceTextSpan("e-1", 0, 11),
        EvidenceTextSpan("e-2", 13, 23),
    )


def test_grounded_candidate_requires_exact_source_alignment() -> None:
    """未定位或经模型改写的结果不得进入候选池。"""
    source = "消息分区突破单机限制。\n\n顺序写减少磁盘寻址。"
    start = source.index("顺序写减少磁盘寻址")
    end = start + len("顺序写减少磁盘寻址")
    spans = (EvidenceTextSpan("e-1", 0, 11), EvidenceTextSpan("e-2", 13, 23))
    exact = lx.data.Extraction(
        extraction_class="knowledge_unit",
        extraction_text="顺序写减少磁盘寻址",
        char_interval=lx.data.CharInterval(start_pos=start, end_pos=end),
        attributes={"topic": "顺序写", "knowledge_type": "作用"},
    )
    paraphrased = lx.data.Extraction(
        extraction_class="knowledge_unit",
        extraction_text="顺序写可以提升性能",
        char_interval=lx.data.CharInterval(start_pos=start, end_pos=end),
    )
    ungrounded = lx.data.Extraction(
        extraction_class="knowledge_unit",
        extraction_text="页缓存减少磁盘读取",
    )

    assert grounded_candidate(exact, source, spans) == CuratorCandidate(
        text="顺序写减少磁盘寻址",
        topic="顺序写",
        knowledge_type="作用",
        evidence_ids=("e-2",),
        char_start=start,
        char_end=end,
    )
    assert grounded_candidate(paraphrased, source, spans) is None
    assert grounded_candidate(ungrounded, source, spans) is None


def test_grounded_candidate_keeps_every_overlapping_evidence_id() -> None:
    """跨片段知识不能因卡片层上限而在候选发现阶段丢失证据。"""
    source = "第一段事实。\n\n第二段事实。\n\n第三段事实。"
    extraction = lx.data.Extraction(
        extraction_class="knowledge_unit",
        extraction_text=source,
        char_interval=lx.data.CharInterval(start_pos=0, end_pos=len(source)),
    )
    spans = (
        EvidenceTextSpan("e-1", 0, 6),
        EvidenceTextSpan("e-2", 8, 14),
        EvidenceTextSpan("e-3", 16, 22),
    )

    candidate = grounded_candidate(extraction, source, spans)

    assert candidate is not None
    assert candidate.evidence_ids == ("e-1", "e-2", "e-3")


def test_cross_pass_candidates_are_deduplicated_in_source_order() -> None:
    """多轮 extraction passes 的重复结果只能保留一次。"""
    candidates = [
        CuratorCandidate("页缓存减少磁盘读取", "页缓存", "作用", ("e-2",), 20, 30),
        CuratorCandidate("消息分区突破单机限制", "消息分区", "作用", ("e-1",), 0, 10),
        CuratorCandidate("消息分区突破单机限制。", "消息分区", "作用", ("e-1",), 0, 11),
    ]

    unique, duplicate_count = deduplicate_curator_candidates(candidates)

    assert [item.text for item in unique] == ["消息分区突破单机限制", "页缓存减少磁盘读取"]
    assert duplicate_count == 1
    assert final_duplicate_rate(tuple(item.text for item in unique)) == 0.0


def test_usage_audit_enforces_equal_request_budget() -> None:
    """任一实验臂都不能用超过预注册上限的模型请求换取召回。"""
    audit = ModelUsageAudit(max_requests=1)
    audit.begin_request()
    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=5),
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))],
    )
    audit.record_response({"messages": []}, response)

    assert audit.request_count == 1
    assert audit.total_tokens == 17
    try:
        audit.begin_request()
    except RuntimeError as exc:
        assert "预算已耗尽" in str(exc)
    else:
        raise AssertionError("第二次请求应被相同预算拦截")


def test_langextract_curator_uses_same_model_timeout_as_baseline() -> None:
    """B 臂默认单请求超时必须与当前生成器的 120 秒保持一致。"""
    from app.review.langextract_curator import LangExtractKnowledgeCurator

    assert LangExtractKnowledgeCurator(api_key="test-key").timeout_seconds == 120.0


def test_expected_point_matching_uses_frozen_aliases() -> None:
    """人工金标通过预先冻结的同义短语确定性计算召回。"""
    matched, missing = match_expected_points(
        [
            {"name": "消息分区", "aliases": ["消息分区", "分区突破单机"]},
            {"name": "页缓存", "aliases": ["页缓存"]},
        ],
        ("Kafka 的消息分区不受单台服务器限制。",),
    )

    assert matched == ["消息分区"]
    assert missing == ["页缓存"]


def test_expected_point_matching_supports_distributed_required_groups() -> None:
    """复合目录项可由多条候选共同覆盖，但每个必需关键词组都要命中。"""
    expected = [
        {
            "name": "装饰器与上下文管理器",
            "requiredGroups": [["装饰器"], ["上下文管理器", "with语句"]],
        }
    ]

    matched, missing = match_expected_points(
        expected,
        ("装饰器可以扩展函数。", "上下文管理器通过 with 语句释放资源。"),
    )
    partial_matched, partial_missing = match_expected_points(expected, ("装饰器可以扩展函数。",))

    assert matched == ["装饰器与上下文管理器"]
    assert missing == []
    assert partial_matched == []
    assert partial_missing == ["装饰器与上下文管理器"]


def test_near_duplicate_rate_catches_repeated_pass_paraphrases() -> None:
    """跨 pass 的包含式复述要按相同规则计入两臂最终重复率。"""
    assert texts_are_near_duplicates("map对每个元素应用函数生成新序列", "map就是对每个元素应用函数生成新序列")
    assert not texts_are_near_duplicates("slots限制实例属性", "slots阻止实例字典创建并节省内存")
    assert final_duplicate_rate(
        ("map 对每个元素应用函数生成新序列", "map 就是对每个元素应用函数生成新序列", "页缓存减少磁盘访问")
    ) == 1 / 3


def arm(
    name: str,
    *,
    recall: float,
    tokens: int,
    mapping: float = 1.0,
    duplicates: float = 0.0,
) -> CuratorArmResult:
    """构造生产启用门槛测试用的单臂结果。"""
    return CuratorArmResult(
        arm=name,
        status="COMPLETED",
        candidates=("候选",),
        evidence_mapping_success_rate=mapping,
        published_ungrounded_rate=0.0,
        duplicate_rate=duplicates,
        raw_candidate_count=1,
        accepted_candidate_count=1,
        matched_expected_points=("知识点",),
        missing_expected_points=(),
        expected_recall=recall,
        request_count=1,
        input_tokens=tokens,
        output_tokens=0,
        total_tokens=tokens,
        duration_seconds=1.0,
    )


def test_production_decision_requires_gain_quality_cost_and_both_categories() -> None:
    """只有召回、定位、重复、成本和资料类型同时达标才允许生产启用。"""
    cases = [
        CuratorCaseResult("declarative", "declarative_course", 29, "Kafka", 1, 100, arm("A", recall=0.7, tokens=100), arm("B", recall=0.9, tokens=140)),
        CuratorCaseResult("structured", "structured_questions", 36, "Python", 1, 100, arm("A", recall=0.7, tokens=100), arm("B", recall=0.9, tokens=140)),
    ]

    passed = decide_langextract(cases)
    failed = decide_langextract(
        [
            cases[0],
            CuratorCaseResult("structured", "structured_questions", 36, "Python", 1, 100, arm("A", recall=0.8, tokens=100), arm("B", recall=0.7, tokens=200, mapping=0.8)),
        ]
    )

    assert passed["enable"] is True
    assert failed["enable"] is False
    assert any("召回率" in reason for reason in failed["reasons"])
    assert any("evidence" in reason for reason in failed["reasons"])
    assert any("Token" in reason for reason in failed["reasons"])
