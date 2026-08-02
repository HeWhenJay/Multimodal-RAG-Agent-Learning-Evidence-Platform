"""学习资料分类与关键知识点提炼测试。"""

import json
from types import SimpleNamespace

import pytest

from app.review.knowledge_extractor import (
    KnowledgePointExtractor,
    LearningMaterialContext,
    REVIEW_LLM_BASE_URL,
    REVIEW_LLM_MODEL,
    REVIEW_LLM_REASONING_EFFORT,
    build_question,
    clean_section_name,
    extract_source_questions,
    is_generic_speech_cue,
    is_noise_fragment,
    is_repetitive_noise,
    split_knowledge_sentences,
    stable_source_key,
)
from app.schemas.rag import Evidence
from prompts.review import REVIEW_CARD_PROMPT_VERSION, review_card_system_prompt


def evidence(evidence_id: str, section: str, snippet: str) -> Evidence:
    """构造带视频时间段的真实 evidence。"""
    return Evidence(
        evidenceId=evidence_id,
        documentId="material-12",
        documentTitle="Kafka 的高可用性（视频讲解）",
        title="Kafka 的高可用性（视频讲解）",
        sectionName=section,
        sectionTitle=section,
        snippet=snippet,
        source="upload",
        documentType="mp4",
        startTime="00:08:12",
        endTime="00:10:05",
        playbackUrl="/videos?documentId=material-12&startTime=00%3A08%3A12",
        score=1.0,
        retrievalSource="summary",
        metadata={"childKind": "summary"},
    )


def test_local_extractor_builds_short_cards_with_real_evidence() -> None:
    """视频学习资料应生成知识点卡，而不是要求重看整段视频。"""
    extractor = KnowledgePointExtractor(provider="local")
    result = extractor.extract(
        LearningMaterialContext(12, "Kafka 的高可用性（视频讲解）", "mp4", "Kafka 面试八股"),
        [
            evidence(
                "material-12-7",
                "ISR 与故障转移",
                "Kafka 的每个分区由一个 Leader 和多个 Follower 副本组成。"
                "ISR 保存与 Leader 保持同步的副本集合。Leader 故障后会优先从 ISR 中选举新 Leader。",
            )
        ],
    )

    assert result.is_learning_content is True
    assert 3 <= len(result.knowledge_points) <= 8
    assert all(point.evidence_refs for point in result.knowledge_points)
    assert all(point.evidence_refs[0].startTime == "00:08:12" for point in result.knowledge_points)
    assert all("重新" not in point.question + point.answer for point in result.knowledge_points)


def test_resume_without_learning_signals_is_skipped() -> None:
    """简历业务资料默认不进入复习队列。"""
    extractor = KnowledgePointExtractor(provider="local")
    result = extractor.extract(
        LearningMaterialContext(9, "个人简历", "pdf", "求职简历与项目经历"),
        [evidence("material-9-1", "项目经历", "负责业务系统开发并参与日常需求交付和项目协作。")],
    )

    assert result.is_learning_content is False
    assert result.knowledge_points == ()


def test_structured_meeting_notes_are_not_learning_content() -> None:
    """普通会议纪要即使有多个章节，也不能仅凭结构进入复习中心。"""
    extractor = KnowledgePointExtractor(provider="local")
    result = extractor.extract(
        LearningMaterialContext(10, "产品迭代会议纪要", "md", "本周协作记录"),
        [
            evidence("material-10-1", "参会人员", "张三、李四和王五参加本次项目沟通会议。"),
            evidence("material-10-2", "待办事项", "张三周五前更新页面，李四负责确认排期。"),
        ],
    )

    assert result.is_learning_content is False
    assert result.knowledge_points == ()


