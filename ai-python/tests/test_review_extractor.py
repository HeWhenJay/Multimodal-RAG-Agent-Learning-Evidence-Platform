"""本地学习过滤与 DeepSeek-only 复习内容提炼测试。"""

import json
import os
from types import SimpleNamespace

import pytest

from app.review.knowledge_extractor import (
    KnowledgePointExtractor,
    LearningMaterialContext,
    REVIEW_LLM_BASE_URL,
    REVIEW_LLM_MODEL,
    REVIEW_LLM_REASONING_EFFORT,
    ReviewExtractionError,
    answer_is_grounded,
    clean_content_text,
    clean_section_name,
    classify_learning_content,
    extract_source_question_candidates,
    extract_source_questions,
    is_generic_speech_cue,
    is_high_quality_review_question,
    is_noise_fragment,
    is_repetitive_noise,
    review_card_limit,
    sanitize_evidences,
    select_review_prompt_evidences,
    split_knowledge_sentences,
    stable_source_key,
)
from app.schemas.rag import Evidence
from prompts.review import REVIEW_CARD_PROMPT_VERSION, review_card_system_prompt


def evidence(
    evidence_id: str,
    section: str,
    snippet: str,
    *,
    position: int = 0,
    child_kind: str = "raw",
    evidence_channel: str = "subtitle",
) -> Evidence:
    """构造带视频时间段和切块来源的真实 evidence。"""
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
        metadata={
            "childKind": child_kind,
            "evidenceChannel": evidence_channel,
            "chunkPosition": position,
        },
    )


def valid_payload(*, summary: str = "资料说明 Kafka 通过 ISR 与副本选举处理 Leader 故障，并保持分区服务可用。") -> dict:
    """返回能够通过结构与 evidence 门禁的模型结果。"""
    return {
        "summary": summary,
        "cards": [
            {
                "question": "Kafka 的 ISR 在 Leader 故障转移中起什么作用？",
                "sourceQuestion": None,
                "answer": "ISR 保存与 Leader 保持同步的副本集合，Leader 故障后会优先从 ISR 中选举新 Leader。",
                "hint": "关注同步副本集合与新 Leader 的候选范围",
                "evidenceIds": ["material-12-7"],
            }
        ],
    }


def test_missing_key_fails_without_local_generated_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少密钥时必须明确失败，不能再用本地规则生成摘要或卡片。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.review.knowledge_extractor.read_process_or_windows_user_environment",
        lambda _name: "",
    )
    extractor = KnowledgePointExtractor()

    with pytest.raises(ReviewExtractionError, match="DEEPSEEK_API_KEY"):
        extractor.extract(
            LearningMaterialContext(12, "Kafka 高可用课程", "mp4"),
            [evidence("material-12-7", "ISR", "ISR 保存与 Leader 保持同步的副本集合。")],
        )


