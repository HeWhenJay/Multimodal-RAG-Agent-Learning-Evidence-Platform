"""按照用户想法生成一张或多张复习卡片的无副作用改写预览。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import re
import time
from typing import Any

from app.core.io_concurrency import configured_io_workers, process_io_limiter, run_llm_io
from app.review.cockpit_retry import call_cockpit_with_retry, cockpit_retry_policy
from app.review.knowledge_extractor import (
    LearningMaterialContext,
    REVIEW_RESPONSE_PARSE_ATTEMPTS,
    ReviewExtractionError,
    answer_is_grounded,
    compact_text,
    normalize_markdown_text,
    parse_json_object,
    review_llm_deepseek_fallback_endpoint,
    review_llm_primary_endpoint,
    review_llm_reasoning_effort,
    review_llm_thinking_enabled,
)
from app.review.missing_knowledge import select_missing_knowledge_evidences
from app.review.repository import ReviewCardRecord
from app.schemas.rag import Evidence
from prompts.review import (
    review_card_rewrite_system_prompt,
    review_card_rewrite_user_prompt,
    review_material_rewrite_system_prompt,
    review_material_rewrite_user_prompt,
)


logger = logging.getLogger(__name__)
REWRITE_EVIDENCE_LIMIT = 32
REWRITE_MODES = {"STRICT_SOURCE", "SOURCE_FIRST", "SOURCE_REFERENCE"}


@dataclass(frozen=True)
class CardRewriteCandidate:
    """一份尚未写入数据库的单卡片改写候选。"""

    question: str
    answer: str
    hint: str | None
    evidence_refs: tuple[Evidence, ...]
    model_name: str


@dataclass(frozen=True)
class MaterialRewriteCardCandidate:
    """资料级改写中的一张尚未写入数据库的候选卡片。"""

    question: str
    answer: str
    hint: str | None
    evidence_refs: tuple[Evidence, ...]


@dataclass(frozen=True)
class MaterialRewriteCandidate:
    """一份尚未写入数据库的资料级候选卡片集合。"""

    summary: str | None
    cards: tuple[MaterialRewriteCardCandidate, ...]
    merge_note: str | None
    model_name: str


class CardRewriter:
    """复用复习模型端点生成三档来源约束的卡片候选。"""

    def __init__(self, *, provider: str | None = None) -> None:
        self.provider = (provider or "auto").strip().lower()
        self.timeout_seconds = cockpit_retry_policy().request_timeout_seconds
        self._refresh_llm_config()

    def rewrite(
        self,
        material: LearningMaterialContext,
        card: ReviewCardRecord,
        evidences: list[Evidence],
        *,
        instruction: str,
        mode: str,
    ) -> CardRewriteCandidate:
        """生成改写预览；严格档位会在返回前执行 evidence 忠实度校验。"""
        self._refresh_llm_config()
        if mode not in REWRITE_MODES:
            raise ReviewExtractionError("不支持的卡片改写档位")
        if self.provider not in {"auto", "deepseek"}:
            raise ReviewExtractionError(f"卡片改写只允许使用 {self.model} 生成")
        if not self.api_key:
            raise ReviewExtractionError("未配置 REVIEW_LLM_API_KEY，无法改写复习卡片")
        selected = select_rewrite_evidences(card, evidences, instruction)
        if mode == "STRICT_SOURCE" and not selected:
            raise ReviewExtractionError("当前资料没有可用于严格改写的原文证据")
        payload = self._generate_payload(material, card, selected, instruction, mode)
        return self.validate_payload(card, selected, payload, mode)

    def rewrite_material(
        self,
        material: LearningMaterialContext,
        cards: list[ReviewCardRecord],
        evidences: list[Evidence],
        *,
        instruction: str,
        mode: str,
        target_card_count: int | None = None,
        base_cards: list[dict[str, Any]] | None = None,
    ) -> MaterialRewriteCandidate:
        """按目标数量生成资料级候选，整个过程不写数据库。"""
        self._refresh_llm_config()
        if mode not in REWRITE_MODES:
            raise ReviewExtractionError("不支持的资料改写档位")
        if self.provider not in {"auto", "deepseek"}:
            raise ReviewExtractionError(f"资料改写只允许使用 {self.model} 生成")
        if not self.api_key:
            raise ReviewExtractionError("未配置 REVIEW_LLM_API_KEY，无法改写复习资料")
        selected = select_material_rewrite_evidences(cards, evidences, instruction)
        if mode == "STRICT_SOURCE" and not selected:
            raise ReviewExtractionError("当前资料没有可用于严格改写的原文证据")
        resolved_count = infer_material_rewrite_card_count(instruction, target_card_count)
        payload = self._generate_material_payload(
            material,
            cards,
            selected,
            instruction,
            mode,
            target_card_count=resolved_count,
            base_cards=base_cards,
        )
        try:
            return self.validate_material_payload(cards, selected, payload, mode, target_card_count=resolved_count)
        except ReviewExtractionError as initial_error:
            # 模型偶尔会把数组压扁成一个对象；用更强的修复提示再请求一次，避免把用户明确要求的新增卡片静默丢失。
            if resolved_count <= 1:
                raise
            repair_instruction = (
                f"{instruction}\n系统强制要求：最终必须返回恰好 {resolved_count} 张彼此独立的 cards，"
                "不得把新增主题合并回第一张卡片。"
            )
            try:
                repaired = self._generate_material_payload(
                    material,
                    cards,
                    selected,
                    repair_instruction,
                    mode,
                    target_card_count=resolved_count,
                    base_cards=base_cards,
                )
                return self.validate_material_payload(
                    cards,
                    selected,
                    repaired,
                    mode,
                    target_card_count=resolved_count,
                )
            except ReviewExtractionError:
                # 若模型仍返回单卡对象，退化为逐卡生成：第一张保留原主题，后续卡片专门承载新增主题。
                # 这样不会把用户明确要求的第二张卡片静默丢弃，也不会在服务层编造答案。
                generated_cards: list[MaterialRewriteCardCandidate] = []
                summary: str | None = None
                model_name = self.active_model_name
                for index in range(resolved_count):
                    role_instruction = (
                        f"{instruction}\n这是第 {index + 1}/{resolved_count} 张独立卡片。"
                        + (
                            "请保留现有资料的主要生成内容，作为基础复习卡片。"
                            if index == 0
                            else "请只承载用户要求新增的独立主题，尤其是 Inbox/Outbox 时说明其职责、事务边界、可靠投递和重试；不要与第一张合并。"
                        )
                    )
                    single_payload = self._generate_material_payload(
                        material,
                        cards,
                        selected,
                        role_instruction,
                        mode,
                        target_card_count=1,
                        base_cards=base_cards,
                    )
                    single = self.validate_material_payload(
                        cards,
                        selected,
                        single_payload,
                        mode,
                        target_card_count=1,
                    )
                    generated_cards.extend(single.cards)
                    summary = summary or single.summary
                    model_name = single.model_name
                if len(generated_cards) != resolved_count:
                    raise initial_error
                return MaterialRewriteCandidate(
                    summary=summary,
                    cards=tuple(generated_cards),
                    merge_note=f"已按用户要求拆分为 {resolved_count} 张独立卡片",
                    model_name=model_name,
                )

    def _refresh_llm_config(self) -> None:
        """刷新主中转与 DeepSeek 降级配置，支持服务启动后补充环境变量。"""
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
        card: ReviewCardRecord,
        evidences: list[Evidence],
        instruction: str,
        mode: str,
    ) -> dict[str, Any]:
        """请求严格 JSON；解析失败时执行短程传输重试。"""
        from openai import OpenAI

        prompt = review_card_rewrite_user_prompt(
            mode=mode,
            instruction=instruction,
            material_title=material.title,
            document_type=material.document_type,
            original_card={
                "question": card.question,
                "answer": card.answer,
                "hint": card.hint,
            },
            evidences=[
                {
                    "evidenceId": item.evidenceId,
                    "sectionName": item.sectionName,
                    "snippet": item.snippet,
                }
                for item in evidences
            ],
        )
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
                        {"role": "system", "content": review_card_rewrite_system_prompt(mode)},
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
                logger.warning(
                    "%s 卡片改写响应解析失败，传输重试 %s/%s",
                    self.active_model_name,
                    attempt,
                    REVIEW_RESPONSE_PARSE_ATTEMPTS,
                )
                if attempt < REVIEW_RESPONSE_PARSE_ATTEMPTS:
                    time.sleep(0.25 * attempt)
        raise ReviewExtractionError(f"{self.active_model_name} 连续返回空改写响应或非法 JSON") from last_error

    def _generate_material_payload(
        self,
        material: LearningMaterialContext,
        cards: list[ReviewCardRecord],
        evidences: list[Evidence],
        instruction: str,
        mode: str,
        *,
        target_card_count: int,
        base_cards: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """请求指定数量的资料级严格 JSON，解析失败时沿用短程传输重试。"""
        from openai import OpenAI

        prompt = review_material_rewrite_user_prompt(
            mode=mode,
            instruction=instruction,
            material_title=material.title,
            document_type=material.document_type,
            summary=material.summary,
            target_card_count=target_card_count,
            base_cards=base_cards or [],
            cards=[
                {"cardId": card.id, "question": card.question, "answer": card.answer, "hint": card.hint}
                for card in cards
            ],
            evidences=[
                {"evidenceId": item.evidenceId, "sectionName": item.sectionName, "snippet": item.snippet}
                for item in evidences
            ],
        )
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
                        {
                            "role": "system",
                            "content": review_material_rewrite_system_prompt(mode, target_card_count),
                        },
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
                logger.warning(
                    "%s 资料改写响应解析失败，传输重试 %s/%s",
                    self.active_model_name,
                    attempt,
                    REVIEW_RESPONSE_PARSE_ATTEMPTS,
                )
                if attempt < REVIEW_RESPONSE_PARSE_ATTEMPTS:
                    time.sleep(0.25 * attempt)
        raise ReviewExtractionError(f"{self.active_model_name} 连续返回空资料改写响应或非法 JSON") from last_error

    def _create_completion(self, client: Any, request: dict[str, Any]) -> Any:
        """Cockpit 可恢复错误先重试一次，仍失败时才降级到 DeepSeek。"""
        from openai import OpenAI, OpenAIError

        try:
            def request_primary() -> Any:
                with process_io_limiter.slot(
                    "review.llm",
                    configured_io_workers("REVIEW_DEEPSEEK_MAX_IN_FLIGHT"),
                ):
                    return run_llm_io(lambda: client.chat.completions.create(**request))

            response = call_cockpit_with_retry(
                request_primary,
                operation=f"{self.primary_endpoint.display_name} 卡片改写",
                logger=logger,
            )
            self.active_model_name = self.primary_endpoint.display_name
            return response
        except OpenAIError as primary_error:
            fallback = self.fallback_endpoint
            if fallback is None:
                raise
            logger.warning(
                "%s 卡片改写的 Cockpit 重试已耗尽，切换至 DeepSeek：%s",
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
            with process_io_limiter.slot(
                "review.llm",
                configured_io_workers("REVIEW_DEEPSEEK_MAX_IN_FLIGHT"),
            ):
                response = run_llm_io(lambda: fallback_client.chat.completions.create(**fallback_request))
            self.active_model_name = fallback.display_name
            return response

    def validate_payload(
        self,
        card: ReviewCardRecord,
        evidences: list[Evidence],
        payload: dict[str, Any],
        mode: str,
    ) -> CardRewriteCandidate:
        """校验卡片正文长度、真实 evidenceId 与严格档位忠实度。"""
        question = compact_text(payload.get("question"), 500)
        answer = normalize_markdown_text(payload.get("answer"), 5000)
        hint = normalize_markdown_text(payload.get("hint"), 1000)
        if not question or not answer:
            raise ReviewExtractionError(f"{self.active_model_name} 未返回完整的卡片问题和答案")
        evidence_by_id = {item.evidenceId: item for item in evidences}
        raw_ids = payload.get("evidenceIds")
        requested_ids = raw_ids if isinstance(raw_ids, list) else []
        evidence_ids = list(
            dict.fromkeys(str(item) for item in requested_ids if str(item) in evidence_by_id)
        )[:4]
        if not evidence_ids:
            # 即使“原文仅参考”也保留原卡片已有 evidence，避免用户改写后丢失可追溯来源；手动卡片本来没有引用时仍允许为空。
            evidence_ids = [
                item
                for item in existing_card_evidence_ids(card)
                if item in evidence_by_id
            ][:4]
        refs = tuple(evidence_by_id[item] for item in evidence_ids)
        if mode == "STRICT_SOURCE":
            if not refs:
                raise ReviewExtractionError("严格依赖原文的改写结果必须引用真实 evidence")
            if not answer_is_grounded(answer, refs):
                raise ReviewExtractionError("严格依赖原文的改写结果未通过 evidence 忠实度校验")
        return CardRewriteCandidate(question, answer, hint, refs, self.active_model_name)

    def validate_material_payload(
        self,
        cards: list[ReviewCardRecord],
        evidences: list[Evidence],
        payload: dict[str, Any],
        mode: str,
        *,
        target_card_count: int = 1,
    ) -> MaterialRewriteCandidate:
        """校验资料级候选数量、正文和真实 evidence。"""
        summary = normalize_markdown_text(payload.get("summary"), 5000)
        merge_note = compact_text(payload.get("mergeNote"), 500)
        raw_cards = payload.get("cards")
        # 兼容旧模型在目标数量为 1 时仍返回 question/answer 顶层对象。
        if not isinstance(raw_cards, list) and target_card_count == 1:
            raw_cards = [payload]
        if not isinstance(raw_cards, list) or len(raw_cards) != target_card_count:
            raise ReviewExtractionError(
                f"{self.active_model_name} 未按要求返回恰好 {target_card_count} 张资料改写卡片"
            )
        evidence_by_id = {item.evidenceId: item for item in evidences}
        candidates: list[MaterialRewriteCardCandidate] = []
        for raw_card in raw_cards:
            if not isinstance(raw_card, dict):
                raise ReviewExtractionError(f"{self.active_model_name} 返回了无效的资料改写卡片")
            question = compact_text(raw_card.get("question"), 500)
            answer = normalize_markdown_text(raw_card.get("answer"), 5000)
            hint = normalize_markdown_text(raw_card.get("hint"), 1000)
            if not question or not answer:
                raise ReviewExtractionError(f"{self.active_model_name} 未返回完整的资料改写卡片")
            raw_ids = raw_card.get("evidenceIds")
            requested_ids = raw_ids if isinstance(raw_ids, list) else []
            evidence_ids = list(
                dict.fromkeys(str(item) for item in requested_ids if str(item) in evidence_by_id)
            )[:4]
            if not evidence_ids:
                for card in cards:
                    for evidence_id in existing_card_evidence_ids(card):
                        if evidence_id in evidence_by_id and evidence_id not in evidence_ids:
                            evidence_ids.append(evidence_id)
                        if len(evidence_ids) >= 4:
                            break
                    if len(evidence_ids) >= 4:
                        break
            refs = tuple(evidence_by_id[item] for item in evidence_ids)
            if mode == "STRICT_SOURCE":
                if not refs:
                    raise ReviewExtractionError("严格依赖原文的资料改写结果必须引用真实 evidence")
                if not answer_is_grounded(answer, refs):
                    raise ReviewExtractionError("严格依赖原文的资料改写结果未通过 evidence 忠实度校验")
            candidates.append(MaterialRewriteCardCandidate(question, answer, hint, refs))
        return MaterialRewriteCandidate(summary, tuple(candidates), merge_note, self.active_model_name)


def infer_material_rewrite_card_count(
    instruction: str,
    explicit_count: int | None = None,
    *,
    base_card_count: int = 1,
) -> int:
    """从显式参数或中文自然语言推断资料级候选卡片总数。"""
    if explicit_count is not None:
        return max(1, int(explicit_count))
    text = compact_text(instruction, 2000) or ""
    numerals = {"一": 1, "两": 2, "俩": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}
    total_match = re.search(
        r"(?:总共\s*(?:应该\s*)?(?:返回|生成|输出)?|(?:返回|输出|应该返回|必须返回))\s*"
        r"(\d+|[一两俩二三四五六七八])\s*(?:张|个)?\s*卡片",
        text,
    )
    if total_match:
        token = total_match.group(1)
        return max(1, int(token) if token.isdigit() else numerals[token])
    add_match = re.search(r"(?:新增|新添|再加|补充|添加)\s*(\d+|[一两俩二三四五六七八])\s*(?:张|个)?\s*卡片", text)
    if add_match:
        token = add_match.group(1)
        added = int(token) if token.isdigit() else numerals[token]
        return max(1, max(1, base_card_count) + added)
    return 1


def select_rewrite_evidences(
    card: ReviewCardRecord,
    evidences: list[Evidence],
    instruction: str,
) -> list[Evidence]:
    """优先保留原卡片引用，再按问题和用户想法补充相关原文。"""
    by_id = {item.evidenceId: item for item in evidences}
    selected: list[Evidence] = [
        by_id[evidence_id]
        for evidence_id in existing_card_evidence_ids(card)
        if evidence_id in by_id
    ]
    related = select_missing_knowledge_evidences(
        evidences,
        f"{card.question} {instruction}",
        [],
    )
    seen = {item.evidenceId for item in selected}
    for item in related:
        if item.evidenceId in seen:
            continue
        selected.append(item)
        seen.add(item.evidenceId)
        if len(selected) >= REWRITE_EVIDENCE_LIMIT:
            break
    return selected[:REWRITE_EVIDENCE_LIMIT]


def select_material_rewrite_evidences(
    cards: list[ReviewCardRecord],
    evidences: list[Evidence],
    instruction: str,
) -> list[Evidence]:
    """合并所有卡片已有引用，再补充与用户说明相关的资料 evidence。"""
    by_id = {item.evidenceId: item for item in evidences}
    selected: list[Evidence] = []
    seen: set[str] = set()
    for card in cards:
        for evidence_id in existing_card_evidence_ids(card):
            if evidence_id in by_id and evidence_id not in seen:
                selected.append(by_id[evidence_id])
                seen.add(evidence_id)
    related = select_missing_knowledge_evidences(evidences, f"{instruction} {' '.join(card.question for card in cards)}", [],)
    for item in related:
        if item.evidenceId in seen:
            continue
        selected.append(item)
        seen.add(item.evidenceId)
        if len(selected) >= 64:
            break
    return selected[:64]


def existing_card_evidence_ids(card: ReviewCardRecord) -> list[str]:
    """从卡片持久化 evidence JSON 中读取稳定引用 ID。"""
    try:
        raw = json.loads(card.evidence_refs_json)
    except (TypeError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    return [
        str(item.get("evidenceId"))
        for item in raw
        if isinstance(item, dict) and str(item.get("evidenceId") or "").strip()
    ]