def test_model_extractor_uses_one_centralized_prompt_call_per_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """一份资料只调用一次复习专用模型，并固定模型与思考强度。"""
    calls: list[dict] = []
    clients: list[dict] = []
    payload = {
        "isLearningContent": True,
        "category": "技术原理",
        "reason": "包含 Kafka 高可用知识点",
        "summary": "模型不得覆盖已有摘要",
        "cards": [
            {
                "question": f"Kafka 高可用知识点 {index} 是什么？",
                "answer": "ISR 保存与 Leader 保持同步的副本集合。",
                "hint": "回忆 ISR",
                "evidenceIds": ["material-12-7"],
            }
            for index in range(1, 4)
        ],
    }

    class FakeCompletions:
        """记录一次 OpenAI 兼容调用。"""

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: clients.append(kwargs) or fake_client)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    extractor = KnowledgePointExtractor(provider="deepseek")

    result = extractor.extract(
        LearningMaterialContext(12, "Kafka 的高可用性", "pdf", "Kafka 面试八股"),
        [evidence("material-12-7", "ISR", "ISR 保存与 Leader 保持同步的副本集合，并参与故障后的 Leader 选举。")],
    )

    assert len(calls) == 1
    assert REVIEW_CARD_PROMPT_VERSION == "review-card-v4"
    assert clients == [{"api_key": "test-key", "base_url": REVIEW_LLM_BASE_URL}]
    assert calls[0]["model"] == REVIEW_LLM_MODEL == "deepseek-v4-flash"
    assert calls[0]["reasoning_effort"] == REVIEW_LLM_REASONING_EFFORT == "max"
    assert calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in calls[0]
    assert calls[0]["messages"][0]["content"] == review_card_system_prompt()
    assert calls[0]["messages"][1]["role"] == "user"
    assert result.extractor == f"model:{REVIEW_CARD_PROMPT_VERSION}"
    assert len(result.knowledge_points) == 3
    assert result.summary == "Kafka 面试八股"