def test_local_provider_is_rejected_even_when_evidence_is_valid(monkeypatch: pytest.MonkeyPatch) -> None:
    """显式 local provider 也不能绕过 DeepSeek-only 约束。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    extractor = KnowledgePointExtractor(provider="local")

    with pytest.raises(ReviewExtractionError, match="只允许使用 DeepSeek"):
        extractor.extract(
            LearningMaterialContext(12, "Kafka 高可用课程", "mp4"),
            [evidence("material-12-7", "ISR", "ISR 保存与 Leader 保持同步的副本集合。")],
        )


def test_model_extractor_uses_one_centralized_prompt_call_per_material(monkeypatch: pytest.MonkeyPatch) -> None:
    """一份资料只调用一次 DeepSeek，并固定正式模型、官方 URL 与最高思考强度。"""
    calls: list[dict] = []
    clients: list[dict] = []
    payload = valid_payload()

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
        LearningMaterialContext(12, "Kafka 的高可用性", "pdf", "这只是 RAG 截断摘要，不应直接展示。"),
        [
            evidence(
                "material-12-7",
                "ISR",
                "ISR 保存与 Leader 保持同步的副本集合，Leader 故障后会优先从 ISR 中选举新 Leader。",
            )
        ],
    )

    assert len(calls) == 1
    assert REVIEW_CARD_PROMPT_VERSION == "review-card-v10"
    assert clients == [{"api_key": "test-key", "base_url": REVIEW_LLM_BASE_URL}]
    assert calls[0]["model"] == REVIEW_LLM_MODEL == "deepseek-v4-flash"
    assert calls[0]["reasoning_effort"] == REVIEW_LLM_REASONING_EFFORT == "max"
    assert calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in calls[0]
    assert calls[0]["messages"][0]["content"] == review_card_system_prompt()
    assert calls[0]["messages"][1]["role"] == "user"
    assert result.extractor == f"model:{REVIEW_CARD_PROMPT_VERSION}"
    assert result.summary == payload["summary"]
    assert result.summary != "这只是 RAG 截断摘要，不应直接展示。"
    assert len(result.knowledge_points) == 1


def test_extractor_retries_with_quality_feedback_and_user_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """首轮门禁失败时，复习图会把诊断和人工说明送入下一轮 Prompt。"""
    calls: list[dict] = []
    first = valid_payload()
    first["cards"][0]["answer"] = "ISR 采用量子退火算法预测消费者扩缩容。"
    responses = [first, valid_payload()]

    class FakeCompletions:
        """按顺序返回一个坏结果和一个修复结果。"""

        def create(self, **kwargs):
            calls.append(kwargs)
            payload = responses.pop(0)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))],
            )

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("REVIEW_GENERATION_MAX_ATTEMPTS", "2")
    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kwargs: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    result = KnowledgePointExtractor(provider="deepseek").extract(
        LearningMaterialContext(12, "Kafka 高可用课程", "mp4"),
        [evidence("material-12-7", "ISR", "ISR 保存与 Leader 保持同步的副本集合，Leader 故障后会优先从 ISR 中选举新 Leader。")],
        user_feedback="只关注视频原文明确讲到的 Kafka 副本机制。",
    )

    assert len(calls) == 2
    assert "用户补充说明" in calls[0]["messages"][1]["content"]
    assert "answer" in calls[1]["messages"][1]["content"]
    assert result.generation_attempts == 2
    assert result.quality_feedback


def test_extractor_refreshes_key_before_generation(monkeypatch: pytest.MonkeyPatch) -> None:
    """服务初始化时未注入密钥，随后补充环境变量也应能立即重试。"""
    payload = valid_payload()
    clients: list[dict] = []

    class FakeCompletions:
        """返回合法的 DeepSeek 复习结果。"""

        def create(self, **_kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))],
            )

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.review.knowledge_extractor.read_process_or_windows_user_environment",
        lambda name: (os.getenv(name) or "").strip(),
    )
    extractor = KnowledgePointExtractor(provider="deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "late-test-key")
    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **kwargs: clients.append(kwargs) or SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions()),
        ),
    )

    result = extractor.extract(
        LearningMaterialContext(12, "Kafka 的高可用性课程", "mp4"),
        [evidence("material-12-7", "ISR", "ISR 保存与 Leader 保持同步的副本集合。")],
    )

    assert clients == [{"api_key": "late-test-key", "base_url": REVIEW_LLM_BASE_URL}]
    assert result.knowledge_points


def test_source_question_is_audited_but_final_question_uses_deepseek_polished_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """原问句只作来源审计，最终卡面采用 DeepSeek 输出的自包含问题。"""
    calls: list[dict] = []
    original_question = "这时候面试官可能会问，那 Kafka 为什么能继续服务？"
    payload = valid_payload()
    payload["cards"][0].update(
        {
            "question": "Kafka 为什么能在 Leader 故障后继续提供分区服务？",
            "sourceQuestion": original_question,
        }
    )

    class FakeCompletions:
        """返回带原问句审计字段的模型结果。"""

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload, ensure_ascii=False)))]
            )

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kwargs: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    result = KnowledgePointExtractor(provider="deepseek").extract(
        LearningMaterialContext(12, "Kafka 高可用课程", "mp4"),
        [
            evidence(
                "material-12-7",
                "故障转移",
                f"{original_question}ISR 保存与 Leader 保持同步的副本集合，Leader 故障后会优先从 ISR 中选举新 Leader。",
            )
        ],
    )

    assert original_question in calls[0]["messages"][1]["content"]
    assert result.knowledge_points[0].question == "Kafka 为什么能在 Leader 故障后继续提供分区服务？"


def test_unreferenced_source_question_causes_quality_failure() -> None:
    """模型声称沿用的原问句不属于引用 evidence 时，整张坏卡不得发布。"""
    extractor = KnowledgePointExtractor(provider="deepseek")
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

    with pytest.raises(ReviewExtractionError, match="质量门禁"):
        extractor._validate_model_result(
            LearningMaterialContext(12, "Kafka 高可用课程", "mp4"),
            [first, second],
            {
                "isLearningContent": True,
                "category": "课程复习",
                "reason": "包含核心机制",
                "summary": "资料讲解 Kafka 的副本机制与 ISR 选举范围，并说明二者在故障恢复中的作用。",
                "cards": [
                    {
                        "question": "Kafka 的副本机制在节点故障时有什么作用？",
                        "sourceQuestion": "ISR 为什么影响 Leader 选举？",
                        "answer": "多个副本用于在节点故障时保留分区数据。",
                        "hint": "关注节点故障后的分区数据保留方式",
                        "evidenceIds": ["material-12-first"],
                    }
                ],
            },
        )


@pytest.mark.parametrize(
    "question",
    [
        "那什么意思呢？",
        "那这些到底什么意思呢？",
        "父段摘要：那这些是什么意思呢？",
        "它最主要的功能就是确定访问哪个版本",
        "就必须先搞定 MVCC 具体是如何实现的",
        "本节的关键知识点是什么？",
        "这段主要讲了什么？",
    ],
)
def test_quality_gate_rejects_contextless_or_non_question_cards(question: str) -> None:
    """用户反馈的无上下文代词、父段摘要和陈述句必须全部被拒绝。"""
    assert is_high_quality_review_question(question) is False


def test_quality_gate_accepts_self_contained_core_questions() -> None:
    """围绕视频明确重点形成的完整问题应通过门禁。"""
    assert is_high_quality_review_question("事务的隔离性如何由锁和 MVCC 共同保证？") is True
    assert is_high_quality_review_question("MVCC 的隐藏字段、undo log 与 Read View 如何协作？") is True
    assert is_high_quality_review_question("RC 与 RR 隔离级别生成 Read View 的时机有什么区别？") is True


def test_source_question_candidates_ignore_parent_summary_and_ocr() -> None:
    """父段摘要和 OCR 文案不能冒充视频原始问句候选。"""
    raw = evidence("raw", "原文", "事务的隔离性如何保证？隔离性由锁和 MVCC 共同保证。")
    summary = evidence(
        "summary",
        "摘要",
        "父段摘要：那这些是什么意思呢？",
        child_kind="summary",
    )
    ocr = evidence(
        "ocr",
        "画面",
        "那主要访问哪个版本呢？",
        child_kind="ocr_occurrence",
        evidence_channel="frame_ocr",
    )

    assert extract_source_question_candidates([summary, ocr, raw]) == [
        {"evidenceId": "raw", "question": "事务的隔离性如何保证？"}
    ]


def test_source_question_extraction_handles_asr_comma_instead_of_question_mark() -> None:
    """ASR 把问号转成逗号时，明确疑问句仍可作为来源候选。"""
    source = evidence(
        "material-12-asr-question",
        "故障恢复",
        "Kafka 为什么能够在 Broker 故障后继续提供服务呢，因为 ISR 可以参与新的 Leader 选举。",
    )

    assert extract_source_questions(source) == ["Kafka 为什么能够在 Broker 故障后继续提供服务呢"]


def test_source_question_extraction_filters_kafka_speech_rhetorical_noise() -> None:
    """Kafka 真实失败样例中的确认句、承接句和无主题追问不能触发完整覆盖门禁。"""
    source = evidence(
        "material-12-kafka-speech",
        "Kafka 高性能设计",
        (
            "Kafka 中实现高性能的设计有了解过吗？"
            "第一个是消息分区，分区的好处是不受单台服务器限制，能处理更多数据，对吧？"
            "是不是很方便的？我们再来看下面这个图，如果也要找一二三四五六七，该怎么办呢？"
            "首先是一，然后是二，再到三，它们是不是不连续啊？然后是四、五，你看又跳了对不对？"
            "该怎么办呢？在缓存中操作数据，是不是就提升了性能呢？"
            "零拷贝为什么能减少磁盘 IO 和网络 IO？它到底是怎么做的呢？"
            "在 Linux 系统中主要划分用户空间和内核空间，对吧？"
        ),
    )

    assert extract_source_questions(source) == [
        "Kafka 中实现高性能的设计有了解过吗？",
        "零拷贝为什么能减少磁盘 IO 和网络 IO？",
    ]


def test_kafka_speech_questions_do_not_trigger_structured_full_coverage() -> None:
    """大量上下文依赖追问即使超过 8 个，也不能被误判成必须逐项覆盖的问题清单。"""
    questions = [
        {"evidenceId": f"speech-{index}", "question": question}
        for index, question in enumerate(
            [
                "这个问的是 Kafka 中实现高性能的设计有了解过吗？",
                "那这个怎么做呢？",
                "消费者要消费这个消息怎么办呢？",
                "现在需要把这个消息发送给消费者，怎么做呢？",
                "大家回想一下刚才数据拷贝了几次呢？",
                "这个流程拷贝了几次呢？",
                "是不是只有两次啊？",
                "数据拷贝变少后性能更高，这个没问题吧？",
                "面试官问的是：Kafka 中实现高性能的设计有了解过吗？",
            ]
        )
    ]

    assert review_card_limit(questions) == 8


def test_review_model_does_not_inherit_proxy_or_other_provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺少 DeepSeek 密钥时必须失败，不能借用代理、百炼或通用 RAG 配置。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("SUBAI_BASE_URL", "https://proxy.example/v1")
    monkeypatch.setenv("SU_BAI_API_KEY", "proxy-key")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dashscope-key")
    monkeypatch.setenv("RAG_LLM_MODEL", "qwen-plus")
    monkeypatch.setattr(
        "app.review.knowledge_extractor.read_process_or_windows_user_environment",
        lambda _name: "",
    )
    extractor = KnowledgePointExtractor()

    with pytest.raises(ReviewExtractionError, match="DEEPSEEK_API_KEY"):
        extractor.extract(
            LearningMaterialContext(12, "Kafka 高可用课程", "mp4"),
            [evidence("material-12-1", "副本", "Kafka 通过分区副本和 Leader 选举提升可用性。")],
        )

    assert extractor.api_key == ""
    assert extractor.base_url == REVIEW_LLM_BASE_URL
    assert extractor.model == "deepseek-v4-flash"


def test_invalid_model_content_structure_raises_instead_of_falling_back() -> None:
    """缺少总结和卡片的非法模型结构必须失败，不能触发本地内容降级。"""
    extractor = KnowledgePointExtractor(provider="deepseek")

    with pytest.raises(ReviewExtractionError, match="资料总结"):
        extractor._validate_model_result(
            LearningMaterialContext(12, "技术笔记", "pdf"),
            [evidence("material-12-1", "核心", "Kafka 副本机制用于保障可用性。")],
            {"cards": []},
        )


def test_quality_gate_keeps_valid_cards_when_other_cards_are_invalid() -> None:
    """非结构化资料应采用部分成功，少量坏卡不能拖死整份已通过门禁的卡片。"""
    extractor = KnowledgePointExtractor(provider="deepseek")
    reference = evidence(
        "material-12-7",
        "ISR",
        "ISR 保存与 Leader 保持同步的副本集合，Leader 故障后会优先从 ISR 中选举新 Leader。",
    )
    payload = valid_payload()
    payload["cards"].append(
        {
            "question": "那到底怎么办？",
            "sourceQuestion": None,
            "answer": "ISR 采用量子退火算法预测消费者扩缩容。",
            "hint": "回忆一下",
            "evidenceIds": [],
        }
    )

    result = extractor._validate_model_result(
        LearningMaterialContext(12, "Kafka 高可用课程", "mp4"),
        [reference],
        payload,
    )

    assert len(result.knowledge_points) == 1
    assert result.knowledge_points[0].question == payload["cards"][0]["question"]
    assert any("卡片 2" in item for item in result.quality_feedback)


def test_empty_json_response_retries_without_spending_graph_quality_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    """空响应应在当前模型轮内重试，不能直接消耗一次 LangGraph 质量修复。"""
    calls = 0

    class FakeCompletions:
        """先返回空内容，再返回合法结果。"""

        def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            content = "" if calls == 1 else json.dumps(valid_payload(), ensure_ascii=False)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    monkeypatch.setattr("app.review.knowledge_extractor.time.sleep", lambda _seconds: None)
    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **_kwargs: SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions())),
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    result = KnowledgePointExtractor(provider="deepseek").extract(
        LearningMaterialContext(12, "Kafka 高可用课程", "mp4"),
        [evidence("material-12-7", "ISR", "ISR 保存与 Leader 保持同步的副本集合，Leader 故障后会优先从 ISR 中选举新 Leader。")],
    )

    assert calls == 2
    assert result.generation_attempts == 1
    assert result.knowledge_points


def test_representative_evidence_sampling_covers_whole_video_and_prefers_raw() -> None:
    """大量父段摘要不能再次垄断输入，长视频首尾原始 transcript 都要进入模型。"""
    summaries = [
        evidence(f"summary-{index}", "摘要", f"父段摘要：第 {index} 段概览。", position=100 + index, child_kind="summary")
        for index in range(48)
    ]
    raw = [
        evidence(f"raw-{index}", "原文", f"第 {index} 段讲解 MVCC 的具体机制和事务版本选择。", position=index)
        for index in range(30)
    ]

    selected = sanitize_evidences(summaries + raw)
    selected_ids = {item.evidenceId for item in selected}

    assert len(selected) <= 16
    assert "raw-0" in selected_ids
    assert "raw-29" in selected_ids
    assert sum(item.evidenceId.startswith("raw-") for item in selected) >= 12


def test_structured_video_preserves_twenty_explicit_questions_and_answer_neighbors() -> None:
    """视频已经列出的 20 个问题不能再被 8 张上限或 16 段采样截断。"""
    topics = [
        "列表", "元组", "字典", "集合", "浅拷贝", "深拷贝", "生成器", "迭代器", "装饰器", "上下文管理器",
        "断言", "过滤器", "匿名函数", "可变参数", "类继承", "抽象类", "垃圾回收", "全局解释器锁", "堆维护", "Top K",
    ]
    evidences: list[Evidence] = []
    cards: list[dict] = []
    for index, topic in enumerate(topics):
        question = f"Python 的{topic}在面试资料中有什么核心特性？"
        answer = f"Python 的{topic}核心特性是按原视频给出的规则完成对应操作。"
        evidence_id = f"question-{index}"
        evidences.extend(
            [
                evidence(evidence_id, topic, f"{question}{answer}", position=index * 2),
                evidence(f"answer-{index}", topic, f"补充说明：{answer}", position=index * 2 + 1),
            ]
        )
        cards.append(
            {
                "question": question,
                "sourceQuestion": question,
                "answer": answer,
                "hint": f"关注{topic}在原视频中的定义与操作规则",
                "evidenceIds": [evidence_id],
            }
        )

    source_questions = extract_source_question_candidates(evidences)
    selected = select_review_prompt_evidences(evidences, source_questions)
    result = KnowledgePointExtractor(provider="deepseek")._validate_model_result(
        LearningMaterialContext(12, "Python 基础面经", "mp4"),
        selected,
        {
            "summary": "视频按二十个明确问题讲解 Python 基础面试考点，并逐项给出定义、行为和使用方式。",
            "cards": cards,
        },
        source_questions=source_questions,
    )

    selected_ids = {item.evidenceId for item in selected}
    assert len(source_questions) == 20
    assert review_card_limit(source_questions) == 20
    assert all(f"question-{index}" in selected_ids for index in range(20))
    assert all(f"answer-{index}" in selected_ids for index in range(20))
    assert len(result.knowledge_points) == 20


def test_answer_grounding_requires_overlap_with_referenced_evidence() -> None:
    """答案需有成段事实覆盖，单个技术词重合和明显外部补写均不得发布。"""
    reference = evidence("material-12-7", "ISR", "ISR 保存与 Leader 保持同步的副本集合。")

    assert answer_is_grounded("ISR 是与 Leader 保持同步的副本集合。", (reference,)) is True
    assert answer_is_grounded("ISR 采用量子退火算法预测消费者扩缩容。", (reference,)) is False
    assert answer_is_grounded("Raft 使用随机选举超时避免多个候选者长期冲突。", (reference,)) is False


def test_answer_grounding_rejects_unsupported_claim_after_supported_sentence() -> None:
    """答案先复述原文、再追加整句外部知识时，追加事实也必须单独通过 evidence 校验。"""
    reference = evidence("material-12-7", "ISR", "ISR 保存与 Leader 保持同步的副本集合。")
    answer = (
        "ISR 是与 Leader 保持同步的副本集合。"
        "此外，它使用量子退火和区块链共识确保消息永不丢失，并通过全球时钟解决 CAP 矛盾。"
    )

    assert answer_is_grounded(answer, (reference,)) is False


def test_answer_grounding_rejects_unsupported_claim_after_comma_connector() -> None:
    """同一句中由连接词追加的外部事实也必须单独通过 evidence 校验。"""
    reference = evidence("material-12-7", "ISR", "ISR 保存与 Leader 保持同步的副本集合。")
    answer = (
        "ISR 保存与 Leader 保持同步的副本集合，此外，它使用量子退火和区块链共识确保消息永不丢失，"
        "并通过全球时钟解决 CAP 矛盾。"
    )

    assert answer_is_grounded(answer, (reference,)) is False
    assert answer_is_grounded(
        "ISR 保存与 Leader 保持同步的副本集合，并通过全球时钟解决 CAP 矛盾。",
        (reference,),
    ) is False


def test_answer_grounding_accepts_multiple_supported_claims() -> None:
    """连接词拆分不能误伤由同一 evidence 明确支撑的多个事实。"""
    reference = evidence(
        "material-12-7",
        "ISR",
        "ISR 保存与 Leader 保持同步的副本集合，Leader 故障后会从 ISR 中选举新 Leader。",
    )
    answer = (
        "ISR 是与 Leader 保持同步的副本集合，"
        "并且 Leader 故障后会从 ISR 中选举新 Leader。"
    )

    assert answer_is_grounded(answer, (reference,)) is True


def test_source_key_uses_evidence_and_content_instead_of_card_order() -> None:
    """卡片调序不能改变身份，不同知识内容也不能错误继承旧进度。"""
    reference = evidence("material-12-7", "ISR", "ISR 保存与 Leader 保持同步的副本集合。")
    second_reference = evidence("material-12-8", "ISR", "Leader 故障后会触发副本选举。")

    first = stable_source_key("ISR", (reference, second_reference), "ISR 保存与 Leader 保持同步的副本集合。")
    reordered = stable_source_key("ISR", (second_reference, reference), "ISR 保存与 Leader 保持同步的副本集合。")
    different = stable_source_key("ISR", (reference, second_reference), "Leader 故障后优先从 ISR 中选举新 Leader。")

    assert first == reordered
    assert first.startswith("knowledge-")
    assert first != different


def test_subtitle_and_ocr_noise_is_removed_without_deleting_knowledge() -> None:
    """时间码、父段前缀、OCR 广告与字幕署名应被清掉，真实知识继续保留。"""
    cleaned = clean_content_text(
        "父段摘要：OCR 出现时间：00:05:15 视频画面聚合 00:04:20 - 00:05:15 "
        "多一句没有，少一句不行，用更短时间，教会更实用的技术！"
        "MVCC 依赖隐藏字段、undo log 和 Read View。高级软件人才培训专家"
    )

    assert cleaned == "MVCC 依赖隐藏字段、undo log 和 Read View。"
    assert is_repetitive_noise("文字幕提供 中文字幕提供 中文字幕提供 中") is True
    assert split_knowledge_sentences(
        "字幕由 Amara.org 社区提供。Kafka 的 ISR 保存与 Leader 保持同步的副本集合。"
    ) == ["Kafka 的 ISR 保存与 Leader 保持同步的副本集合。"]


def test_noise_only_evidence_is_skipped_by_local_prefilter(monkeypatch: pytest.MonkeyPatch) -> None:
    """纯噪声应在送模前确定性跳过，不浪费 DeepSeek 调用。"""
    calls = 0

    class UnexpectedOpenAI:
        """一旦构造模型客户端就说明前置过滤失效。"""

        def __init__(self, **_kwargs):
            nonlocal calls
            calls += 1

    monkeypatch.setattr("openai.OpenAI", UnexpectedOpenAI)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    result = KnowledgePointExtractor(provider="deepseek").extract(
        LearningMaterialContext(12, "技术课程", "mp4", "课程视频"),
        [evidence("noise", "00:03:15,000 --> 00:03:20,000", "字幕由 Amara.org 社区提供")],
    )

    assert calls == 0
    assert result.is_learning_content is False
    assert result.extractor == f"filter:{REVIEW_CARD_PROMPT_VERSION}"


def test_local_prefilter_skips_misc_content_and_accepts_study_material() -> None:
    """本地过滤只做送模决策，能拦截杂项并放行结构化学习资料。"""
    misc = classify_learning_content(
        LearningMaterialContext(1, "会议纪要", "docx"),
        [evidence("misc", "全文", "今天讨论项目排期和参会人员安排。")],
    )
    study = classify_learning_content(
        LearningMaterialContext(2, "Kafka 高可用面试题", "mp4"),
        [evidence("study", "副本机制", "Kafka 如何通过 ISR 和 Leader 选举保证高可用？")],
    )

    assert misc[0] is False
    assert study[0] is True
    assert study[1] == "面试复习"


def test_local_prefilter_uses_title_intent_without_overfiltering_technical_courses() -> None:
    """杂项标题优先跳过，显式讲解系统日志的课程仍可进入复习生成。"""
    log_dump = classify_learning_content(
        LearningMaterialContext(3, "数据库系统日志", "txt"),
        [evidence("log", "全文", "ERROR connection timeout at 03:15:20")],
    )
    log_course = classify_learning_content(
        LearningMaterialContext(4, "系统日志排查教程", "mp4"),
        [evidence("course", "排查流程", "系统日志有什么作用？排查时通过错误码定位失败原因。")],
    )

    assert log_dump[0] is False
    assert log_course[0] is True


def test_basic_noise_helpers_keep_technical_content() -> None:
    """口播噪声被识别，重复技术概念仍保留。"""
    paragraph = (
        "一致性哈希通过哈希环组织节点并映射数据。"
        "一致性哈希在节点扩缩容时只迁移相邻区间的数据。"
        "一致性哈希通过虚拟节点改善数据倾斜问题。"
    )

    assert clean_section_name("00:03:15 - 00:03:20", "大模型微调课程") == "大模型微调课程"
    assert is_generic_speech_cue("欢迎大家点赞关注收藏转发") is True
    assert is_generic_speech_cue("感谢大家观看，记得点赞关注，下期再见。") is True
    assert is_noise_fragment("字幕由 Amara.org 社区提供") is True
    assert is_repetitive_noise(paragraph) is False
