"""从既有 RAG evidence 提炼适合主动回忆的短知识点。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import logging
from math import ceil
import os
import re
import time
from typing import Any

from app.core.environment import read_process_or_windows_user_environment
from app.core.io_concurrency import (
    LlmIoTimeoutError,
    configured_cpu_workers,
    configured_io_workers,
    process_io_limiter,
    run_llm_io,
)
from app.review.cockpit_retry import call_cockpit_with_retry, cockpit_retry_policy
from app.review.execution_budget import (
    ReviewExecutionBudget,
    ReviewExecutionTimeout,
    configured_segment_cockpit_request_retries,
    configured_segment_generation_attempts,
    configured_segment_merge_rounds,
)
from app.review.generation_graph import (
    ProgressCallback,
    ReviewManualReviewRequired,
    emit_progress,
    run_review_generation_graph,
    unique_feedback,
)
from app.schemas.rag import Evidence
from prompts.review import (
    REVIEW_CARD_PROMPT_VERSION,
    review_card_system_prompt,
    review_card_user_prompt,
    review_merge_repair_system_prompt,
    review_merge_repair_user_prompt,
    review_multi_card_observer_system_prompt,
    review_multi_card_observer_user_prompt,
)


logger = logging.getLogger(__name__)
REVIEW_LLM_MODEL = "gpt-5.6-terra"
REVIEW_LLM_REASONING_EFFORT = "max"
REVIEW_LLM_BASE_URL = "http://localhost:58966/v1"
REVIEW_LLM_THINKING_ENABLED = True
REVIEW_LLM_FALLBACK_MODEL = "deepseek-v4-flash"
REVIEW_LLM_FALLBACK_BASE_URL = "https://api.deepseek.com"
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
# 兼容旧版调度计划的参考值。它只用于进度展示和旧调用方，不再截断实际卡片输出。
STANDARD_REVIEW_CARD_LIMIT = 8
STANDARD_EVIDENCE_LIMIT = 16
REVIEW_RESPONSE_PARSE_ATTEMPTS = 3
SPEECH_QUESTION_MARKER_PATTERN = re.compile(
    r"(?:嗯|啊|呃|哎|就是|可能|然后|还有|这时候|他会问|面试官|"
    r"第[一二三四五六七八九十百千万0-9]+个)"
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
    generation_attempts: int = 0
    quality_feedback: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReviewLlmEndpoint:
    """一条复习模型调用端点及其面向用户的模型标识。"""

    api_key: str
    model: str
    base_url: str
    display_name: str


class ReviewExtractionError(RuntimeError):
    """复习模型内容未能生成或未通过质量门禁。"""

    def __init__(self, message: str, *, diagnostics: list[str] | tuple[str, ...] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = tuple(diagnostics or [message])


class KnowledgePointExtractor:
    """只使用复习模型生成内容，本地代码仅清洗和拒绝坏结果。"""

    def __init__(
        self,
        *,
        provider: str | None = None,
        langextract_enabled: bool | None = None,
        langextract_curator: Any | None = None,
    ) -> None:
        self.provider = (provider or os.getenv("REVIEW_EXTRACTION_PROVIDER") or "auto").strip().lower()
        self.timeout_seconds = cockpit_retry_policy().request_timeout_seconds
        self._langextract_enabled_override = langextract_enabled
        self._langextract_curator = langextract_curator
        self._refresh_llm_config()

    def extract(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
        *,
        user_feedback: str | None = None,
        generation_mode: str = "STANDARD",
        progress_callback: ProgressCallback | None = None,
        execution_budget: ReviewExecutionBudget | None = None,
    ) -> ExtractionResult:
        """只根据 evidence 运行多轮复习模型生成图，失败时不发布降级内容。"""
        # 提取器通常随 FastAPI 一起初始化；本地开发时用户可能在服务启动后才补充环境变量。
        self._refresh_llm_config()
        cleaned = clean_review_evidences(deduplicate_evidences(evidences))
        if not cleaned:
            return ExtractionResult(
                False,
                "非学习资料",
                "资料清洗后仅剩时间码、字幕水印、重复字幕或口头语等无效内容",
                (),
                f"filter:{REVIEW_CARD_PROMPT_VERSION}",
                None,
            )
        is_learning, category, reason = classify_learning_content(material, cleaned)
        if not is_learning:
            return ExtractionResult(
                False, category, reason, (), f"filter:{REVIEW_CARD_PROMPT_VERSION}", None
            )
        if self.provider not in {"auto", "deepseek"}:
            raise ReviewExtractionError(f"复习内容只允许使用 {self.model} 生成")
        if not self.api_key:
            raise ReviewExtractionError("未配置 REVIEW_LLM_API_KEY，无法生成复习内容")
        mode = normalize_generation_mode(generation_mode)
        if mode == "SEGMENTED":
            return self._extract_segmented(
                material,
                cleaned,
                category=category,
                reason=reason,
                user_feedback=user_feedback,
                progress_callback=progress_callback,
                execution_budget=execution_budget,
            )
        try:
            if execution_budget is not None:
                execution_budget.ensure_active("整理 evidence")
            # 原始问题全部参与完整性检查，不设置候选数量截断。
            source_questions = extract_source_question_candidates(cleaned)
            required_questions = required_structured_source_questions(source_questions)
            langextract_enabled = self._langextract_is_enabled()
            emit_progress(
                progress_callback,
                stageCode="review.evidence",
                stageLabel="整理证据",
                message="已完成 evidence 清洗，正在提取原始问题并准备模型上下文",
                status="RUNNING",
                currentStep=1,
                totalSteps=5 if langextract_enabled else 4,
                percent=4 if langextract_enabled else 12,
                attempt=0,
                maxAttempts=None,
                detail=(
                    f"清洗后保留 {len(cleaned)} 条 evidence，识别 {len(source_questions)} 个候选问题，"
                    f"其中 {len(required_questions)} 个属于必须完整覆盖的问题清单；"
                    f"LangExtract {'已启用' if langextract_enabled else '未启用'}"
                ),
            )
            outcome = run_review_generation_graph(
                curator=(
                    lambda: self._curate_material(
                        material,
                        cleaned,
                        execution_budget=execution_budget,
                    )
                ) if langextract_enabled else None,
                actor=lambda attempt, feedback, previous_candidate, curator_context: self._generate_model_payload(
                    material,
                    cleaned,
                    source_questions=source_questions,
                    curator_context=curator_context,
                    attempt=attempt,
                    quality_feedback=feedback,
                    user_feedback=user_feedback,
                    generation_mode=mode,
                    previous_candidate=previous_candidate,
                    progress_callback=progress_callback,
                    execution_budget=execution_budget,
                ),
                observer=lambda payload, curator_context: self._validate_model_result(
                    material,
                    cleaned,
                    payload,
                    source_questions=source_questions,
                    curator_context=curator_context,
                    generation_mode=mode,
                ),
                multi_card_observer=lambda payload, curator_context, merge_round: self._observe_multi_card_candidate(
                    material,
                    payload,
                    curator_context=curator_context,
                    merge_round=merge_round,
                    progress_callback=progress_callback,
                    execution_budget=execution_budget,
                ),
                merge_repair=lambda payload, merge_plan, curator_context, merge_round: self._merge_card_groups(
                    material,
                    payload,
                    merge_plan=merge_plan,
                    curator_context=curator_context,
                    merge_round=merge_round,
                    progress_callback=progress_callback,
                    execution_budget=execution_budget,
                ),
                plan={
                    "materialId": material.material_id,
                    "title": material.title,
                    "sourceQuestionCount": len(source_questions),
                    "structuredQuestionCount": len(required_questions),
                    # 仅保留结构化问题计数，卡片输出不设置 maxCards 业务上限。
                    "maxCards": None,
                    "hasUserFeedback": bool((user_feedback or "").strip()),
                    "generationMode": mode,
                    "langExtractEnabled": langextract_enabled,
                    "llmModel": self.model,
                },
                max_attempts=(
                    configured_segment_generation_attempts()
                    if execution_budget is not None
                    else None
                ),
                max_merge_rounds=(
                    configured_segment_merge_rounds()
                    if execution_budget is not None
                    else None
                ),
                on_progress=progress_callback,
            )
            modeled = outcome.result
            return replace(
                modeled,
                is_learning_content=True,
                category=category,
                reason=reason,
                generation_attempts=outcome.attempts,
                quality_feedback=tuple(
                    unique_feedback([*modeled.quality_feedback, *outcome.quality_feedback])
                ),
            )
        except ReviewManualReviewRequired:
            raise
        except ReviewExtractionError:
            raise
        except (ReviewExecutionTimeout, LlmIoTimeoutError) as exc:
            emit_progress(
                progress_callback,
                stageCode="review.timeout",
                stageLabel="模型调用已停止",
                message="本段模型调用已达到当前执行预算，正在收敛为可重试失败",
                status="FAILED",
                currentStep=1,
                totalSteps=1,
                percent=100,
                attempt=0,
                maxAttempts=None,
                detail=(
                    execution_budget.diagnostics()
                    if execution_budget is not None
                    else str(exc)
                ),
            )
            raise ReviewExtractionError(
                "本段模型调用超过执行预算，请稍后单独重试",
                diagnostics=[str(exc)],
            ) from exc
        except json.JSONDecodeError as exc:
            logger.warning("%s 响应不是合法 JSON", self.active_model_name)
            raise ReviewExtractionError(f"{self.active_model_name} 返回的复习内容格式无效，请重新生成") from exc
        except Exception as exc:
            logger.exception("%s 生成复习内容失败", self.active_model_name)
            raise ReviewExtractionError(f"{self.active_model_name} 生成复习内容失败，请稍后重新生成") from exc

    def _langextract_is_enabled(self) -> bool:
        """每次生成时读取线上开关，显式构造参数优先用于测试和 A/B 隔离。"""
        if self._langextract_enabled_override is not None:
            return self._langextract_enabled_override
        return read_bool_environment("REVIEW_LANGEXTRACT_ENABLED", True)

    def _extract_segmented(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
        *,
        category: str | None,
        reason: str,
        user_feedback: str | None,
        progress_callback: ProgressCallback | None,
        execution_budget: ReviewExecutionBudget | None,
    ) -> ExtractionResult:
        """按连续 evidence 分段生成，成功段经确定性 reducer 去重合并。"""
        segments = split_review_evidence_segments(evidences)
        emit_progress(
            progress_callback,
            stageCode="review.segmented",
            stageLabel="分段生成",
            message=f"资料已拆分为 {len(segments)} 段，将逐段生成并合并全部合格卡片",
            status="RUNNING",
            currentStep=1,
            totalSteps=max(1, len(segments) + 1),
            percent=5,
            attempt=0,
            maxAttempts=None,
        )
        merged_points: list[KnowledgePoint] = []
        summaries: list[str] = []
        diagnostics: list[str] = []
        attempts = 0
        for index, segment in enumerate(segments, start=1):
            def segment_progress(event: dict[str, Any], *, segment_index: int = index) -> None:
                """在原节点进度前增加分段位置，便于页面识别当前处理范围。"""
                if progress_callback is None:
                    return
                enriched = dict(event)
                enriched["message"] = f"分段 {segment_index}/{len(segments)}：{event.get('message') or '正在生成'}"
                detail = str(event.get("detail") or "").strip()
                enriched["detail"] = f"segment={segment_index}/{len(segments)}" + (f"；{detail}" if detail else "")
                progress_callback(enriched)

            segment_feedback = "；".join(
                item
                for item in (
                    (user_feedback or "").strip(),
                    f"当前只处理资料第 {index}/{len(segments)} 段，完整保留本段独立知识点，不设置卡片数量上限",
                )
                if item
            )
            try:
                result = self.extract(
                    material,
                    segment,
                    user_feedback=segment_feedback,
                    generation_mode="RELAXED",
                    progress_callback=segment_progress,
                    execution_budget=execution_budget,
                )
            except ReviewManualReviewRequired as exc:
                attempts += exc.attempts
                diagnostics.extend(f"分段 {index}/{len(segments)}：{item}" for item in exc.quality_feedback)
                continue
            except ReviewExtractionError as exc:
                diagnostics.extend(f"分段 {index}/{len(segments)}：{item}" for item in exc.diagnostics)
                continue
            attempts += result.generation_attempts
            diagnostics.extend(f"分段 {index}/{len(segments)}：{item}" for item in result.quality_feedback)
            if result.summary:
                summaries.append(result.summary)
            if result.is_learning_content:
                merged_points.extend(result.knowledge_points)
        unique_points = deduplicate_segmented_points(merged_points)
        if not unique_points:
            raise ReviewManualReviewRequired(
                "分段生成后仍没有可发布的复习卡片",
                attempts=attempts,
                quality_feedback=unique_feedback(diagnostics) or ["所有分段都未通过 evidence 质量门禁"],
            )
        emit_progress(
            progress_callback,
            stageCode="review.segmented.merge",
            stageLabel="合并分段结果",
            message=f"已合并 {len(segments)} 段结果，保留 {len(unique_points)} 张去重后的合格卡片",
            status="COMPLETED",
            currentStep=max(1, len(segments) + 1),
            totalSteps=max(1, len(segments) + 1),
            percent=94,
            attempt=attempts,
            maxAttempts=None,
        )
        return ExtractionResult(
            True,
            category,
            reason,
            tuple(unique_points),
            f"model:{REVIEW_CARD_PROMPT_VERSION}:segmented",
            merge_segment_summaries(summaries),
            generation_attempts=attempts,
            quality_feedback=tuple(unique_feedback(diagnostics)),
        )

    def _refresh_llm_config(self) -> None:
        """刷新复习模型连接配置，支持启动后注入本机中转密钥。"""
        self.timeout_seconds = cockpit_retry_policy().request_timeout_seconds
        self.primary_endpoint = review_llm_primary_endpoint()
        self.fallback_endpoint = review_llm_deepseek_fallback_endpoint(self.primary_endpoint)
        self.api_key = self.primary_endpoint.api_key
        self.model = self.primary_endpoint.model
        self.reasoning_effort = review_llm_reasoning_effort()
        self.base_url = self.primary_endpoint.base_url
        self.thinking_enabled = review_llm_thinking_enabled()
        self.active_model_name = self.primary_endpoint.display_name

    def _curate_material(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
        *,
        execution_budget: ReviewExecutionBudget | None = None,
    ) -> dict[str, Any]:
        """运行一次官方 LangExtract，并转换为生成图的严格 evidence 候选上下文。"""
        from app.review.langextract_curator import (
            LangExtractKnowledgeCurator,
            build_production_curator_context,
        )

        curator = self._langextract_curator or LangExtractKnowledgeCurator(
            extraction_passes=bounded_int_environment(
                "REVIEW_LANGEXTRACT_EXTRACTION_PASSES", 2, minimum=1, maximum=5
            ),
            max_char_buffer=bounded_int_environment(
                "REVIEW_LANGEXTRACT_MAX_CHAR_BUFFER", 8000, minimum=1000, maximum=20000
            ),
            max_workers=configured_cpu_workers("REVIEW_LANGEXTRACT_MAX_WORKERS"),
            max_model_requests=bounded_int_environment(
                "REVIEW_LANGEXTRACT_MAX_MODEL_REQUESTS", 32, minimum=1, maximum=64
            ),
            timeout_seconds=float(os.getenv("REVIEW_LANGEXTRACT_TIMEOUT_SECONDS", str(self.timeout_seconds))),
        )
        if execution_budget is None:
            result = curator.extract(material.title, evidences)
        else:
            execution_budget.ensure_active("LangExtract 知识发现")
            result = curator.extract(
                material.title,
                evidences,
                execution_budget=execution_budget,
            )
        context = build_production_curator_context(result)
        context["llmModel"] = getattr(curator, "active_model_name", self.model)
        return context

    def _extract_with_model(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
        *,
        source_questions: list[dict[str, str]] | None = None,
    ) -> ExtractionResult:
        """兼容单轮测试入口；正式生成由独立 LangGraph 负责循环。"""
        payload = self._generate_model_payload(
            material,
            evidences,
            source_questions=source_questions,
        )
        return self._validate_model_result(
            material,
            evidences,
            payload,
            source_questions=source_questions,
        )

    def _generate_model_payload(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
        *,
        source_questions: list[dict[str, str]] | None = None,
        curator_context: dict[str, Any] | None = None,
        attempt: int = 1,
        quality_feedback: list[str] | None = None,
        user_feedback: str | None = None,
        generation_mode: str = "STANDARD",
        previous_candidate: dict[str, Any] | None = None,
        progress_callback: ProgressCallback | None = None,
        execution_budget: ReviewExecutionBudget | None = None,
    ) -> dict[str, Any]:
        """调用复习模型；空响应或非法 JSON 在当前质量轮内短程重试。"""
        source_questions = source_questions or extract_source_question_candidates(evidences)
        curated_knowledge_units = valid_curated_knowledge_units(curator_context)
        prioritized_evidence_ids = {
            str(evidence_id)
            for unit in curated_knowledge_units
            for evidence_id in unit.get("evidenceIds") or []
        }
        usable_evidences = select_review_prompt_evidences(
            evidences,
            source_questions,
            prioritized_evidence_ids=prioritized_evidence_ids,
            # 提炼卡片数量不设上限；有结构化问题或 Curator 候选时完整保留其 evidence。
            limit=None,
        )
        evidence_payload = [
            {
                "evidenceId": item.evidenceId,
                "sectionName": item.sectionName,
                "snippet": item.snippet,
            }
            for item in usable_evidences
        ]
        prompt = review_card_user_prompt(
            title=material.title,
            document_type=material.document_type,
            summary=material.summary or "",
            evidences=evidence_payload,
            source_questions=source_questions,
            required_source_questions=required_structured_source_questions(source_questions),
            curated_knowledge_units=curated_knowledge_units,
            max_cards=None,
            generation_mode=generation_mode,
            attempt=attempt,
            quality_feedback=quality_feedback,
            user_feedback=user_feedback,
            previous_candidate=previous_candidate,
        )
        return self._request_json_completion(
            system_prompt=review_card_system_prompt(),
            user_prompt=prompt,
            attempt=attempt,
            progress_callback=progress_callback,
            execution_budget=execution_budget,
        )

    def _observe_multi_card_candidate(
        self,
        material: LearningMaterialContext,
        candidate: dict[str, Any],
        *,
        curator_context: dict[str, Any],
        merge_round: int,
        progress_callback: ProgressCallback | None,
        execution_budget: ReviewExecutionBudget | None = None,
    ) -> dict[str, Any]:
        """请求模型只输出多卡片合并计划，不允许在 Observer 内改卡。"""
        prompt = review_multi_card_observer_user_prompt(
            candidate={"materialTitle": material.title, **candidate},
            curated_knowledge_units=valid_curated_knowledge_units(curator_context),
            merge_round=merge_round,
        )
        return self._request_json_completion(
            system_prompt=review_multi_card_observer_system_prompt(),
            user_prompt=prompt,
            attempt=max(1, merge_round + 1),
            progress_callback=progress_callback,
            progress_stage_code="review.multi_card_observer",
            progress_current_step=5 if curator_context else 4,
            progress_total_steps=7 if curator_context else 6,
            progress_percent=90,
            execution_budget=execution_budget,
        )

    def _merge_card_groups(
        self,
        material: LearningMaterialContext,
        candidate: dict[str, Any],
        *,
        merge_plan: dict[str, Any],
        curator_context: dict[str, Any],
        merge_round: int,
        progress_callback: ProgressCallback | None,
        execution_budget: ReviewExecutionBudget | None = None,
    ) -> dict[str, Any]:
        """请求模型只返回点名组的替换卡，未点名卡由生成图原样重建。"""
        prompt = review_merge_repair_user_prompt(
            candidate={"materialTitle": material.title, **candidate},
            merge_plan=merge_plan,
            curated_knowledge_units=valid_curated_knowledge_units(curator_context),
            merge_round=merge_round,
        )
        return self._request_json_completion(
            system_prompt=review_merge_repair_system_prompt(),
            user_prompt=prompt,
            attempt=max(1, merge_round),
            progress_callback=progress_callback,
            progress_stage_code="review.merge_repair",
            progress_current_step=6 if curator_context else 5,
            progress_total_steps=7 if curator_context else 6,
            progress_percent=90,
            execution_budget=execution_budget,
        )

    def _request_json_completion(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        attempt: int,
        progress_callback: ProgressCallback | None,
        progress_stage_code: str = "review.actor",
        progress_current_step: int = 2,
        progress_total_steps: int = 4,
        progress_percent: int = 26,
        execution_budget: ReviewExecutionBudget | None = None,
    ) -> dict[str, Any]:
        """统一执行复习图各模型节点的 JSON 请求、解析重试和 Cockpit 降级。"""
        from openai import OpenAI

        # 关闭 SDK 隐式重试，由统一 Cockpit 策略记录每次尝试并在耗尽后决定是否降级。
        client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            max_retries=0,
        )
        last_error: Exception | None = None
        for transport_attempt in range(1, REVIEW_RESPONSE_PARSE_ATTEMPTS + 1):
            try:
                request_timeout = (
                    execution_budget.timeout_for_request(progress_stage_code)
                    if execution_budget is not None
                    else self.timeout_seconds
                )
                request: dict[str, Any] = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "reasoning_effort": self.reasoning_effort,
                    "response_format": {"type": "json_object"},
                    "timeout": request_timeout,
                }
                if self.thinking_enabled:
                    request["extra_body"] = {"thinking": {"type": "enabled"}}
                emit_progress(
                    progress_callback,
                    stageCode=progress_stage_code,
                    stageLabel=f"{self.primary_endpoint.display_name} 请求",
                    message=(
                        f"正在请求 {self.primary_endpoint.display_name}，"
                        f"当前为质量轮次 {attempt}、传输尝试 {transport_attempt}"
                    ),
                    status="RUNNING",
                    currentStep=progress_current_step,
                    totalSteps=progress_total_steps,
                    percent=progress_percent,
                    attempt=attempt,
                    maxAttempts=None,
                    detail=(
                        f"模型={self.primary_endpoint.display_name}；单请求超时={int(request_timeout)} 秒；"
                        + (
                            execution_budget.diagnostics()
                            if execution_budget is not None
                            else f"默认模型超时={int(self.timeout_seconds)} 秒"
                        )
                    ),
                )
                response = self._create_completion(
                    client,
                    request,
                    attempt=attempt,
                    progress_callback=progress_callback,
                    progress_stage_code=progress_stage_code,
                    progress_current_step=progress_current_step,
                    progress_total_steps=progress_total_steps,
                    progress_percent=progress_percent,
                    execution_budget=execution_budget,
                )
                choices = getattr(response, "choices", None) or []
                content = choices[0].message.content if choices else ""
                return parse_json_object(content or "")
            except (json.JSONDecodeError, IndexError, AttributeError, TypeError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "%s 第 %s 轮的响应解析失败，传输重试 %s/%s：%s",
                    self.active_model_name,
                    attempt,
                    transport_attempt,
                    REVIEW_RESPONSE_PARSE_ATTEMPTS,
                    type(exc).__name__,
                )
                if transport_attempt < REVIEW_RESPONSE_PARSE_ATTEMPTS:
                    time.sleep(0.25 * transport_attempt)
        raise ReviewExtractionError(
            f"{self.active_model_name} 连续返回空响应或非法 JSON",
            diagnostics=[
                f"模型响应连续 {REVIEW_RESPONSE_PARSE_ATTEMPTS} 次为空或不是合法 JSON，下一轮将重新请求完整 JSON 对象"
            ],
        ) from last_error

    def _create_completion(
        self,
        client: Any,
        request: dict[str, Any],
        *,
        attempt: int,
        progress_callback: ProgressCallback | None,
        progress_stage_code: str = "review.actor",
        progress_current_step: int = 2,
        progress_total_steps: int = 4,
        progress_percent: int = 26,
        execution_budget: ReviewExecutionBudget | None = None,
    ) -> Any:
        """先按 Cockpit 长等待策略重试，尝试耗尽后才降级 DeepSeek。"""
        from openai import OpenAI, OpenAIError

        try:
            def request_primary() -> Any:
                request_payload = dict(request)
                slot_timeout = None
                io_timeout = None
                if execution_budget is not None:
                    request_payload["timeout"] = execution_budget.timeout_for_request(
                        progress_stage_code
                    )
                    slot_timeout = execution_budget.remaining_seconds()
                    io_timeout = min(
                        execution_budget.remaining_seconds(),
                        float(request_payload["timeout"]) + 2.0,
                    )
                with process_io_limiter.slot(
                    "review.llm",
                    configured_io_workers("REVIEW_DEEPSEEK_MAX_IN_FLIGHT"),
                    timeout_seconds=slot_timeout,
                ):
                    return run_llm_io(
                        lambda: client.chat.completions.create(**request_payload),
                        timeout_seconds=io_timeout,
                    )

            def report_cockpit_retry(
                error: Exception,
                next_attempt: int,
                max_attempts: int,
                delay: float,
            ) -> None:
                emit_progress(
                    progress_callback,
                    stageCode=progress_stage_code,
                    stageLabel="Cockpit 重试",
                    message=(
                        f"{self.primary_endpoint.display_name} 的 Cockpit 请求未成功，"
                        f"正在等待 Cockpit 切换账号或上游后重试 {next_attempt}/{max_attempts}"
                    ),
                    status="RUNNING",
                    currentStep=progress_current_step,
                    totalSteps=progress_total_steps,
                    percent=progress_percent,
                    attempt=attempt,
                    maxAttempts=None,
                    detail=(
                        f"模型={self.primary_endpoint.display_name}；{type(error).__name__}；"
                        f"退避 {delay:.1f} 秒；"
                        + (
                            execution_budget.diagnostics()
                            if execution_budget is not None
                            else "使用默认请求预算"
                        )
                    ),
                )

            response = call_cockpit_with_retry(
                request_primary,
                operation=self.primary_endpoint.display_name,
                logger=logger,
                on_retry=report_cockpit_retry,
                policy=(
                    replace(
                        cockpit_retry_policy(),
                        request_retries=configured_segment_cockpit_request_retries(),
                    )
                    if execution_budget is not None
                    else None
                ),
            )
            self.active_model_name = self.primary_endpoint.display_name
            return response
        except OpenAIError as primary_error:
            fallback = self.fallback_endpoint
            if fallback is None:
                raise
            logger.warning(
                "%s Cockpit 重试已耗尽，切换至 DeepSeek：%s",
                self.primary_endpoint.display_name,
                type(primary_error).__name__,
            )
            emit_progress(
                progress_callback,
                stageCode=progress_stage_code,
                stageLabel="DeepSeek 降级",
                message=(
                    f"{self.primary_endpoint.display_name} 的 Cockpit 尝试已耗尽，正在切换至 "
                    f"DeepSeek（{fallback.model}）"
                ),
                status="RUNNING",
                currentStep=progress_current_step,
                totalSteps=progress_total_steps,
                percent=max(progress_percent, 28),
                attempt=attempt,
                maxAttempts=None,
                detail=(
                    f"模型={fallback.display_name}；主中转失败={type(primary_error).__name__}；"
                    + (
                        execution_budget.diagnostics()
                        if execution_budget is not None
                        else "使用默认请求预算"
                    )
                ),
            )
            fallback_client = OpenAI(
                api_key=fallback.api_key,
                base_url=fallback.base_url,
                timeout=self.timeout_seconds,
                max_retries=0,
            )
            fallback_request = dict(request)
            fallback_request["model"] = fallback.model
            slot_timeout = None
            io_timeout = None
            if execution_budget is not None:
                fallback_request["timeout"] = execution_budget.timeout_for_request(
                    progress_stage_code
                )
                slot_timeout = execution_budget.remaining_seconds()
                io_timeout = min(
                    execution_budget.remaining_seconds(),
                    float(fallback_request["timeout"]) + 2.0,
                )
            with process_io_limiter.slot(
                "review.llm",
                configured_io_workers("REVIEW_DEEPSEEK_MAX_IN_FLIGHT"),
                timeout_seconds=slot_timeout,
            ):
                response = run_llm_io(
                    lambda: fallback_client.chat.completions.create(**fallback_request),
                    timeout_seconds=io_timeout,
                )
            self.active_model_name = fallback.display_name
            return response

    def _validate_model_result(
        self,
        material: LearningMaterialContext,
        evidences: list[Evidence],
        payload: dict[str, Any],
        *,
        source_questions: list[dict[str, str]] | None = None,
        curator_context: dict[str, Any] | None = None,
        generation_mode: str = "STANDARD",
    ) -> ExtractionResult:
        """逐卡收集可修复诊断，只发布全部通过门禁且结构覆盖完整的结果。"""
        summary = normalize_generated_summary(payload.get("summary"))
        if summary is None:
            raise ReviewExtractionError(
                f"{self.active_model_name} 未生成有效的资料总结",
                diagnostics=["资料总结为空、过短、包含检索产物或仍是噪声，请重新生成 2-5 句资料级总结"],
            )
        evidence_by_id = {item.evidenceId: item for item in evidences}
        question_candidates = (
            extract_source_question_candidates(evidences)
            if source_questions is None
            else source_questions
        )
        points: list[KnowledgePoint] = []
        seen_questions: set[str] = set()
        covered_source_questions: set[str] = set()
        curated_units = valid_curated_knowledge_units(curator_context)
        curated_by_id = {
            str(unit["knowledgeUnitId"]): unit
            for unit in curated_units
        }
        covered_curated_units: set[str] = set()
        diagnostics: list[str] = []
        raw_cards = payload.get("cards")
        if not isinstance(raw_cards, list):
            raise ReviewExtractionError(
                f"{self.active_model_name} 未返回有效的复习卡片数组",
                diagnostics=["cards 必须是 JSON 数组，不能缺失、为 null 或使用其他结构"],
            )
        for card_index, raw in enumerate(raw_cards, start=1):
            label = f"卡片 {card_index}"
            if not isinstance(raw, dict):
                diagnostics.append(f"{label} 不是 JSON 对象")
                continue
            answer = normalize_answer_text(raw.get("answer"), 1200)
            if not answer:
                diagnostics.append(f"{label} 的 answer 为空或不是有效文本")
                continue
            raw_evidence_ids = raw.get("evidenceIds")
            evidence_ids = list(
                dict.fromkeys(
                    str(item)
                    for item in (raw_evidence_ids if isinstance(raw_evidence_ids, list) else [])
                    if str(item).strip()
                )
            )
            if not evidence_ids:
                diagnostics.append(f"{label} 必须按逐论断支撑引用至少 1 个 evidenceId")
                continue
            unknown_ids = [str(evidence_id) for evidence_id in evidence_ids if evidence_id not in evidence_by_id]
            if unknown_ids:
                diagnostics.append(f"{label} 引用了不存在的 evidenceId：{'、'.join(unknown_ids[:4])}")
                continue
            refs = tuple(
                evidence_by_id[evidence_id]
                for evidence_id in evidence_ids
                if evidence_id in evidence_by_id
            )
            if not refs:
                diagnostics.append(f"{label} 没有可用 evidence 引用")
                continue
            knowledge_unit_ids: list[str] = []
            if curated_by_id:
                raw_knowledge_unit_ids = raw.get("knowledgeUnitIds")
                if raw_knowledge_unit_ids is not None and not isinstance(raw_knowledge_unit_ids, list):
                    diagnostics.append(f"{label} 的 knowledgeUnitIds 必须是数组")
                    continue
                knowledge_unit_ids = list(
                    dict.fromkeys(str(item) for item in (raw_knowledge_unit_ids or []) if str(item).strip())
                )
                unknown_unit_ids = [item for item in knowledge_unit_ids if item not in curated_by_id]
                if unknown_unit_ids:
                    diagnostics.append(
                        f"{label} 引用了不存在的 LangExtract 候选：{'、'.join(unknown_unit_ids[:6])}"
                    )
                    continue
                referenced_evidence_ids = {item.evidenceId for item in refs}
                mismatched_unit_ids = [
                    unit_id
                    for unit_id in knowledge_unit_ids
                    if not referenced_evidence_ids.intersection(
                        str(item) for item in curated_by_id[unit_id].get("evidenceIds") or []
                    )
                ]
                if mismatched_unit_ids:
                    diagnostics.append(
                        f"{label} 的 LangExtract 候选与 evidenceIds 不一致：{'、'.join(mismatched_unit_ids[:6])}"
                    )
                    continue
            if is_noise_fragment(answer) or not answer_is_grounded(
                answer,
                refs,
                relaxed=normalize_generation_mode(generation_mode) == "RELAXED",
            ):
                diagnostics.append(f"{label} 的 answer 含噪声或未通过逐论断 evidence 忠实度校验")
                continue
            question = compact_text(raw.get("question"), 180)
            hint = compact_text(raw.get("hint"), 180)
            if not question or not is_high_quality_review_question(question):
                diagnostics.append(f"{label} 的 question 不是主题明确、自包含的疑问句或主动回忆指令")
                continue
            source_questions_for_card = resolve_card_source_questions(
                raw,
                refs,
                question_candidates,
                review_question=question,
            )
            raw_source_question = compact_text(raw.get("sourceQuestion"), 180)
            if raw_source_question and all(
                canonical_source_question_key(item) != canonical_source_question_key(raw_source_question)
                for item in source_questions_for_card
            ):
                logger.info(
                    "复习卡片忽略了无法按资料问题候选核对的 sourceQuestion：material_id=%s card=%s",
                    material.material_id,
                    card_index,
                )
            if not hint or not is_high_quality_review_hint(hint):
                diagnostics.append(f"{label} 的 hint 为空、过于泛化或直接包含无效占位内容")
                continue
            if question_reveals_answer_structure(question):
                diagnostics.append(f"{label} 的 question 包含答案子项或多个面试意图，应把回忆方向移入 hint")
                continue
            section = clean_section_name(refs[0].sectionName, material.title)
            question_key = normalized_sentence(question)
            if not question_key or question_key in seen_questions:
                diagnostics.append(f"{label} 与其他卡片问题重复或无法形成稳定问题标识")
                continue
            seen_questions.add(question_key)
            covered_source_questions.update(
                canonical_source_question_key(item) for item in source_questions_for_card
            )
            covered_curated_units.update(knowledge_unit_ids)
            points.append(
                KnowledgePoint(
                    source_key=stable_source_key(section, refs, answer),
                    question=question,
                    answer=answer,
                    hint=hint,
                    evidence_refs=refs,
                )
            )

        expected_structured = structured_source_questions(question_candidates)
        missing_structured = [
            question for key, question in expected_structured
            if key not in covered_source_questions
        ]
        covered_structured_count = len(expected_structured) - len(missing_structured)
        minimum_structured_count = minimum_structured_question_coverage(
            question_candidates,
            len(expected_structured),
            generation_mode=generation_mode,
        )
        if missing_structured:
            if minimum_structured_count < len(expected_structured) and covered_structured_count >= minimum_structured_count:
                diagnostics.append(
                    "结构化原始问题未完全覆盖，但已按口语资料门槛通过："
                    f"规范化后应覆盖 {len(expected_structured)} 个，已覆盖 {covered_structured_count} 个，"
                    f"至少需要 {minimum_structured_count} 个；缺少：{'；'.join(missing_structured[:12])}"
                )
            elif minimum_structured_count < len(expected_structured):
                diagnostics.append(
                    "结构化原始问题覆盖不足（口语资料门槛仍未达到）："
                    f"规范化后应覆盖 {len(expected_structured)} 个，已覆盖 {covered_structured_count} 个，"
                    f"至少需要 {minimum_structured_count} 个；缺少：{'；'.join(missing_structured[:12])}"
                )
            else:
                diagnostics.append(
                    "结构化原始问题覆盖不足："
                    f"应覆盖 {len(expected_structured)} 个，已覆盖 {covered_structured_count} 个；"
                    f"缺少：{'；'.join(missing_structured[:12])}"
                )
        missing_curated_ids = [unit_id for unit_id in curated_by_id if unit_id not in covered_curated_units]
        minimum_curated_count = minimum_curated_knowledge_coverage(
            len(curated_by_id), generation_mode=generation_mode
        )
        if missing_curated_ids:
            missing_curated = [
                f"{unit_id} {compact_text(curated_by_id[unit_id].get('topic'), 40) or compact_text(curated_by_id[unit_id].get('text'), 60) or ''}"
                for unit_id in missing_curated_ids[:16]
            ]
            covered_curated_count = len(curated_by_id) - len(missing_curated_ids)
            threshold_label = "宽松门禁" if normalize_generation_mode(generation_mode) == "RELAXED" else "标准门禁"
            diagnostics.append(
                f"LangExtract 候选知识覆盖不足（{threshold_label}最低线 {minimum_curated_count}/{len(curated_by_id)}）："
                f"应覆盖 {len(curated_by_id)} 个，已覆盖 {covered_curated_count} 个；"
                f"缺少：{'；'.join(missing_curated)}"
            )
        if not points:
            if not diagnostics:
                diagnostics.append("cards 为空，没有生成任何可发布的复习卡片")
            raise ReviewExtractionError(
            f"{self.active_model_name} 生成的卡片未通过问题完整性与 evidence 质量门禁",
                diagnostics=diagnostics[:80],
            )
        if missing_structured and covered_structured_count < minimum_structured_count:
            raise ReviewExtractionError(
            f"{self.active_model_name} 生成的卡片未完整覆盖资料已有的问题清单",
                diagnostics=diagnostics[:80],
            )
        if len(curated_by_id) - len(missing_curated_ids) < minimum_curated_count:
            raise ReviewExtractionError(
            f"{self.active_model_name} 生成的卡片未完整覆盖 LangExtract 候选知识单元",
                diagnostics=diagnostics[:80],
            )
        if diagnostics:
            logger.info(
                "复习卡片采用部分成功结果：material_id=%s accepted=%s discarded=%s",
                material.material_id,
                len(points),
                len(diagnostics),
            )
        return ExtractionResult(
            True,
            None,
            f"{self.active_model_name} 已生成复习内容",
            # 通过逐卡 evidence 门禁的结果全部发布，不再按历史 8/32 张规则截断。
            tuple(points),
            f"model:{REVIEW_CARD_PROMPT_VERSION}",
            summary,
            quality_feedback=tuple(diagnostics[:80]),
        )


def valid_curated_knowledge_units(context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """只保留包含稳定 ID、原文和 evidence 映射的 LangExtract 线上候选。"""
    if not isinstance(context, dict) or context.get("status") != "COMPLETED":
        return []
    raw_units = context.get("knowledgeUnits")
    if not isinstance(raw_units, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_units:
        if not isinstance(raw, dict):
            continue
        unit_id = compact_text(raw.get("knowledgeUnitId"), 40)
        text = compact_text(raw.get("text"), 1200)
        raw_evidence_ids = raw.get("evidenceIds")
        evidence_ids = list(
            dict.fromkeys(
                str(item)
                for item in (raw_evidence_ids if isinstance(raw_evidence_ids, list) else [])
                if str(item).strip()
            )
        )
        if not unit_id or unit_id in seen or not text or not evidence_ids:
            continue
        seen.add(unit_id)
        result.append(
            {
                "knowledgeUnitId": unit_id,
                "text": text,
                "topic": compact_text(raw.get("topic"), 120),
                "knowledgeType": compact_text(raw.get("knowledgeType"), 80),
                "evidenceIds": evidence_ids,
            }
        )
    return result


def normalize_generation_mode(value: str | None) -> str:
    """规范资料生成门禁档位，未知值安全回退标准模式。"""
    normalized = str(value or "STANDARD").strip().upper()
    return normalized if normalized in {"STANDARD", "RELAXED", "SEGMENTED"} else "STANDARD"


def minimum_curated_knowledge_coverage(
    expected_count: int,
    *,
    generation_mode: str = "STANDARD",
) -> int:
    """计算 LangExtract 候选最低覆盖数，不影响最终卡片数量。"""
    if expected_count <= 0:
        return 0
    mode = normalize_generation_mode(generation_mode)
    ratio = 0.40 if mode == "RELAXED" else 0.60
    return min(expected_count, max(1, ceil(expected_count * ratio)))


def split_review_evidence_segments(
    evidences: list[Evidence],
    *,
    max_evidence_per_segment: int = 24,
    max_characters_per_segment: int = 12000,
) -> list[list[Evidence]]:
    """按 evidence 原始顺序切成有界上下文段，段数随资料增长而增长。"""
    ordered = sorted(evidences, key=evidence_position)
    if not ordered:
        return []
    segments: list[list[Evidence]] = []
    current: list[Evidence] = []
    current_chars = 0
    for item in ordered:
        item_chars = len(item.snippet or "")
        reaches_count = len(current) >= max(1, max_evidence_per_segment)
        reaches_chars = current and current_chars + item_chars > max(1000, max_characters_per_segment)
        if current and (reaches_count or reaches_chars):
            segments.append(current)
            current = []
            current_chars = 0
        current.append(item)
        current_chars += item_chars
    if current:
        segments.append(current)
    return segments


def deduplicate_segmented_points(points: list[KnowledgePoint]) -> list[KnowledgePoint]:
    """跨分段按稳定问题键和来源键去重，保留首次通过门禁的完整卡片。"""
    result: list[KnowledgePoint] = []
    seen: set[str] = set()
    for point in points:
        key = normalized_sentence(point.question) or point.source_key
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(point)
    return result


def merge_segment_summaries(summaries: list[str]) -> str | None:
    """合并各段模型摘要并去重，不在本地编造资料事实。"""
    unique: list[str] = []
    seen: set[str] = set()
    for summary in summaries:
        normalized = normalize_generated_summary(summary)
        key = normalized_sentence(normalized or "")
        if normalized and key and key not in seen:
            seen.add(key)
            unique.append(normalized)
    if not unique:
        return None
    return compact_text(" ".join(unique), 500)


def read_bool_environment(name: str, default: bool) -> bool:
    """读取兼容常见真假写法的布尔环境变量。"""
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def review_llm_api_key() -> str:
    """读取本机复习中转密钥，不借用 DeepSeek 降级密钥。"""
    return read_process_or_windows_user_environment("REVIEW_LLM_API_KEY")


def review_llm_model() -> str:
    """读取复习模型名称，默认使用本机中转提供的模型。"""
    return (os.getenv("REVIEW_LLM_MODEL") or REVIEW_LLM_MODEL).strip() or REVIEW_LLM_MODEL


def review_llm_reasoning_effort() -> str:
    """读取复习模型思考强度，默认固定为最高档。"""
    return (
        (os.getenv("REVIEW_LLM_REASONING_EFFORT") or REVIEW_LLM_REASONING_EFFORT).strip()
        or REVIEW_LLM_REASONING_EFFORT
    )


def review_llm_base_url() -> str:
    """读取复习模型 OpenAI-compatible 中转地址。"""
    return (os.getenv("REVIEW_LLM_BASE_URL") or REVIEW_LLM_BASE_URL).strip().rstrip("/")


def review_llm_thinking_enabled() -> bool:
    """读取是否发送供应商兼容的 thinking 开关。"""
    return read_bool_environment("REVIEW_LLM_THINKING_ENABLED", REVIEW_LLM_THINKING_ENABLED)


def review_llm_primary_endpoint() -> ReviewLlmEndpoint:
    """构建默认本机中转端点。"""
    model = review_llm_model()
    return ReviewLlmEndpoint(
        api_key=review_llm_api_key(),
        model=model,
        base_url=review_llm_base_url(),
        display_name=model,
    )


def review_llm_deepseek_fallback_endpoint(
    primary: ReviewLlmEndpoint,
) -> ReviewLlmEndpoint | None:
    """本机中转异常时返回可直连的 DeepSeek 降级端点，未配置密钥时不降级。"""
    if not read_bool_environment("REVIEW_LLM_FALLBACK_ENABLED", True):
        return None
    api_key = (
        read_process_or_windows_user_environment("REVIEW_LLM_FALLBACK_API_KEY")
        or read_process_or_windows_user_environment("DEEPSEEK_API_KEY")
    )
    if not api_key:
        return None
    model = (os.getenv("REVIEW_LLM_FALLBACK_MODEL") or REVIEW_LLM_FALLBACK_MODEL).strip()
    base_url = (
        os.getenv("REVIEW_LLM_FALLBACK_BASE_URL") or REVIEW_LLM_FALLBACK_BASE_URL
    ).strip().rstrip("/")
    if not model or not base_url or (primary.model == model and primary.base_url == base_url):
        return None
    return ReviewLlmEndpoint(
        api_key=api_key,
        model=model,
        base_url=base_url,
        display_name="DeepSeek",
    )


def bounded_int_environment(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """读取有上下界的整数环境变量，非法值安全回退。"""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


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
    """在调用复习模型前用可解释信号过滤杂项，只决定是否送模和资料类别。"""
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
    return select_representative_evidences(
        clean_review_evidences(evidences),
        limit=STANDARD_EVIDENCE_LIMIT,
    )


def clean_review_evidences(evidences: list[Evidence]) -> list[Evidence]:
    """清洗整份资料但不提前截断，让结构化问题可以完整参与选题。"""
    result: list[Evidence] = []
    for item in evidences:
        snippet = clean_content_text(item.snippet)
        if is_noise_fragment(snippet):
            continue
        cleaned = compact_text(snippet, 600)
        if cleaned:
            result.append(item.model_copy(update={"snippet": cleaned}))
    return result


def select_review_prompt_evidences(
    evidences: list[Evidence],
    source_questions: list[dict[str, str]],
    *,
    prioritized_evidence_ids: set[str] | None = None,
    limit: int | None = None,
) -> list[Evidence]:
    """优先保留 Curator/问题片段及相邻答案，再用整篇均匀采样补足上下文。"""
    required_questions = required_structured_source_questions(source_questions)
    prioritized_ids = {str(item) for item in (prioritized_evidence_ids or set()) if str(item)}
    if not required_questions and not prioritized_ids:
        return sorted(evidences, key=evidence_position)

    ordered = sorted(evidences, key=evidence_position)
    preferred_ids = {
        *prioritized_ids,
        *(str(item.get("evidenceId") or "") for item in required_questions),
    }
    expanded_ids = set(preferred_ids)
    for index, evidence in enumerate(ordered[:-1]):
        if evidence.evidenceId in preferred_ids:
            expanded_ids.add(ordered[index + 1].evidenceId)

    # limit 仅供未来调用方控制请求体预算；默认完整发送，不能成为卡片数量的隐式上限。
    resolved_limit = len(ordered) if limit is None else max(1, int(limit))
    preferred = [item for item in ordered if item.evidenceId in expanded_ids]
    selected = preferred[:resolved_limit]
    selected_ids = {item.evidenceId for item in selected}
    remaining = resolved_limit - len(selected)
    if remaining > 0:
        candidates = [item for item in ordered if item.evidenceId not in selected_ids]
        selected.extend(select_representative_evidences(candidates, limit=remaining))
    return sorted(selected[:resolved_limit], key=evidence_position)


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


def extract_source_question_candidates(
    evidences: list[Evidence],
    *,
    limit: int | None = None,
) -> list[dict[str, str]]:
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
            if limit is not None and len(result) >= max(1, int(limit)):
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
    return result


def review_card_limit(
    source_questions: list[dict[str, str]],
    curator_knowledge_unit_count: int = 0,
) -> int:
    """返回旧调度器所需的建议数量；此值不是发布上限。"""
    required_questions = required_structured_source_questions(source_questions)
    if not required_questions and curator_knowledge_unit_count <= 0:
        return STANDARD_REVIEW_CARD_LIMIT
    return max(STANDARD_REVIEW_CARD_LIMIT, len(required_questions), curator_knowledge_unit_count)


def structured_source_questions(source_questions: list[dict[str, str]]) -> list[tuple[str, str]]:
    """返回必须逐项覆盖的结构化原始问题，普通资料不强制逐题映射。"""
    result: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in source_questions:
        question = compact_text(item.get("question"), 180)
        key = canonical_source_question_key(question)
        if not key or key in seen or not is_structured_source_question(question):
            continue
        seen.add(key)
        result.append((key, question))
    if len(result) <= STANDARD_REVIEW_CARD_LIMIT:
        return []
    return result


def required_structured_source_questions(
    source_questions: list[dict[str, str]],
) -> list[dict[str, str]]:
    """只在自包含问题超过普通卡片上限时返回必须完整覆盖的清单。"""
    required_keys = {key for key, _question in structured_source_questions(source_questions)}
    if not required_keys:
        return []
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in source_questions:
        question = compact_text(item.get("question"), 180)
        key = canonical_source_question_key(question)
        if not question or key not in required_keys or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def canonical_source_question_key(value: str | None) -> str:
    """把 ASR 口语前缀、重复引导语和同义复述归并为稳定的原始问题键。"""
    question = compact_text(value, 180) or ""
    if not question:
        return ""

    # 面经视频常把“就是可能他会问你，嗯”这类引导语混在真正问题前面。
    question = re.sub(
        r"^(?:(?:就是|可能|然后|还有|嗯|啊|呃|哎|好|就比如说|我就被问过|"
        r"他会问你|他会问|面试官(?:可能)?(?:会)?问你|"
        r"第[一二三四五六七八九十百千万0-9]+个(?:说|是)?)[，,、:：\s]*)+",
        "",
        question,
    )
    question = re.sub(
        r"^(?:(?:嗯|啊|呃|哎|就是|可能|然后|还有)[，,、:：\s]*)+",
        "",
        question,
    )
    question = re.sub(r"^(?:那|那么|这时候)\s*", "", question)

    # “不可变数据，不可变数据有哪些”只保留真正承担疑问含义的尾部。
    parts = [part.strip() for part in re.split(r"[，,、]", question) if part.strip()]
    if len(parts) >= 2:
        previous = normalized_sentence(parts[-2])
        tail = normalized_sentence(parts[-1])
        if len(previous) >= 4 and previous in tail:
            question = parts[-1]

    # 结尾语气词不应让同一问题产生不同覆盖键。
    question = re.sub(r"[，,、\s]*(?:呢|啊|呀|吧)+[？?。！!]*$", "", question)
    return normalized_sentence(question)


def minimum_structured_question_coverage(
    source_questions: list[dict[str, str]],
    expected_count: int,
    *,
    generation_mode: str | None = None,
) -> int:
    """按资料口语程度和门禁档位计算结构化原始问题最低覆盖数。"""
    if expected_count <= 0:
        return 0
    speech_markers = sum(
        bool(SPEECH_QUESTION_MARKER_PATTERN.search(str(item.get("question") or "")))
        for item in source_questions
    )
    is_speech_material = speech_markers >= 3 and speech_markers / max(1, len(source_questions)) >= 0.08
    if generation_mode is None:
        # 旧测试和外部调用方未传档位时保留历史严格行为。
        return expected_count if not is_speech_material else min(expected_count, max(1, ceil(expected_count * 0.75)))
    mode = normalize_generation_mode(generation_mode)
    # 新流程对干净结构化资料保留 85% 覆盖线，对口语资料使用更低的容错线。
    if not is_speech_material:
        ratio = 0.45 if mode == "RELAXED" else 0.85
    else:
        ratio = 0.45 if mode == "RELAXED" else 0.65
    return min(expected_count, max(1, ceil(expected_count * ratio)))


def is_structured_source_question(value: str | None) -> bool:
    """结构化清单只接受无需口播上下文即可独立理解的专业问题。"""
    question = compact_text(value, 180) or ""
    if not is_high_quality_review_question(question):
        return False
    return not bool(
        re.search(
            r"(?:这个|那个|这些|它们|他们|刚才|下面这个图|这个消息|该怎么办|"
            r"大家|同学们|我们|你来看|回想一下|面试官问的是)",
            question,
        )
    )


def is_meaningful_source_question(value: str) -> bool:
    """排除寒暄、确认和无知识目标的反问。"""
    compact = " ".join(value.split()).strip()
    normalized = normalized_sentence(compact)
    if len(normalized) < 5 or is_noise_fragment(compact):
        return False
    if re.search(r"(?:对吧|对不对|是吧|没错吧|有没有问题)$", normalized):
        return False
    if re.search(r"是不是(?:就|很|也|又|已经|还)", normalized):
        return False
    if re.fullmatch(
        r"(?:该|那|这|它|他们|它们|这些|这样|这种|那它)?(?:到底)?(?:是|该)?(?:怎么办|怎么做)(?:的)?(?:呢)?",
        normalized,
    ):
        return False
    if re.search(r"(?:下面这个图|找一二三四五六七|它们是不是|是不是很方便)", normalized):
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
    return not bool(
        re.search(
            r"(?:介绍|讲解|说明|分析|讨论|解释|判断|检查|确认|验证|查看|取决于)\s*$",
            reporting_prefix,
        )
    )


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


def resolve_card_source_questions(
    raw_card: dict[str, Any],
    evidence_refs: tuple[Evidence, ...],
    candidates: list[dict[str, str]],
    *,
    review_question: str,
) -> list[str]:
    """兼容单个与多个来源问题，并把显式覆盖键限制在真实候选集合内。"""
    candidate_by_key = {
        canonical_source_question_key(question): question
        for candidate in candidates
        if (question := compact_text(candidate.get("question"), 180))
        and is_meaningful_source_question(question)
    }
    raw_source_questions = raw_card.get("sourceQuestions")
    raw_covered_keys = raw_card.get("coveredSourceQuestionKeys")
    has_plural_contract = isinstance(raw_source_questions, list) or isinstance(raw_covered_keys, list)
    resolved: list[str] = []
    seen: set[str] = set()

    def add_candidate(value: object) -> None:
        """只接纳逐字匹配或稳定键命中的真实原始问题。"""
        requested = value.get("question") if isinstance(value, dict) else value
        key = canonical_source_question_key(compact_text(requested, 180))
        question = candidate_by_key.get(key)
        if question and key not in seen:
            seen.add(key)
            resolved.append(question)

    if isinstance(raw_source_questions, list):
        for item in raw_source_questions:
            add_candidate(item)
    if isinstance(raw_covered_keys, list):
        for item in raw_covered_keys:
            add_candidate(item)
    if has_plural_contract:
        add_candidate(raw_card.get("sourceQuestion"))
        return resolved
    legacy = resolve_source_question(
        raw_card.get("sourceQuestion"),
        evidence_refs,
        candidates,
        review_question=review_question,
    )
    return [legacy] if legacy else []


def resolve_source_question(
    value: object,
    evidence_refs: tuple[Evidence, ...],
    candidates: list[dict[str, str]],
    *,
    review_question: str,
) -> str | None:
    """把 sourceQuestion 作为资料级可选审计信息，问题与答案允许位于相邻 evidence。"""
    validated = validated_source_question(value, evidence_refs, candidates)
    if validated:
        return validated
    requested = compact_text(value, 180)
    requested_key = normalized_sentence(requested or "")
    for candidate in candidates:
        candidate_question = compact_text(candidate.get("question"), 180)
        if (
            candidate_question
            and requested_key
            and normalized_sentence(candidate_question) == requested_key
            and is_meaningful_source_question(candidate_question)
            and source_question_similarity(review_question, candidate_question) >= 0.18
        ):
            return candidate_question
    global_match = best_matching_source_question(review_question, candidates)
    if global_match:
        return global_match
    evidence_ids = {reference.evidenceId for reference in evidence_refs}
    referenced = [
        question
        for candidate in candidates
        if candidate.get("evidenceId") in evidence_ids
        and (question := compact_text(candidate.get("question"), 180))
        and is_meaningful_source_question(question)
    ]
    if len(referenced) == 1:
        return referenced[0]
    if not referenced:
        return None
    # 一个切块含多个原问句时，只在卡面与其中一项有明确字符重合且最优项唯一时自动关联。
    scored = sorted(
        (
            source_question_similarity(review_question, candidate),
            index,
            candidate,
        )
        for index, candidate in enumerate(referenced)
    )
    best_score, _best_index, best = scored[-1]
    second_score = scored[-2][0] if len(scored) > 1 else 0.0
    return best if best_score >= 0.18 and best_score > second_score else None


def best_matching_source_question(
    review_question: str,
    candidates: list[dict[str, str]],
) -> str | None:
    """从整份资料问题清单推断唯一主题匹配项，解除问题与答案必须同切块的限制。"""
    questions = [
        question
        for candidate in candidates
        if (question := compact_text(candidate.get("question"), 180))
        and is_meaningful_source_question(question)
    ]
    if not questions:
        return None
    scored = sorted(
        (source_question_similarity(review_question, candidate), index, candidate)
        for index, candidate in enumerate(questions)
    )
    best_score, _best_index, best = scored[-1]
    second_score = scored[-2][0] if len(scored) > 1 else 0.0
    return best if best_score >= 0.18 and best_score > second_score else None


def source_question_similarity(left: str, right: str) -> float:
    """用中文字符二元组比较卡面与同一 evidence 内的多个原始问题。"""
    left_key = normalized_sentence(left)
    right_key = normalized_sentence(right)
    if not left_key or not right_key:
        return 0.0
    left_grams = {left_key[index : index + 2] for index in range(max(1, len(left_key) - 1))}
    right_grams = {right_key[index : index + 2] for index in range(max(1, len(right_key) - 1))}
    union = left_grams | right_grams
    return len(left_grams & right_grams) / len(union) if union else 0.0


def normalize_generated_summary(value: object) -> str | None:
    """只清洗并校验复习模型摘要，不从 evidence 或本地规则补写内容。"""
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
    """只接受可独立理解的面试官式问题，拒绝教材任务和转述式元话语。"""
    question = " ".join(str(value or "").split()).strip()
    normalized = normalized_sentence(question)
    if not 8 <= len(normalized) <= 180:
        return False
    if question_reveals_answer_structure(question):
        return False
    if contains_review_artifact(question) or is_noise_fragment(question):
        return False
    if re.match(r"^(?:就|还|接下来|下面|首先)?(?:必须|需要|要)?(?:先)?(?:搞定|来看|看看|讲解|学习)", question):
        return False
    if re.match(
        r"^(?:那|那么|然后|所以|这时|这时候|这个|这些|那这些|它|其|这里|那里|上述|前面|刚才|"
        r"大家|同学们|我们|面试官(?:可能)?(?:会)?(?:顺着)?问)",
        question,
    ):
        return False
    if re.match(r"^(?:请)?(?:说明|列出|概括|总结|梳理|阐述|指出|回忆)", question):
        return False
    if re.search(r"(?:如果)?面试官(?:要求|问|问到|提到).{0,80}(?:如何回答|怎么回答|你会怎么说)", question):
        return False
    if re.search(
        r"(?:什么意思|这些是什么|那是什么|到底什么意思|本节(?:的)?(?:核心内容|关键知识点|主要内容)|"
        r"本段(?:的)?(?:核心内容|关键知识点|主要内容)|这段(?:主要)?讲了什么|需要掌握什么)",
        question,
    ):
        return False
    question_form = re.search(
        r"什么|为什么|为何|如何|怎么|怎样|哪些|哪一种|哪种|哪个|是否|能否|有何|有什么|"
        r"区别|作用|含义|机制|流程|条件|场景|原因|由谁|由什么|通过什么|分别",
        question,
    )
    recall_instruction = re.match(
        r"^(?:请你?)?(?:解释|比较|分析|描述)\s*",
        question,
    )
    return bool(question_form or recall_instruction)


def question_reveals_answer_structure(value: str) -> bool:
    """拒绝在一个问题中先问主问题、再列答案子项继续追问的卡面。"""
    question = " ".join(str(value or "").split()).strip()
    if len(re.findall(r"[？?]", question)) > 1:
        return True
    if re.search(r"[？?].+(?:分别)?(?:如何|怎么|怎样|使用|实现|起什么作用)", question):
        return True
    if re.search(r"(?:解决方案|策略|类型|组成)(?:包括|例如|比如|如[：:]?)", question):
        return True
    return False


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


def answer_is_grounded(
    answer: str,
    evidence_refs: tuple[Evidence, ...],
    *,
    relaxed: bool = False,
) -> bool:
    """逐论断核验答案事实；宽松档降低重合阈值但仍要求每条事实有原文依据。"""
    answer_key = normalized_sentence(answer)
    source_key = normalized_sentence(" ".join(reference.snippet for reference in evidence_refs))
    if not answer_key or not source_key or contains_review_artifact(answer):
        return False
    if answer_key in source_key:
        return True
    claims = [normalized_sentence(claim) for claim in split_answer_claims(answer)]
    return bool(claims) and all(text_is_grounded(claim, source_key, relaxed=relaxed) for claim in claims)


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
        if sentence.lstrip().startswith("#"):
            # Markdown 标题只用于组织层次，不把“核心机制”等标题误判为独立事实。
            continue
        # 模型常用“此外/并通过/它使用”等在同一句追加新事实，不能让前半句的原文重合掩盖后半句幻觉。
        for raw_claim in connector_boundary.split(sentence):
            claim = re.sub(r"^(?:[-*+]>|\d+[.)])\s+", "", raw_claim.strip())
            claim = leading_connector.sub("", claim)
            if normalized_sentence(claim):
                claims.append(claim)
    return claims


def text_is_grounded(text_key: str, source_key: str, *, relaxed: bool = False) -> bool:
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
    minimum_coverage = 0.08 if relaxed else 0.12
    minimum_common = 4 if relaxed else 6
    return coverage >= minimum_coverage and longest_common_substring_length(text_key, source_key) >= min(minimum_common, len(text_key))


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
    """清理模型答案噪声并保留 Markdown 换行、列表和代码块结构。"""
    return normalize_markdown_text(value, maximum_length)


def normalize_markdown_text(value: object, maximum_length: int) -> str | None:
    """规范 Markdown 文本换行，避免再把结构化答案压成单行。"""
    if value is None:
        return None
    raw = str(value).replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return None
    lines = [line.rstrip() for line in raw.split("\n")]
    filtered = [
        line
        for line in lines
        if not re.match(
            r"^\s*(?:OCR\s*出现时间|视频画面(?:聚合)?|多一句没有[，,、 ]*少一句不行|高级软件人才培训专家)",
            line,
            flags=re.IGNORECASE,
        )
    ]
    text = "\n".join(filtered)
    text = re.sub(r"^\s*父段摘要[：:]\s*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text or contains_review_artifact(text):
        return None
    if len(text) <= maximum_length:
        return text
    return text[:maximum_length].rstrip() + "..."


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
