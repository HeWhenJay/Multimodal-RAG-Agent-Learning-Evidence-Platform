"""按用户提示从单份资料 evidence 中补充遗漏复习卡片。"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import logging
import re
import time
from typing import Any

from app.review.cockpit_retry import call_cockpit_with_retry, cockpit_retry_policy
from app.review.knowledge_extractor import (
    KnowledgePoint,
    LearningMaterialContext,
    REVIEW_RESPONSE_PARSE_ATTEMPTS,
    ReviewExtractionError,
    answer_is_grounded,
    clean_content_text,
    clean_review_evidences,
    clean_section_name,
    compact_text,
    evidence_position,
    is_high_quality_review_hint,
    is_high_quality_review_question,
    is_noise_fragment,
    normalize_answer_text,
    normalized_sentence,
    parse_json_object,
    select_representative_evidences,
    stable_source_key,
    review_llm_deepseek_fallback_endpoint,
    review_llm_primary_endpoint,
    review_llm_reasoning_effort,
    review_llm_thinking_enabled,
)
from app.review.repository import ReviewCardRecord
from app.schemas.rag import Evidence
from app.schemas.review import ReviewMissingKnowledgeConversationMessage
from prompts.review import (
    review_missing_knowledge_system_prompt,
    review_missing_knowledge_user_prompt,
)


logger = logging.getLogger(__name__)
MISSING_KNOWLEDGE_EVIDENCE_LIMIT = 48
MISSING_KNOWLEDGE_CARD_LIMIT = 8


@dataclass(frozen=True)
class MissingKnowledgeExtraction:
    """一次补漏模型调用中通过门禁的新知识点候选。"""

    knowledge_points: tuple[KnowledgePoint, ...]
    assistant_message: str | None
    skipped_count: int = 0


class MissingKnowledgeExtractor:
    """把用户提示映射到当前资料 evidence，并拒绝无来源或重复候选。"""

    def __init__(self, *, provider: str | None = None) -> None:
        self.provider = (provider or "auto").strip().lower()
        self.timeout_seconds = cockpit_retry_policy().request_timeout_seconds
        self._refresh_llm_config()

    def extract(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
        *,
        message: str,
        conversation: list[ReviewMissingKnowledgeConversationMessage],
        existing_cards: list[ReviewCardRecord],
    ) -> MissingKnowledgeExtraction:
        """调用复习模型提取补充候选，空候选按正常零新增处理。"""
        self._refresh_llm_config()
        if self.provider not in {"auto", "deepseek"}:
            raise ReviewExtractionError(f"遗漏知识点补充只允许使用 {self.model} 生成")
        if not self.api_key:
            raise ReviewExtractionError("未配置 REVIEW_LLM_API_KEY，无法查找遗漏知识点")
        cleaned = clean_review_evidences(evidences)
        if not cleaned:
            return MissingKnowledgeExtraction((), "当前资料没有可用于补漏的有效原文证据。")
        selected = select_missing_knowledge_evidences(cleaned, message, conversation)
        payload = self._generate_payload(material, selected, message, conversation, existing_cards)
        return self.validate_payload(material, selected, payload, existing_cards)

    def _refresh_llm_config(self) -> None:
        """刷新主中转与 DeepSeek 降级端点，确保补漏与主生成使用相同策略。"""
        self.timeout_seconds = cockpit_retry_policy().request_timeout_seconds
        self.primary_endpoint = review_llm_primary_endpoint()
        self.fallback_endpoint = review_llm_deepseek_fallback_endpoint(self.primary_endpoint)
        self.api_key = self.primary_endpoint.api_key
        self.model = self.primary_endpoint.model
        self.base_url = self.primary_endpoint.base_url
        self.reasoning_effort = review_llm_reasoning_effort()
        self.thinking_enabled = review_llm_thinking_enabled()
        self.active_model_name = self.primary_endpoint.display_name

    def _generate_payload(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
        message: str,
        conversation: list[ReviewMissingKnowledgeConversationMessage],
        existing_cards: list[ReviewCardRecord],
    ) -> dict[str, Any]:
        """以短程传输重试请求严格 JSON，避免空响应直接触发资料重建。"""
        from openai import OpenAI

        prompt = review_missing_knowledge_user_prompt(
            title=material.title,
            document_type=material.document_type,
            message=message,
            conversation=[item.model_dump() for item in conversation],
            evidences=[
                {
                    "evidenceId": item.evidenceId,
                    "sectionName": item.sectionName,
                    "snippet": item.snippet,
                    "position": evidence_position(item),
                }
                for item in evidences
            ],
            existing_cards=[
                {
                    "question": item.question,
                    "answer": item.answer,
                    "evidenceIds": existing_card_evidence_ids(item),
                }
                for item in existing_cards
            ],
        )
        # 补漏请求关闭 SDK 隐式重试，由统一 Cockpit 策略公开记录尝试顺序。
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            max_retries=0,
        )
        last_error: Exception | None = None
        for attempt in range(1, REVIEW_RESPONSE_PARSE_ATTEMPTS + 1):
            try:
                request: dict[str, Any] = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": review_missing_knowledge_system_prompt()},
                        {"role": "user", "content": prompt},
                    ],
                    "reasoning_effort": self.reasoning_effort,
                    "response_format": {"type": "json_object"},
                    "timeout": self.timeout_seconds,
                }
                if self.thinking_enabled:
                    request["extra_body"] = {"thinking": {"type": "enabled"}}
                response = self._create_completion(client, request)
                choices = getattr(response, "choices", None) or []
                content = choices[0].message.content if choices else ""
                return parse_json_object(content or "")
            except (json.JSONDecodeError, IndexError, AttributeError, TypeError, ValueError) as exc:
                last_error = exc
                logger.warning("%s 补漏响应解析失败，传输重试 %s/%s", self.active_model_name, attempt, REVIEW_RESPONSE_PARSE_ATTEMPTS)
                if attempt < REVIEW_RESPONSE_PARSE_ATTEMPTS:
                    time.sleep(0.25 * attempt)
        raise ReviewExtractionError(f"{self.active_model_name} 连续返回空补漏响应或非法 JSON") from last_error

    def _create_completion(self, client: Any, request: dict[str, Any]) -> Any:
        """Cockpit 可恢复错误先重试一次，仍失败时才降级到 DeepSeek。"""
        from openai import OpenAI, OpenAIError

        try:
            response = call_cockpit_with_retry(
                lambda: client.chat.completions.create(**request),
                operation=f"{self.primary_endpoint.display_name} 补漏",
                logger=logger,
            )
            self.active_model_name = self.primary_endpoint.display_name
            return response
        except OpenAIError as primary_error:
            fallback = self.fallback_endpoint
            if fallback is None:
                raise
            logger.warning(
                "%s 补漏的 Cockpit 重试已耗尽，切换至 DeepSeek：%s",
                self.primary_endpoint.display_name,
                type(primary_error).__name__,
            )
            fallback_client = OpenAI(
                api_key=fallback.api_key,
                base_url=fallback.base_url,
                timeout=self.timeout_seconds,
                max_retries=0,
            )
            fallback_request = dict(request)
            fallback_request["model"] = fallback.model
            response = fallback_client.chat.completions.create(**fallback_request)
            self.active_model_name = fallback.display_name
            return response

    def validate_payload(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
        payload: dict[str, Any],
        existing_cards: list[ReviewCardRecord],
    ) -> MissingKnowledgeExtraction:
        """逐卡执行 evidence、文本质量与既有卡片语义去重门禁。"""
        raw_cards = payload.get("cards")
        if not isinstance(raw_cards, list):
            raise ReviewExtractionError(f"{self.active_model_name} 未返回有效的遗漏知识点数组")
        evidence_by_id = {item.evidenceId: item for item in evidences}
        existing_questions = [item.question for item in existing_cards]
        accepted_questions = list(existing_questions)
        accepted: list[KnowledgePoint] = []
        skipped = max(0, len(raw_cards) - MISSING_KNOWLEDGE_CARD_LIMIT)
        for raw in raw_cards[:MISSING_KNOWLEDGE_CARD_LIMIT]:
            if not isinstance(raw, dict):
                skipped += 1
                continue
            question = compact_text(raw.get("question"), 180)
            answer = normalize_answer_text(raw.get("answer"), 600)
            hint = compact_text(raw.get("hint"), 180)
            raw_ids = raw.get("evidenceIds")
            evidence_ids = raw_ids if isinstance(raw_ids, list) else []
            if not question or not is_high_quality_review_question(question):
                skipped += 1
                continue
            if any(review_questions_duplicate(question, existing) for existing in accepted_questions):
                skipped += 1
                continue
            if not answer or is_noise_fragment(answer) or not hint or not is_high_quality_review_hint(hint):
                skipped += 1
                continue
            if not 1 <= len(evidence_ids) <= 2 or any(item not in evidence_by_id for item in evidence_ids):
                skipped += 1
                continue
            refs = tuple(evidence_by_id[item] for item in evidence_ids)[:2]
            if not answer_is_grounded(answer, refs):
                skipped += 1
                continue
            accepted_questions.append(question)
            accepted.append(
                KnowledgePoint(
                    source_key=stable_source_key(clean_section_name(refs[0].sectionName, material.title), refs, answer),
                    question=question,
                    answer=answer,
                    hint=hint,
                    evidence_refs=refs,
                )
            )
        assistant_message = compact_text(payload.get("assistantMessage"), 500)
        return MissingKnowledgeExtraction(tuple(accepted), assistant_message, skipped)


def select_missing_knowledge_evidences(
    evidences: list[Evidence],
    message: str,
    conversation: list[ReviewMissingKnowledgeConversationMessage],
) -> list[Evidence]:
    """按用户主题召回相关片段及相邻片段，弱提示时均匀覆盖整份资料。"""
    ordered = sorted(evidences, key=evidence_position)
    query = " ".join([*(item.content for item in conversation[-6:] if item.role == "USER"), message])
    terms = query_terms(query)
    scored: list[tuple[float, int]] = []
    for index, evidence in enumerate(ordered):
        corpus = normalized_sentence(f"{evidence.sectionName} {evidence.snippet}")
        score = sum(min(4, corpus.count(term)) * min(8, len(term)) for term in terms if term in corpus)
        if score > 0:
            scored.append((float(score), index))
    if not scored:
        return select_representative_evidences(ordered, limit=MISSING_KNOWLEDGE_EVIDENCE_LIMIT)
    selected_indexes: set[int] = set()
    for _score, index in sorted(scored, key=lambda item: (-item[0], item[1]))[:20]:
        selected_indexes.update(position for position in range(max(0, index - 1), min(len(ordered), index + 2)))
    selected = [ordered[index] for index in sorted(selected_indexes)][:MISSING_KNOWLEDGE_EVIDENCE_LIMIT]
    if len(selected) < min(16, len(ordered)):
        selected_ids = {item.evidenceId for item in selected}
        supplements = select_representative_evidences(
            [item for item in ordered if item.evidenceId not in selected_ids],
            limit=min(16, len(ordered)) - len(selected),
        )
        selected.extend(supplements)
    return sorted(selected[:MISSING_KNOWLEDGE_EVIDENCE_LIMIT], key=evidence_position)


def query_terms(value: str) -> list[str]:
    """生成中英文主题词和中文 n-gram，仅用于 evidence 候选召回。"""
    normalized = normalized_sentence(clean_content_text(value))
    terms = set(re.findall(r"[a-z0-9+#.]{2,}", value.lower()))
    for segment in re.findall(r"[\u4e00-\u9fff]{2,}", normalized):
        terms.add(segment)
        for width in range(2, min(6, len(segment)) + 1):
            terms.update(segment[index : index + width] for index in range(len(segment) - width + 1))
    stop = {"知识点", "遗漏", "漏掉", "视频", "文档", "资料", "还有", "讲了", "相关", "内容", "寻找", "补充"}
    return sorted((term for term in terms if term not in stop), key=len, reverse=True)[:80]


def review_questions_duplicate(left: str, right: str) -> bool:
    """结合规范化包含关系和编辑相似度识别同一复习问题。"""
    left_key = normalized_sentence(left)
    right_key = normalized_sentence(right)
    if not left_key or not right_key:
        return False
    if left_key == right_key:
        return True
    shorter, longer = sorted((left_key, right_key), key=len)
    if len(shorter) >= 10 and shorter in longer and len(shorter) / len(longer) >= 0.72:
        return True
    return SequenceMatcher(None, left_key, right_key).ratio() >= 0.86


def existing_card_evidence_ids(card: ReviewCardRecord) -> list[str]:
    """从既有卡片快照中安全提取来源 ID，供模型避免重复。"""
    try:
        raw = json.loads(card.evidence_refs_json)
    except (TypeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [str(item.get("evidenceId")) for item in raw if isinstance(item, dict) and item.get("evidenceId")]
