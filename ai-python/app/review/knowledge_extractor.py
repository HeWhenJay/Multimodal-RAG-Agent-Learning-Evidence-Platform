"""从既有 RAG evidence 提炼适合主动回忆的短知识点。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
import re
from typing import Any

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
LEARNING_KEYWORDS = (
    "八股",
    "面经",
    "面试题",
    "课程",
    "教程",
    "知识点",
    "原理",
    "机制",
    "技术讲解",
    "学习笔记",
    "复习",
    "高可用",
    "分布式",
    "算法",
    "数据库",
    "缓存",
    "消息队列",
    "源码分析",
)
NON_LEARNING_KEYWORDS = (
    "个人简历",
    "求职简历",
    "招聘职位",
    "岗位描述",
    "职位描述",
    "会议纪要",
    "工作周报",
    "工作日报",
    "聊天记录",
    "通知公告",
    "系统日志",
    "错误日志",
    "上传清单",
    "账单",
    "发票",
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


class KnowledgePointExtractor:
    """优先使用显式启用的模型，失败时执行确定性本地提炼。"""

    def __init__(
        self,
        *,
        provider: str | None = None,
    ) -> None:
        # 复习模型固定走 DeepSeek 官方入口，避免误继承通用 RAG 或代理配置。
        self.provider = (provider or os.getenv("REVIEW_EXTRACTION_PROVIDER") or "auto").strip().lower()
        self.api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
        self.model = REVIEW_LLM_MODEL
        self.reasoning_effort = REVIEW_LLM_REASONING_EFFORT
        self.timeout_seconds = float(os.getenv("REVIEW_EXTRACTION_TIMEOUT_SECONDS", "120"))
        self.base_url = REVIEW_LLM_BASE_URL

    def extract(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
    ) -> ExtractionResult:
        """只根据传入 evidence 生成卡片，绝不补写资料外事实。"""
        usable = sanitize_evidences(deduplicate_evidences(evidences))
        if not usable:
            return ExtractionResult(
                False,
                "非学习资料",
                "资料清洗后仅剩时间码、字幕水印、重复字幕或口头语等无效内容",
                (),
                f"none:{REVIEW_CARD_PROMPT_VERSION}",
                existing_material_summary(material),
            )
        if self.provider in {"auto", "deepseek"} and self.api_key:
            try:
                modeled = self._extract_with_model(material, usable)
                if modeled is not None:
                    return modeled
            except Exception:
                logger.exception("DeepSeek 提炼复习知识点失败，已降级为本地确定性提炼")
        return self._extract_locally(material, usable)

    def _extract_with_model(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
    ) -> ExtractionResult | None:
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
    ) -> ExtractionResult | None:
        """过滤虚假引用并仅采纳可追溯到原 evidence 的 sourceQuestion。"""
        raw_learning = payload.get("isLearningContent")
        if not isinstance(raw_learning, bool):
            return None
        is_learning = raw_learning
        reason = compact_text(payload.get("reason"), 240) or "模型完成学习内容判定"
        category = compact_text(payload.get("category"), 60) or ("学习资料" if is_learning else None)
        summary = resolve_material_summary(material, payload.get("summary"), evidences)
        if not is_learning:
            return ExtractionResult(
                False,
                category,
                reason,
                (),
                f"model:{REVIEW_CARD_PROMPT_VERSION}",
                summary,
            )
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
            return None
        for index, raw in enumerate(raw_cards, start=1):
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
            if is_noise_fragment(answer):
                continue
            source_question = validated_source_question(
                raw.get("sourceQuestion"),
                refs,
                question_candidates,
            )
            question = source_question or compact_text(raw.get("question"), 180)
            if not question:
                continue
            section = clean_section_name(refs[0].sectionName, material.title)
            if source_question is None:
                question = normalize_model_question(question, section, answer, index)
            question_key = normalized_sentence(question)
            if not question_key or question_key in seen_questions:
                continue
            seen_questions.add(question_key)
            points.append(
                KnowledgePoint(
                    source_key=stable_source_key(section, refs, answer),
                    question=question,
                    answer=answer,
                    hint=compact_text(raw.get("hint"), 180),
                    evidence_refs=refs,
                )
            )
        if not points:
            return None
        return ExtractionResult(
            True,
            category,
            reason,
            tuple(points[:8]),
            f"model:{REVIEW_CARD_PROMPT_VERSION}",
            summary,
        )

    def _extract_locally(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
    ) -> ExtractionResult:
        """优先沿用原始问句，再按重点陈述生成可复现的本地卡片。"""
        is_learning, category, reason = classify_learning_content(material, evidences)
        summary = resolve_material_summary(material, None, evidences)
        if not is_learning:
            return ExtractionResult(
                False,
                category,
                reason,
                (),
                f"local:{REVIEW_CARD_PROMPT_VERSION}",
                summary,
            )

        candidates: list[tuple[Evidence, str | None, str]] = []
        seen_answers: set[str] = set()
        # 视频讲解和面经中的原始提问优先于自动改写的泛化问题。
        for evidence in evidences:
            for question in extract_source_questions(evidence):
                answer = answer_for_source_question(evidence.snippet, question)
                answer_key = normalized_sentence(answer or "")
                if not answer or not answer_key or answer_key in seen_answers:
                    continue
                seen_answers.add(answer_key)
                candidates.append((evidence, question, answer))
                if len(candidates) >= 8:
                    break
            if len(candidates) >= 8:
                break

        # 只要资料存在可回答的原始问句，本地降级就不再用陈述句凑泛化问题。
        if not candidates:
            for evidence in evidences:
                for sentence in split_knowledge_sentences(evidence.snippet):
                    if is_noise_fragment(sentence) or looks_like_question(sentence):
                        continue
                    sentence_key = normalized_sentence(sentence)
                    if not sentence_key or sentence_key in seen_answers:
                        continue
                    seen_answers.add(sentence_key)
                    candidates.append((evidence, None, sentence))
                    if len(candidates) >= 8:
                        break
                if len(candidates) >= 8:
                    break
        if not candidates:
            return ExtractionResult(
                True,
                category,
                "已识别为学习资料，但未提炼出有效知识点",
                (),
                f"local:{REVIEW_CARD_PROMPT_VERSION}",
                summary,
            )

        points: list[KnowledgePoint] = []
        section_counts: dict[str, int] = {}
        for evidence, source_question, sentence in candidates:
            section = clean_section_name(evidence.sectionName, material.title)
            section_counts[section] = section_counts.get(section, 0) + 1
            ordinal = section_counts[section]
            answer = compact_text(sentence, 360) or compact_text(evidence.snippet, 360)
            if not answer:
                continue
            points.append(
                KnowledgePoint(
                    source_key=stable_source_key(section, (evidence,), answer),
                    question=source_question or build_question(section, answer, ordinal),
                    answer=answer,
                    hint=build_hint(section, answer),
                    evidence_refs=(evidence,),
                )
            )
        return ExtractionResult(
            True,
            category,
            reason,
            tuple(points[:8]),
            f"local:{REVIEW_CARD_PROMPT_VERSION}",
            summary,
        )


def classify_learning_content(
    material: LearningMaterialContext,
    evidences: list[Evidence],
) -> tuple[bool, str | None, str]:
    """使用可解释关键词和文本结构判断是否值得进入复习队列。"""
    corpus = " ".join(
        [material.title, material.summary or "", *(item.sectionName for item in evidences), *(item.snippet for item in evidences)]
    ).lower()
    negative_hits = [keyword for keyword in NON_LEARNING_KEYWORDS if keyword in corpus]
    positive_hits = [keyword for keyword in LEARNING_KEYWORDS if keyword in corpus]
    question_like = len(re.findall(r"[？?]|为什么|如何|是什么|区别|作用|流程", corpus))
    section_names = {item.sectionName for item in evidences if item.sectionName not in {"", "全文"}}
    educational_cues = (
        "定义", "原理", "机制", "流程", "步骤", "方法", "示例",
        "总结", "重点", "概念", "架构", "实现",
    )
    educational_sections = sum(
        any(cue in section_name for cue in educational_cues)
        for section_name in section_names
    )
    knowledge_statements = len(
        re.findall(r"是指|用于|通过|包括|分为|原因|区别|优点|缺点|步骤|机制|原理", corpus)
    )
    if negative_hits and not positive_hits:
        return False, "非学习资料", f"命中非学习资料特征：{negative_hits[0]}"
    if positive_hits or question_like >= 2 or (educational_sections >= 2 and knowledge_statements >= 2):
        category = infer_category(corpus, positive_hits)
        signal = "、".join(positive_hits[:3]) or "结构化知识章节与知识陈述"
        return True, category, f"命中学习内容特征：{signal}"
    return False, "待确认", "未发现足够明确的学习内容特征"


def infer_category(corpus: str, positive_hits: list[str]) -> str:
    """给学习资料分配面向用户的简洁类别。"""
    if any(word in corpus for word in ("面经", "面试题", "八股")):
        return "面试复习"
    if any(word in corpus for word in ("课程", "教程", "视频", "讲解")):
        return "课程复习"
    if any(word in corpus for word in ("原理", "机制", "算法", "高可用", "分布式")):
        return "技术原理"
    return "学习笔记" if positive_hits else "学习资料"


def deduplicate_evidences(evidences: list[Evidence]) -> list[Evidence]:
    """优先保留父段摘要，再按章节和正文去重。"""
    ordered = sorted(
        evidences,
        key=lambda item: 0 if item.metadata.get("childKind") == "summary" else 1,
    )
    result: list[Evidence] = []
    seen: set[tuple[str, str]] = set()
    for item in ordered:
        snippet = compact_text(item.snippet, 600)
        if not snippet:
            continue
        key = (item.sectionName.strip(), normalized_sentence(snippet))
        if key in seen:
            continue
        seen.add(key)
        result.append(item.model_copy(update={"snippet": snippet}))
    return result[:16]


def sanitize_evidences(evidences: list[Evidence]) -> list[Evidence]:
    """在进入模型或本地提炼前移除时间码、水印和纯口头噪声。"""
    result: list[Evidence] = []
    for item in evidences:
        snippet = clean_content_text(item.snippet)
        if is_noise_fragment(snippet):
            continue
        cleaned = compact_text(snippet, 600)
        if cleaned:
            result.append(item.model_copy(update={"snippet": cleaned}))
    return result[:16]


def extract_source_question_candidates(evidences: list[Evidence]) -> list[dict[str, str]]:
    """提取带 evidence 归属的原始问句，供模型选择和服务端校验。"""
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for evidence in evidences:
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


def answer_for_source_question(snippet: str, question: str) -> str | None:
    """优先取原问句之后的事实陈述作为本地降级答案。"""
    cleaned = clean_content_text(snippet)
    question_index = cleaned.find(question)
    search_spaces = [
        cleaned[question_index + len(question) :].lstrip("，,：:。；; ")
    ] if question_index >= 0 else []
    search_spaces.append(cleaned)
    for text in search_spaces:
        answers: list[str] = []
        for sentence in split_knowledge_sentences(text):
            if looks_like_question(sentence) or is_noise_fragment(sentence):
                continue
            if normalized_sentence(sentence) == normalized_sentence(question):
                continue
            answers.append(sentence)
            if len(answers) >= 2:
                break
        answer = compact_text("".join(answers), 360)
        if answer:
            return answer
    return None


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


def existing_material_summary(material: LearningMaterialContext) -> str | None:
    """资料已有摘要时原样返回，模型与本地降级均不得改写。"""
    if isinstance(material.summary, str) and material.summary.strip():
        return material.summary
    return None


def resolve_material_summary(
    material: LearningMaterialContext,
    generated_summary: object,
    evidences: list[Evidence],
) -> str | None:
    """已有摘要优先；仅在缺失时采纳同次模型结果或本地摘要。"""
    existing = existing_material_summary(material)
    if existing is not None:
        return existing
    generated = compact_text(generated_summary, 500) if isinstance(generated_summary, str) else None
    if generated:
        cleaned = compact_text(clean_content_text(generated), 500)
        if cleaned and not is_noise_fragment(cleaned):
            return cleaned
    return build_local_material_summary(evidences)


def build_local_material_summary(evidences: list[Evidence]) -> str | None:
    """模型不可用时从多条 evidence 的事实陈述拼出保守摘要。"""
    statements: list[str] = []
    seen: set[str] = set()
    for evidence in evidences:
        for sentence in split_knowledge_sentences(evidence.snippet):
            key = normalized_sentence(sentence)
            if not key or key in seen or looks_like_question(sentence) or is_noise_fragment(sentence):
                continue
            seen.add(key)
            statements.append(sentence)
            if len(statements) >= 4:
                break
        if len(statements) >= 4:
            break
    return compact_text("".join(statements), 500)


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


def build_question(section: str, answer: str, ordinal: int) -> str:
    """把陈述句转换为主动回忆提示，避免提示用户重新阅读原资料。"""
    if is_noise_fragment(answer):
        return f"{section}的核心内容是什么？"
    colon = re.match(r"^([^：:]{2,28})[：:]\s*(.+)$", answer)
    if colon:
        return f"{colon.group(1).strip()}的核心含义是什么？"
    cue = re.sub(r"[，。；：,.!?！？].*$", "", answer).strip()
    if 4 <= len(cue) <= 24 and cue != section and not is_generic_speech_cue(cue):
        return f"关于“{cue}”，需要掌握什么？"
    suffix = "" if ordinal == 1 else f"（要点 {ordinal}）"
    return f"{section}的关键知识点是什么{suffix}？"


def normalize_model_question(question: str, section: str, answer: str, ordinal: int) -> str:
    """修正模型偶尔生成的时间码问题或字幕噪声问题。"""
    if re.match(TIMECODE_RANGE_PATTERN, question) or is_noise_fragment(question) or is_generic_speech_cue(question):
        return build_question(section, answer, ordinal)
    return question


def build_hint(section: str, answer: str) -> str:
    """生成不直接泄露完整答案的短提示。"""
    first_phrase = re.split(r"[，。；：,.!?！？]", answer, maxsplit=1)[0].strip()
    if first_phrase and first_phrase != section:
        return compact_text(f"先回忆 {section} 与“{first_phrase}”的关系", 100) or f"先回忆 {section}"
    return f"先回忆 {section} 的核心概念和作用"


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
    """清除时间码及重复字幕水印，混合片段中的真实知识正文继续保留。"""
    text = strip_leading_timecode(" ".join(str(value or "").split()))
    marker_count = sum(text.count(marker) for marker in ("字幕提供", "中文字幕", "文字幕", "字幕由"))
    has_credit = re.search(SUBTITLE_CREDIT_PATTERN, text, flags=re.IGNORECASE) is not None
    if marker_count < 2 and not has_credit:
        return text
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
