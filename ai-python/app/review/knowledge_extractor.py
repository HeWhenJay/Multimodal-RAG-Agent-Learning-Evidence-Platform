"""从既有 RAG evidence 提炼适合主动回忆的短知识点。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
from typing import Any

from app.core.environment import read_process_or_windows_user_environment
from app.schemas.rag import Evidence
from prompts.review import (
    REVIEW_CARD_PROMPT_VERSION,
    review_card_system_prompt,
    review_card_user_prompt,
)


logger = logging.getLogger(__name__)
REVIEW_LLM_MODEL = "deepseek-v4-flash"
REVIEW_LLM_REASONING_EFFORT = "max"
REVIEW_LLM_BASE_URL = "https://api.deepseek.com"
TIMECODE_TOKEN_PATTERN = r"\d{1,2}:\d{2}(?::\d{2})?(?:[,.]\d{1,3})?"
TIMECODE_RANGE_PATTERN = (
    rf"\s*[\[(]?{TIMECODE_TOKEN_PATTERN}"
    rf"(?:\s*(?:-->|[-至~～—–])\s*{TIMECODE_TOKEN_PATTERN})?[\])]?\s*"
)
SUBTITLE_CREDIT_PATTERN = r"字幕由.{1,40}?(?:提供|制作)"
LEARNING_INTENT_KEYWORDS = (
    "八股", "面经", "面试题", "课程", "教程", "知识点", "技术讲解",
    "学习笔记", "复习", "源码分析", "教学", "讲解",
)
LEARNING_TOPIC_KEYWORDS = (
    "原理", "机制", "高可用", "分布式", "算法", "数据库", "缓存",
    "消息队列", "向量检索", "大模型", "事务", "并发", "架构",
)
NON_LEARNING_KEYWORDS = (
    "个人简历", "求职简历", "招聘职位", "岗位描述", "职位描述", "会议纪要",
    "工作周报", "工作日报", "聊天记录", "通知公告", "系统日志", "错误日志",
    "上传清单", "账单", "发票", "歌词", "声乐练习",
)
ANSWER_CLAIM_CONNECTOR_PATTERN = (
    r"此外|另外|同时|而且|并且|但是|然而|因此|所以|从而|这意味着|这说明|"
    r"(?:它|其|该(?:机制|系统|方法|算法|功能|组件|方案))"
    r"(?:使用|采用|通过|依赖|负责|保证|支持|实现|解决|导致|需要|可以|能够|会|将|必须|应当)|"
    r"并(?:使用|采用|通过|依赖|负责|保证|支持|实现|解决|导致|需要|可以|能够|会|将)"
)


@dataclass(frozen=True)
class LearningMaterialContext:
    """分类和提炼所需的最小资料上下文。"""

    material_id: int
    title: str
    document_type: str
    summary: str | None = None


@dataclass(frozen=True)
class KnowledgePoint:
    """一条带真实 evidence 的关键知识点。"""

    source_key: str
    question: str
    answer: str
    hint: str | None
    evidence_refs: tuple[Evidence, ...]


@dataclass(frozen=True)
class ExtractionResult:
    """资料分类与知识点提炼结果。"""

    is_learning_content: bool
    category: str | None
    reason: str
    knowledge_points: tuple[KnowledgePoint, ...]
    extractor: str
    summary: str | None = None


class ReviewExtractionError(RuntimeError):
    """DeepSeek 复习内容未能生成或未通过质量门禁。"""


class KnowledgePointExtractor:
    """只使用 DeepSeek 生成复习内容，本地代码仅清洗和拒绝坏结果。"""

    def __init__(
        self,
        *,
        provider: str | None = None,
    ) -> None:
        # 复习模型固定走 DeepSeek 官方入口，避免误继承通用 RAG 或代理配置。
        self.provider = (provider or os.getenv("REVIEW_EXTRACTION_PROVIDER") or "auto").strip().lower()
        self.api_key = read_process_or_windows_user_environment("DEEPSEEK_API_KEY")
        self.model = REVIEW_LLM_MODEL
        self.reasoning_effort = REVIEW_LLM_REASONING_EFFORT
        self.timeout_seconds = float(os.getenv("REVIEW_EXTRACTION_TIMEOUT_SECONDS", "120"))
        self.base_url = REVIEW_LLM_BASE_URL

    def extract(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
    ) -> ExtractionResult:
        """只根据传入 evidence 调用 DeepSeek，失败时不发布任何降级内容。"""
        # 提取器通常随 FastAPI 一起初始化；本地开发时用户可能在服务启动后才补充环境变量。
        # 每次生成前刷新一次密钥，但仍只允许使用 DEEPSEEK_API_KEY，绝不借用其他供应商配置。
        self.api_key = read_process_or_windows_user_environment("DEEPSEEK_API_KEY")
        usable = sanitize_evidences(deduplicate_evidences(evidences))
        if not usable:
            return ExtractionResult(
                False,
                "非学习资料",
                "资料清洗后仅剩时间码、字幕水印、重复字幕或口头语等无效内容",
                (),
                f"filter:{REVIEW_CARD_PROMPT_VERSION}",
                None,
            )
        is_learning, category, reason = classify_learning_content(material, usable)
        if not is_learning:
            return ExtractionResult(
                False, category, reason, (), f"filter:{REVIEW_CARD_PROMPT_VERSION}", None
            )
        if self.provider not in {"auto", "deepseek"}:
            raise ReviewExtractionError("复习内容只允许使用 DeepSeek 生成")
        if not self.api_key:
            raise ReviewExtractionError("未配置 DEEPSEEK_API_KEY，无法生成复习内容")
        try:
            modeled = self._extract_with_model(material, usable)
            return ExtractionResult(
                True, category, reason, modeled.knowledge_points, modeled.extractor, modeled.summary
            )
        except ReviewExtractionError:
            raise
        except json.JSONDecodeError as exc:
            logger.warning("DeepSeek 复习内容响应不是合法 JSON")
            raise ReviewExtractionError("DeepSeek 返回的复习内容格式无效，请重新生成") from exc
        except Exception as exc:
            logger.exception("DeepSeek 生成复习内容失败")
            raise ReviewExtractionError("DeepSeek 复习内容生成失败，请稍后重新生成") from exc

    def _extract_with_model(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
    ) -> ExtractionResult:
        """调用 DeepSeek 官方 OpenAI 兼容接口并校验摘要、问句与 evidence 引用。"""
        from openai import OpenAI

        source_questions = extract_source_question_candidates(evidences)
        evidence_payload = [
            {
                "evidenceId": item.evidenceId,
                "sectionName": item.sectionName,
                "snippet": item.snippet,
            }
            for item in evidences[:16]
        ]
        prompt = review_card_user_prompt(
            title=material.title,
            document_type=material.document_type,
            summary=material.summary or "",
            evidences=evidence_payload,
            source_questions=source_questions,
        )
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        response = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": review_card_system_prompt()},
                {"role": "user", "content": prompt},
            ],
            reasoning_effort=self.reasoning_effort,
            response_format={"type": "json_object"},
            extra_body={"thinking": {"type": "enabled"}},
            timeout=self.timeout_seconds,
        )
        content = response.choices[0].message.content or ""
        payload = parse_json_object(content)
        return self._validate_model_result(
            material,
            evidences,
            payload,
            source_questions=source_questions,
        )

    def _validate_model_result(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
        payload: dict[str, Any],
        *,
        source_questions: list[dict[str, str]] | None = None,
    ) -> ExtractionResult:
        """只发布结构完整、可独立理解且带有效 evidence 的 DeepSeek 结果。"""
        summary = normalize_generated_summary(payload.get("summary"))
        if summary is None:
            raise ReviewExtractionError("DeepSeek 未生成有效的资料总结")
        evidence_by_id = {item.evidenceId: item for item in evidences}
        question_candidates = (
            extract_source_question_candidates(evidences)
            if source_questions is None
            else source_questions
        )
        points: list[KnowledgePoint] = []
        seen_questions: set[str] = set()
        raw_cards = payload.get("cards")
        if not isinstance(raw_cards, list):
            raise ReviewExtractionError("DeepSeek 未返回有效的复习卡片数组")
        for raw in raw_cards:
            if not isinstance(raw, dict):
                continue
            answer = normalize_answer_text(raw.get("answer"), 600)
            if not answer:
                continue
            raw_evidence_ids = raw.get("evidenceIds")
            evidence_ids = raw_evidence_ids if isinstance(raw_evidence_ids, list) else []
            refs = tuple(
                evidence_by_id[evidence_id]
                for evidence_id in evidence_ids
                if evidence_id in evidence_by_id
            )[:2]
            if not refs:
                continue
            if is_noise_fragment(answer) or not answer_is_grounded(answer, refs):
                continue
            raw_source_question = compact_text(raw.get("sourceQuestion"), 180)
            source_question = validated_source_question(raw_source_question, refs, question_candidates)
            if raw_source_question and source_question is None:
                continue
            question = compact_text(raw.get("question"), 180)
            hint = compact_text(raw.get("hint"), 180)
            if not question or not is_high_quality_review_question(question):
                continue
            if not hint or not is_high_quality_review_hint(hint):
                continue
            section = clean_section_name(refs[0].sectionName, material.title)
            question_key = normalized_sentence(question)
            if not question_key or question_key in seen_questions:
                continue
            seen_questions.add(question_key)
            points.append(
                KnowledgePoint(
                    source_key=stable_source_key(section, refs, answer),
                    question=question,
                    answer=answer,
                    hint=hint,
                    evidence_refs=refs,
                )
            )
        if not points:
            raise ReviewExtractionError("DeepSeek 生成的卡片未通过问题完整性与 evidence 质量门禁")
        return ExtractionResult(
            True,
            None,
            "DeepSeek 已生成复习内容",
            tuple(points[:8]),
            f"model:{REVIEW_CARD_PROMPT_VERSION}",
            summary,
        )


def deduplicate_evidences(evidences: list[Evidence]) -> list[Evidence]:
    """按正文内容去重，不让父段摘要挤占原始 transcript 的输入预算。"""
    result: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    for item in evidences:
        snippet = compact_text(item.snippet, 600)
        if not snippet:
            continue
        key = (item.sectionName.strip(), normalized_sentence(snippet))
        if key in seen:
            continue
        seen.add(key)
        result.append(item.model_copy(update={"snippet": snippet}))
    return result


def classify_learning_content(
    material: LearningMaterialContext,
    evidences: list[Evidence],
) -> tuple[bool, str, str]:
    """在调用 DeepSeek 前用可解释信号过滤杂项，只决定是否送模和资料类别。"""
    title = material.title.lower()
    corpus = " ".join(
        [material.title, material.summary or "", *(item.sectionName for item in evidences), *(item.snippet for item in evidences)]
    ).lower()
    intent_hits = [keyword for keyword in LEARNING_INTENT_KEYWORDS if keyword in corpus]
    title_intent_hits = [keyword for keyword in LEARNING_INTENT_KEYWORDS if keyword in title]
    topic_hits = [keyword for keyword in LEARNING_TOPIC_KEYWORDS if keyword in corpus]
    negative_hits = [keyword for keyword in NON_LEARNING_KEYWORDS if keyword in corpus]
    negative_title_hits = [keyword for keyword in NON_LEARNING_KEYWORDS if keyword in title]
    question_like = len(re.findall(r"[？?]|为什么|如何|是什么|区别|作用|流程", corpus))
    knowledge_statements = len(
        re.findall(
            r"是指|用于|通过|包括|分为|原因|区别|优点|缺点|步骤|机制|原理|实现|"
            r"保证|负责|依赖|同步|选举|配置|组成|采用|导致|解决|比较",
            corpus,
        )
    )
    # 标题明确是简历、日志、歌词等杂项时，只有同一标题显式标注课程/教程/讲解才允许继续。
    if negative_title_hits and not title_intent_hits:
        return False, "非学习资料", f"标题命中非学习资料特征：{negative_title_hits[0]}"
    structured_score = min(question_like, 2) + min(knowledge_statements, 2)
    if not intent_hits and not (topic_hits and structured_score >= 2) and structured_score < 4:
        if negative_hits:
            return False, "非学习资料", f"命中非学习资料特征：{negative_hits[0]}"
        return False, "非学习资料", "未发现足够明确的学习内容特征"
    category = infer_learning_category(corpus)
    signal = "、".join([*intent_hits, *topic_hits][:3]) or "结构化问答与知识陈述"
    return True, category, f"本地前置过滤命中学习内容特征：{signal}"


def infer_learning_category(corpus: str) -> str:
    """本地过滤阶段只生成内部分类标签，不生成任何面向用户的复习正文。"""
    if any(word in corpus for word in ("面经", "面试题", "八股", "面试官")):
        return "面试复习"
    if any(word in corpus for word in ("课程", "教程", "视频", "讲解")):
        return "课程复习"
    if any(word in corpus for word in ("原理", "机制", "算法", "高可用", "分布式")):
        return "技术原理"
    return "学习资料"


def sanitize_evidences(evidences: list[Evidence]) -> list[Evidence]:
    """进入模型前移除噪声，并在整份资料中均匀选择代表性 evidence。"""
    result: list[Evidence] = []
    for item in evidences:
        snippet = clean_content_text(item.snippet)
        if is_noise_fragment(snippet):
            continue
        cleaned = compact_text(snippet, 600)
        if cleaned:
            result.append(item.model_copy(update={"snippet": cleaned}))
    return select_representative_evidences(result, limit=16)


def select_representative_evidences(evidences: list[Evidence], *, limit: int) -> list[Evidence]:
    """优先均匀覆盖原始正文，再补充少量视觉 OCR，避免只看到视频开头。"""
    primary: list[Evidence] = []
    visual: list[Evidence] = []
    summaries: list[Evidence] = []
    for item in evidences:
        metadata = item.metadata or {}
        child_kind = str(metadata.get("childKind") or "")
        evidence_channel = str(metadata.get("evidenceChannel") or "")
        if child_kind in {"summary", "video_segment_summary"}:
            summaries.append(item)
        elif child_kind == "ocr_occurrence" or evidence_channel == "frame_ocr":
            visual.append(item)
        else:
            primary.append(item)

    selected = evenly_sample(primary, min(12, limit))
    remaining = limit - len(selected)
    if remaining > 0:
        visual_quota = min(4, remaining)
        selected.extend(evenly_sample(visual, visual_quota))
        remaining = limit - len(selected)
    if remaining > 0:
        selected.extend(evenly_sample(summaries, remaining))
        remaining = limit - len(selected)
    if remaining > 0:
        selected.extend(evenly_sample(primary[len(selected) :], remaining))
    return sorted(selected[:limit], key=evidence_position)


def evenly_sample(items: list[Evidence], limit: int) -> list[Evidence]:
    """保留首尾并均匀抽取中间片段，让长资料的后半段也能参与出题。"""
    if limit <= 0 or not items:
        return []
    if len(items) <= limit:
        return list(items)
    if limit == 1:
        return [items[len(items) // 2]]
    indexes = {
        round(index * (len(items) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [items[index] for index in sorted(indexes)]


def evidence_position(item: Evidence) -> int:
    """从 metadata 读取稳定位置，缺失时放在已知片段之后。"""
    raw = (item.metadata or {}).get("chunkPosition")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 2**31 - 1


def extract_source_question_candidates(evidences: list[Evidence]) -> list[dict[str, str]]:
    """提取带 evidence 归属的原始问句，供模型选择和服务端校验。"""
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for evidence in evidences:
        metadata = evidence.metadata or {}
        if str(metadata.get("childKind") or "") in {"summary", "video_segment_summary", "ocr_occurrence"}:
            continue
        if str(metadata.get("evidenceChannel") or "") == "frame_ocr":
            continue
        for question in extract_source_questions(evidence):
            key = (evidence.evidenceId, normalized_sentence(question))
            if key in seen:
                continue
            seen.add(key)
            result.append({"evidenceId": evidence.evidenceId, "question": question})
            if len(result) >= 32:
                return result
    return result


def extract_source_questions(evidence: Evidence) -> list[str]:
    """从正文和疑问式章节名中保留资料已经提出的原始问题。"""
    result: list[str] = []
    seen: set[str] = set()
    cleaned = clean_content_text(evidence.snippet)
    for match in re.finditer(r"(?:^|(?<=[。！？!?；;]))\s*([^。！？!?；;]{2,180}[？?])", cleaned):
        question = compact_text(match.group(1), 180)
        if not question or not is_meaningful_source_question(question):
            continue
        key = normalized_sentence(question)
        if key in seen:
            continue
        seen.add(key)
        result.append(question)

    # ASR 有时把疑问语气转写成逗号，保留带明确疑问词且不是“本节介绍……”的原始短句。
    for clause in re.split(r"[，,。；;！？!?]", cleaned):
        question = compact_text(clause, 180)
        if not question or not looks_like_question(question) or not is_meaningful_source_question(question):
            continue
        key = normalized_sentence(question)
        if key in seen:
            continue
        seen.add(key)
        result.append(question)

    section = compact_text(evidence.sectionName, 180)
    if section and looks_like_question(section) and is_meaningful_source_question(section):
        key = normalized_sentence(section)
        if key not in seen:
            result.append(section)
    return result[:6]


def is_meaningful_source_question(value: str) -> bool:
    """排除寒暄、确认和无知识目标的反问。"""
    compact = " ".join(value.split()).strip()
    normalized = normalized_sentence(compact)
    if len(normalized) < 5 or is_noise_fragment(compact):
        return False
    return not bool(
        re.fullmatch(
            r"(?:大家)?(?:明白|懂|清楚|记住|学会|看懂)(?:了)?(?:吗|没有|没)|"
            r"(?:是不是|对不对|好不好|可以吗|行不行|有没有问题)",
            normalized,
        )
    )


def looks_like_question(value: str) -> bool:
    """识别带问号或明确疑问句式的短文本。"""
    compact = " ".join(str(value or "").split()).strip()
    if compact.endswith(("?", "？")):
        return True
    if len(compact) > 100:
        return False
    question_cue = re.search(
        r"什么是|为什么|为何|如何|怎么|怎样|哪些|哪种|哪个|是否|能否|有何|有什么|"
        r"区别是什么|作用是什么",
        compact,
    )
    if question_cue is None:
        return False
    # “介绍为什么……/讲解如何……”是内容描述，不是资料向学习者提出的问题。
    reporting_prefix = compact[: question_cue.start()]
    return not bool(re.search(r"(?:介绍|讲解|说明|分析|讨论|解释)\s*$", reporting_prefix))


def validated_source_question(
    value: object,
    evidence_refs: tuple[Evidence, ...],
    candidates: list[dict[str, str]],
) -> str | None:
    """只接受模型逐字指向所引用 evidence 的候选，并返回候选原文。"""
    requested = compact_text(value, 180)
    if not requested:
        return None
    requested_key = normalized_sentence(requested)
    evidence_ids = {reference.evidenceId for reference in evidence_refs}
    for candidate in candidates:
        question = compact_text(candidate.get("question"), 180)
        evidence_id = candidate.get("evidenceId")
        if (
            question
            and evidence_id in evidence_ids
            and normalized_sentence(question) == requested_key
            and is_meaningful_source_question(question)
        ):
            return question
    return None


def normalize_generated_summary(value: object) -> str | None:
    """只清洗并校验 DeepSeek 摘要，不从 evidence 或本地规则补写内容。"""
    summary = compact_text(value, 500)
    if not summary:
        return None
    cleaned = compact_text(clean_content_text(summary), 500)
    if not cleaned or len(normalized_sentence(cleaned)) < 20 or is_noise_fragment(cleaned):
        return None
    if contains_review_artifact(cleaned):
        return None
    return cleaned


def is_high_quality_review_question(value: str) -> bool:
    """拒绝无上下文指代、陈述句、转场句和泛化占位题。"""
    question = " ".join(str(value or "").split()).strip()
    normalized = normalized_sentence(question)
    if not question.endswith(("?", "？")) or not 8 <= len(normalized) <= 180:
        return False
    if contains_review_artifact(question) or is_noise_fragment(question):
        return False
    if re.match(
        r"^(?:那|那么|然后|所以|这时|这时候|这个|这些|那这些|它|其|这里|那里|上述|前面|刚才|"
        r"大家|同学们|我们|面试官(?:可能)?(?:会)?(?:顺着)?问)",
        question,
    ):
        return False
    if re.search(
        r"(?:什么意思|这些是什么|那是什么|到底什么意思|本节(?:的)?(?:核心内容|关键知识点|主要内容)|"
        r"本段(?:的)?(?:核心内容|关键知识点|主要内容)|这段(?:主要)?讲了什么|需要掌握什么)",
        question,
    ):
        return False
    return bool(
        re.search(
            r"什么|为什么|为何|如何|怎么|怎样|哪些|哪一种|哪种|哪个|是否|能否|有何|有什么|"
            r"区别|作用|含义|机制|流程|条件|场景|原因|由谁|由什么|通过什么|分别",
            question,
        )
    )


def is_high_quality_review_hint(value: str) -> bool:
    """提示必须包含具体回忆方向，不能是本地占位式套话。"""
    hint = " ".join(str(value or "").split()).strip()
    normalized = normalized_sentence(hint)
    if not 6 <= len(normalized) <= 180 or contains_review_artifact(hint) or is_noise_fragment(hint):
        return False
    return not bool(
        re.fullmatch(
            r"(?:先)?回忆(?:一下)?(?:本节|本段|这段|资料|视频)?(?:的)?(?:内容|核心内容|关键知识点|主要内容)",
            hint.strip("。！？!? "),
        )
    )


def answer_is_grounded(answer: str, evidence_refs: tuple[Evidence, ...]) -> bool:
    """逐论断核验答案事实，防止正确摘录后夹带资料外结论。"""
    answer_key = normalized_sentence(answer)
    source_key = normalized_sentence(" ".join(reference.snippet for reference in evidence_refs))
    if not answer_key or not source_key or contains_review_artifact(answer):
        return False
    if answer_key in source_key:
        return True
    claims = [normalized_sentence(claim) for claim in split_answer_claims(answer)]
    return bool(claims) and all(text_is_grounded(claim, source_key) for claim in claims)


def split_answer_claims(answer: str) -> list[str]:
    """按强标点和新增事实连接词拆分答案，供逐条忠实度校验。"""
    sentences = re.split(r"[。！？!?；;\n]+", answer)
    claims: list[str] = []
    connector_boundary = re.compile(
        rf"[，,]\s*(?=(?:{ANSWER_CLAIM_CONNECTOR_PATTERN}))"
    )
    leading_connector = re.compile(
        rf"^(?:{ANSWER_CLAIM_CONNECTOR_PATTERN})[，,:：\s]*"
    )
    for sentence in sentences:
        # 模型常用“此外/并通过/它使用”等在同一句追加新事实，不能让前半句的原文重合掩盖后半句幻觉。
        for raw_claim in connector_boundary.split(sentence):
            claim = leading_connector.sub("", raw_claim.strip())
            if normalized_sentence(claim):
                claims.append(claim)
    return claims


def text_is_grounded(text_key: str, source_key: str) -> bool:
    """要求单个答案事实与引用正文存在连续片段及足够的字符 n-gram 覆盖。"""
    if text_key in source_key:
        return True
    gram_width = 3 if len(text_key) <= 12 else 4
    grams = {
        text_key[index : index + gram_width]
        for index in range(max(0, len(text_key) - gram_width + 1))
    }
    if not grams:
        return False
    coverage = sum(gram in source_key for gram in grams) / len(grams)
    return coverage >= 0.12 and longest_common_substring_length(text_key, source_key) >= min(6, len(text_key))


def longest_common_substring_length(left: str, right: str) -> int:
    """计算两个已归一化文本的最长连续公共片段长度，限制答案外部补写。"""
    previous = [0] * (len(right) + 1)
    longest = 0
    for left_character in left:
        current = [0] * (len(right) + 1)
        for index, right_character in enumerate(right, start=1):
            if left_character == right_character:
                current[index] = previous[index - 1] + 1
                longest = max(longest, current[index])
        previous = current
    return longest


def contains_review_artifact(value: str) -> bool:
    """识别不能进入摘要、问题、答案或提示的检索元数据与 OCR 广告。"""
    compact = " ".join(str(value or "").split())
    return bool(
        re.search(
            rf"父段摘要[：:]|OCR\s*出现时间|视频画面(?:聚合)?|{TIMECODE_TOKEN_PATTERN}\s*(?:-->|[-至~～—–])\s*"
            rf"{TIMECODE_TOKEN_PATTERN}|多一句没有[，,、 ]*少一句不行|高级软件人才培训专家",
            compact,
            flags=re.IGNORECASE,
        )
    )


def split_knowledge_sentences(text: str) -> list[str]:
    """沿中文句号和分号切分，过短片段与导航噪声不生成卡片。"""
    cleaned = clean_content_text(re.sub(r"^父段摘要[：:]\s*", "", " ".join(text.split())))
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", cleaned)
    result: list[str] = []
    for part in parts:
        sentence = part.strip(" -\t")
        if len(sentence) < 12 or sentence.startswith(("http://", "https://")):
            continue
        if len(sentence) > 360:
            sentence = sentence[:360].rstrip() + "..."
        result.append(sentence)
    if result:
        return result[:4]
    return [cleaned[:360].rstrip()] if len(cleaned) >= 12 else []


def clean_section_name(section: str, title: str) -> str:
    """选择适合出题的章节名。"""
    value = section.strip()
    # 视频切片或 OCR 失败时章节可能只有时间码，使用资料标题避免生成无意义问题。
    if re.fullmatch(TIMECODE_RANGE_PATTERN, value):
        value = title
    return compact_text(value if value and value != "全文" else title, 48) or "本节内容"


def is_repetitive_noise(value: str) -> bool:
    """识别字幕/OCR 中同一短语连续重复的低质量片段。"""
    compact = strip_leading_timecode(" ".join(value.split()))
    if not compact or is_subtitle_watermark(compact) or is_generic_speech_cue(compact):
        return True
    return is_repetitive_noise_core(compact)


def is_noise_fragment(value: str) -> bool:
    """统一识别不能承载知识事实的时间码、字幕水印和口头填充片段。"""
    compact = strip_leading_timecode(" ".join(str(value or "").split())).strip(" -，。；：:,.!?！？")
    if not compact:
        return True
    if is_subtitle_watermark(compact) or is_generic_speech_cue(compact):
        return True
    return is_repetitive_noise_core(compact)


def is_repetitive_noise_core(value: str) -> bool:
    """执行重复 n-gram 检测，避免与统一噪声入口互相递归。"""
    compact = " ".join(value.split())
    if re.search(r"(.{2,14})(?:\s+\1){2,}", compact):
        return True
    normalized = re.sub(r"[\s，。；：,.!?！？、]+", "", compact)
    if len(normalized) < 18:
        return False
    # 只有重复短语覆盖正文大部分时才判噪，避免误删多次出现同一技术术语的正常知识段。
    for width in range(3, min(12, len(normalized) // 3) + 1):
        counts: dict[str, int] = {}
        for start in range(len(normalized) - width + 1):
            gram = normalized[start : start + width]
            counts[gram] = counts.get(gram, 0) + 1
        if max(counts.values(), default=0) >= 3 and max(counts.values()) * width >= len(normalized) * 0.60:
            return True
    return False


def is_subtitle_watermark(value: str) -> bool:
    """识别“字幕提供/中文字幕”等片源水印重复，而非把正常字幕内容误删。"""
    compact = re.sub(r"\s+", "", value)
    if re.fullmatch(SUBTITLE_CREDIT_PATTERN, compact, flags=re.IGNORECASE):
        return True
    marker_count = sum(compact.count(marker) for marker in ("字幕提供", "中文字幕", "文字幕", "字幕由"))
    return marker_count >= 2


def is_generic_speech_cue(value: str) -> bool:
    """识别只有口头转场、没有可复习事实的片段。"""
    compact = re.sub(r"[\s，。；：:,.!?！？、]+", "", value)
    return bool(
        re.fullmatch(
            r"(?:(?:"
            r"嗯|啊|哦|好|对|那么|然后|其实|首先|第一点|也就是说|大家可以看到|那这样的方式呢|我们先看一下|"
            r"欢迎大家(?:点赞|关注|收藏|转发|投币|一键三连)+|"
            r"(?:感谢|谢谢)大家(?:的)?(?:收看|观看|支持)|"
            r"(?:请|记得)?(?:点赞|关注|收藏|转发|投币|一键三连)+|"
            r"(?:我们)?(?:下期|下次)(?:视频)?再见"
            r")(?:了|呢|啊|吧|嘛)?)+",
            compact,
        )
    )


def strip_leading_timecode(value: str) -> str:
    """去掉普通或 SRT 视频时间范围，保留其后的事实文本。"""
    return re.sub(
        rf"^{TIMECODE_RANGE_PATTERN}(?:[-，。；：:\s]*)",
        "",
        value,
    ).strip()


def clean_content_text(value: str) -> str:
    """清除检索前缀、时间码、OCR 广告和字幕水印，保留真实知识正文。"""
    raw = str(value or "")
    lines = [
        line
        for line in raw.splitlines()
        if not re.match(
            r"^\s*(?:OCR\s*出现时间|视频画面(?:聚合)?|多一句没有[，,、 ]*少一句不行|高级软件人才培训专家)",
            line,
            flags=re.IGNORECASE,
        )
    ]
    text = " ".join(" ".join(lines).split())
    text = re.sub(r"^父段摘要[：:]\s*", "", text)
    text = re.sub(rf"OCR\s*出现时间[：:]\s*{TIMECODE_TOKEN_PATTERN}", " ", text, flags=re.IGNORECASE)
    text = re.sub(
        rf"视频画面(?:聚合)?\s*{TIMECODE_TOKEN_PATTERN}(?:\s*[-至~～—–]\s*{TIMECODE_TOKEN_PATTERN})?",
        " ",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"多一句没有[，,、 ]*少一句不行[，,、 ]*用更短时间[，,、 ]*教会更实用的技术[！!]?", " ", text)
    text = text.replace("高级软件人才培训专家", " ")
    text = strip_leading_timecode(text)
    marker_count = sum(text.count(marker) for marker in ("字幕提供", "中文字幕", "文字幕", "字幕由"))
    has_credit = re.search(SUBTITLE_CREDIT_PATTERN, text, flags=re.IGNORECASE) is not None
    if marker_count >= 2 or has_credit:
        # OCR 对“中文字幕提供”经常发生单字错位，按长词优先移除后再清理孤立残字。
        text = re.sub(SUBTITLE_CREDIT_PATTERN, " ", text, flags=re.IGNORECASE)
        for marker in ("中文字幕提供", "文字幕提供", "字幕提供", "中文字幕", "字幕由"):
            text = text.replace(marker, " ")
    cleaned = re.sub(r"\s+", " ", text).strip()
    cleaned = re.sub(r"^[，。；：:,.!?！？、-]+\s*", "", cleaned)
    noise_key = re.sub(r"[\s，。；：:,.!?！？、-]+", "", cleaned)
    return "" if re.fullmatch(r"[中文字幕提供]+", noise_key) else cleaned


def normalize_answer_text(value: object, maximum_length: int) -> str | None:
    """清理模型答案前置时间码，避免把视频定位信息当成知识正文。"""
    text = compact_text(value, maximum_length)
    if not text:
        return None
    return compact_text(clean_content_text(text), maximum_length)


def stable_source_key(
    section: str,
    evidence_refs: tuple[Evidence, ...],
    answer: str,
) -> str:
    """按证据和知识内容生成身份键，避免卡片调序后错误继承学习状态。"""
    identity = {
        "section": re.sub(r"\s+", "", section).lower(),
        "evidenceIds": sorted({reference.evidenceId.strip() for reference in evidence_refs}),
        "knowledge": normalized_sentence(answer),
    }
    serialized = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:32]
    return f"knowledge-{digest}"


def compact_text(value: object, maximum_length: int) -> str | None:
    """压缩空白并限制模型或原文进入卡片的长度。"""
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    return text if len(text) <= maximum_length else text[:maximum_length].rstrip() + "..."


def normalized_sentence(value: str) -> str:
    """生成句子去重键。"""
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).lower()


def parse_json_object(content: str) -> dict[str, Any]:
    """兼容模型偶尔返回的 Markdown JSON 代码块。"""
    value = content.strip()
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```$", "", value)
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("模型提炼结果不是 JSON 对象")
    return parsed