def test_model_uses_validated_original_question_and_generates_missing_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺失摘要与卡片应同次生成，命中候选后必须采用 evidence 中的原问句。"""
    calls: list[dict] = []
    original_question = "Kafka 为什么能够在 Broker 故障后继续提供服务？"
    payload = {
        "isLearningContent": True,
        "category": "课程复习",
        "reason": "视频明确讲解 Kafka 故障转移",
        "summary": "视频重点说明 Kafka 通过分区副本、ISR 和 Leader 选举完成故障转移。",
        "cards": [
            {
                "question": "Kafka 是如何实现高可用的？",
                "sourceQuestion": original_question,
                "answer": "Leader 故障后会优先从 ISR 中选举新的 Leader。",
                "hint": "回忆 ISR",
                "evidenceIds": ["material-12-question"],
            }
        ],
    }

    class FakeCompletions:
        """返回包含 sourceQuestion 的单次结构化响应。"""

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))]
            )

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr("openai.OpenAI", lambda **_kwargs: fake_client)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    extractor = KnowledgePointExtractor(provider="deepseek")
    result = extractor.extract(
        LearningMaterialContext(12, "Kafka 高可用课程", "mp4"),
        [
            evidence(
                "material-12-question",
                "故障转移",
                f"{original_question}Leader 故障后会优先从 ISR 中选举新的 Leader。",
            )
        ],
    )

    assert len(calls) == 1
    assert original_question in calls[0]["messages"][1]["content"]
    assert result.summary == payload["summary"]
    assert result.knowledge_points[0].question == original_question


def test_model_rejects_source_question_from_unreferenced_evidence() -> None:
    """sourceQuestion 与卡片引用不一致时不得冒充资料原问句。"""
    extractor = KnowledgePointExtractor(provider="local")
    first = evidence(
        "material-12-first",
        "副本",
        "Kafka 为什么需要多个副本？多个副本用于在节点故障时保留分区数据。",
    )
    second = evidence(
        "material-12-second",
        "ISR",
        "ISR 为什么影响 Leader 选举？ISR 保存与 Leader 保持同步的副本集合。",
    )
    result = extractor._validate_model_result(
        LearningMaterialContext(12, "Kafka 高可用课程", "mp4"),
        [first, second],
        {
            "isLearningContent": True,
            "category": "课程复习",
            "reason": "包含核心机制",
            "summary": "讲解 Kafka 的副本和 ISR。",
            "cards": [
                {
                    "question": "Kafka 的副本机制有什么作用？",
                    "sourceQuestion": "ISR 为什么影响 Leader 选举？",
                    "answer": "多个副本用于在节点故障时保留分区数据。",
                    "evidenceIds": ["material-12-first"],
                }
            ],
        },
    )

    assert result is not None
    assert result.knowledge_points[0].question == "Kafka 的副本机制有什么作用？"


def test_local_fallback_prioritizes_original_question_and_builds_summary() -> None:
    """模型不可用时也应沿用视频原问句，并从其后原文生成答案与摘要。"""
    original_question = "Faiss 的 IndexFlatL2 为什么不需要训练？"
    source = evidence(
        "material-12-faiss",
        "IndexFlatL2",
        f"{original_question}因为它直接保存原始向量，并使用精确的 L2 距离进行检索。",
    )

    assert extract_source_questions(source) == [original_question]
    result = KnowledgePointExtractor(provider="local").extract(
        LearningMaterialContext(12, "Faiss 向量检索算法课程", "mp4"),
        [source],
    )

    assert result.is_learning_content is True
    assert len(result.knowledge_points) == 1
    assert result.knowledge_points[0].question == original_question
    assert "直接保存原始向量" in result.knowledge_points[0].answer
    assert result.summary is not None
    assert "直接保存原始向量" in result.summary


def test_source_question_extraction_handles_asr_comma_instead_of_question_mark() -> None:
    """ASR 把问号转成逗号时，明确疑问句仍应作为原问句候选。"""
    source = evidence(
        "material-12-asr-question",
        "故障恢复",
        "Kafka 为什么能够在 Broker 故障后继续提供服务呢，因为 ISR 可以参与新的 Leader 选举。",
    )

    assert extract_source_questions(source) == ["Kafka 为什么能够在 Broker 故障后继续提供服务呢"]


def test_review_model_does_not_inherit_rag_or_dashscope_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缺少复习专用地址或密钥时必须本地降级，不能借用其他模型配置。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("SUBAI_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setenv("SU_BAI_API_KEY", "proxy-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://dashscope.example/v1")
    monkeypatch.setenv("RAG_LLM_MODEL", "qwen-plus")
    monkeypatch.setenv("REVIEW_EXTRACTION_MODEL", "another-model")
    extractor = KnowledgePointExtractor()

    result = extractor.extract(
        LearningMaterialContext(12, "Kafka 高可用课程", "mp4"),
        [evidence("material-12-1", "副本", "Kafka 通过分区副本和 Leader 选举提升可用性。")],
    )

    assert extractor.api_key == ""
    assert extractor.base_url == REVIEW_LLM_BASE_URL
    assert extractor.model == "deepseek-v4-flash"
    assert result.extractor == f"local:{REVIEW_CARD_PROMPT_VERSION}"


def test_source_key_uses_evidence_and_content_instead_of_card_order() -> None:
    """卡片调序不能改变身份，不同知识内容也不能错误继承旧进度。"""
    reference = evidence("material-12-7", "ISR", "ISR 保存与 Leader 保持同步的副本集合。")
    second_reference = evidence("material-12-8", "ISR", "Leader 故障后会触发副本选举。")

    first = stable_source_key(
        "ISR",
        (reference, second_reference),
        "ISR 保存与 Leader 保持同步的副本集合。",
    )
    reordered = stable_source_key(
        "ISR",
        (second_reference, reference),
        "ISR 保存与 Leader 保持同步的副本集合。",
    )
    different = stable_source_key(
        "ISR",
        (reference, second_reference),
        "Leader 故障后优先从 ISR 中选举新 Leader。",
    )

    assert first == reordered
    assert first.startswith("knowledge-")
    assert first != different


def test_local_fallback_avoids_timecode_and_repeated_subtitle_questions() -> None:
    """无模型降级时不应把视频时间码或重复字幕当作知识标题。"""
    section = clean_section_name("00:03:15 - 00:03:20", "大模型微调课程")

    assert section == "大模型微调课程"
    assert build_question(section, "文字幕提供 文字幕提供 文字幕提供 文本内容", 1) == "大模型微调课程的核心内容是什么？"


def test_subtitle_noise_detector_handles_one_character_alignment_drift() -> None:
    """字幕重复片段即使出现一个字的对齐偏移也应被过滤。"""
    assert is_repetitive_noise("文字幕提供 中文字幕提供 中文字幕提供 中") is True


def test_noise_only_video_evidence_does_not_create_review_cards() -> None:
    """时间码和字幕水印即使来自课程视频，也要按非学习资料跳过。"""
    extractor = KnowledgePointExtractor(provider="local")
    result = extractor.extract(
        LearningMaterialContext(12, "大模型微调课程", "mp4", "课程视频"),
        [
            evidence(
                "material-12-noise",
                "00:03:15 - 00:03:20",
                "00:03:15 - 00:03:20 字幕提供 中文字幕提供 中文字幕提供 中文字幕提供 中文字幕提供 中",
            )
        ],
    )

    assert result.is_learning_content is False
    assert result.category == "非学习资料"
    assert result.knowledge_points == ()
    assert "无效内容" in result.reason


def test_mixed_subtitle_watermark_keeps_supported_knowledge() -> None:
    """同一片段既有重复字幕水印又有知识正文时，只删除噪声部分。"""
    extractor = KnowledgePointExtractor(provider="local")
    result = extractor.extract(
        LearningMaterialContext(12, "Kafka 高可用课程", "mp4", "Kafka 面试八股"),
        [
            evidence(
                "material-12-mixed",
                "ISR 与故障转移",
                "00:03:15 - 00:03:20 中文字幕提供 中文字幕提供 "
                "Kafka 的 ISR 保存与 Leader 保持同步的副本集合。",
            )
        ],
    )

    assert result.is_learning_content is True
    assert result.knowledge_points
    assert any("Kafka 的 ISR" in point.answer for point in result.knowledge_points)
    assert all("字幕提供" not in point.answer for point in result.knowledge_points)


def test_single_subtitle_credit_keeps_following_knowledge() -> None:
    """单次字幕署名和知识正文相邻时，只删除署名。"""
    sentences = split_knowledge_sentences(
        "字幕由 Amara.org 社区提供。Kafka 的 ISR 保存与 Leader 保持同步的副本集合。"
    )

    assert sentences == ["Kafka 的 ISR 保存与 Leader 保持同步的副本集合。"]


def test_video_call_to_action_is_noise() -> None:
    """片尾求赞关注和告别语不应成为学习卡片。"""
    assert is_generic_speech_cue("欢迎大家点赞关注收藏转发") is True
    assert is_generic_speech_cue("谢谢大家观看") is True
    assert is_generic_speech_cue("我们下期视频再见") is True
    assert is_generic_speech_cue("感谢大家观看，记得点赞关注，下期再见。") is True
    assert is_noise_fragment("字幕由 Amara.org 社区提供") is True


def test_timecode_prefix_is_removed_but_supported_knowledge_is_kept() -> None:
    """时间码后存在真实知识陈述时，只去掉时间码并保留正文。"""
    sentences = split_knowledge_sentences(
        "00:03:15 - 00:03:20 Kafka 的 ISR 保存与 Leader 保持同步的副本集合。"
    )

    assert sentences == ["Kafka 的 ISR 保存与 Leader 保持同步的副本集合。"]


def test_srt_timecode_is_removed_from_learning_content() -> None:
    """标准 SRT 毫秒时间码和箭头不能污染问题或答案。"""
    sentences = split_knowledge_sentences(
        "00:03:15,000 --> 00:03:20,000 Kafka 的 ISR 保存与 Leader 保持同步的副本集合。"
    )

    assert sentences == ["Kafka 的 ISR 保存与 Leader 保持同步的副本集合。"]
    assert clean_section_name("00:03:15,000 --> 00:03:20,000", "Kafka 课程") == "Kafka 课程"


def test_repeated_technical_concept_is_not_mistaken_for_noise() -> None:
    """多个句子重复同一技术名词时仍属于正常知识内容。"""
    paragraph = (
        "一致性哈希通过哈希环组织节点并映射数据。"
        "一致性哈希在节点扩缩容时只迁移相邻区间的数据。"
        "一致性哈希通过虚拟节点改善数据倾斜问题。"
    )

    assert is_repetitive_noise(paragraph) is False


def test_noise_only_evidence_skips_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型启用时也必须先做确定性清洗，纯噪声不得消耗 LLM 调用。"""
    calls = 0

    class UnexpectedOpenAI:
        """一旦构造模型客户端就说明前置过滤失效。"""

        def __init__(self, **_kwargs):
            nonlocal calls
            calls += 1

    monkeypatch.setattr("openai.OpenAI", UnexpectedOpenAI)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    extractor = KnowledgePointExtractor(provider="deepseek")
    result = extractor.extract(
        LearningMaterialContext(12, "技术课程", "mp4", "课程视频"),
        [evidence("noise", "00:03:15,000 --> 00:03:20,000", "字幕由 Amara.org 社区提供")],
    )

    assert calls == 0
    assert result.is_learning_content is False
    assert result.knowledge_points == ()


def test_generic_speech_cue_is_not_used_as_question_subject() -> None:
    """“也就是说”等口头转场不能成为复习问题主体。"""
    question = build_question("向量检索", "也就是说，向量数据库负责保存并检索嵌入向量。", 1)

    assert question == "向量检索的关键知识点是什么？"


def test_model_result_with_non_boolean_classification_falls_back() -> None:
    """模型输出类型不符合契约时不能把字符串 false 当成 true。"""
    extractor = KnowledgePointExtractor(provider="local")
    result = extractor._validate_model_result(
        LearningMaterialContext(12, "技术笔记", "pdf"),
        [evidence("material-12-1", "核心", "Kafka 副本机制用于保障可用性。")],
        {"isLearningContent": "false", "cards": []},
    )

    assert result is None
