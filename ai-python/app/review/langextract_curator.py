"""使用官方 LangExtract 从完整资料中发现可追踪的复习知识单元。"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Any, Callable

from app.core.environment import read_process_or_windows_user_environment
from app.core.io_concurrency import configured_io_workers, process_io_limiter
from app.review.knowledge_extractor import (
    REVIEW_LLM_BASE_URL,
    REVIEW_LLM_MODEL,
    REVIEW_LLM_REASONING_EFFORT,
    compact_text,
    normalized_sentence,
)
from app.schemas.rag import Evidence


LANGEXTRACT_CURATOR_VERSION = "langextract-curator-v1"
MAX_PRODUCTION_CURATOR_UNITS = 32


@dataclass(frozen=True)
class EvidenceTextSpan:
    """一条 evidence 在送入 LangExtract 的连续文本中的字符范围。"""

    evidence_id: str
    start: int
    end: int


@dataclass(frozen=True)
class CuratorCandidate:
    """一条已精确定位回原文和 evidence 的候选知识单元。"""

    text: str
    topic: str | None
    knowledge_type: str | None
    evidence_ids: tuple[str, ...]
    char_start: int
    char_end: int
    alignment_status: str | None = None


@dataclass
class ModelUsageAudit:
    """记录实验实际模型请求和 Token，不保存提示词或密钥。"""

    request_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    max_requests: int | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def begin_request(self) -> None:
        """原子预占一次模型预算，超出上限时在发出请求前停止。"""
        with self._lock:
            if self.max_requests is not None and self.request_count >= self.max_requests:
                raise RuntimeError(f"A/B 单臂模型请求预算已耗尽：最多 {self.max_requests} 次")
            self.request_count += 1

    def record_response(self, request: dict[str, Any], response: Any) -> None:
        """优先采用供应商 usage，缺失时使用统一 tokenizer 估算。"""
        usage = getattr(response, "usage", None)
        input_tokens = int(
            getattr(usage, "prompt_tokens", None)
            or getattr(usage, "input_tokens", None)
            or estimate_chat_tokens(request.get("messages") or [])
        )
        output_text = ""
        choices = getattr(response, "choices", None) or []
        if choices:
            output_text = str(getattr(choices[0].message, "content", None) or "")
        output_tokens = int(
            getattr(usage, "completion_tokens", None)
            or getattr(usage, "output_tokens", None)
            or estimate_text_tokens(output_text)
        )
        with self._lock:
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens

    @property
    def total_tokens(self) -> int:
        """返回输入与输出 Token 总量。"""
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class LangExtractCuratorResult:
    """LangExtract 候选、定位质量与调用成本快照。"""

    candidates: tuple[CuratorCandidate, ...]
    raw_extraction_count: int
    grounded_extraction_count: int
    duplicate_count: int
    duration_seconds: float
    usage: ModelUsageAudit
    source_character_count: int
    version: str = LANGEXTRACT_CURATOR_VERSION


class _CompletionsAuditProxy:
    """代理 OpenAI-compatible 请求并采集官方响应 usage。"""

    def __init__(self, delegate: Any, audit: ModelUsageAudit, max_in_flight: int) -> None:
        self._delegate = delegate
        self._audit = audit
        self._max_in_flight = max_in_flight

    def create(self, **kwargs: Any) -> Any:
        self._audit.begin_request()
        # LangExtract 会为同一批文本块创建线程；进程级闸门同时防止多份资料叠加后压垮 DeepSeek。
        with process_io_limiter.slot("review.langextract", self._max_in_flight):
            response = self._delegate.create(**kwargs)
        self._audit.record_response(kwargs, response)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class _ChatAuditProxy:
    """只替换 chat.completions，其余官方客户端能力保持透明。"""

    def __init__(self, delegate: Any, audit: ModelUsageAudit, max_in_flight: int) -> None:
        self._delegate = delegate
        self.completions = _CompletionsAuditProxy(delegate.completions, audit, max_in_flight)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class _OpenAIClientAuditProxy:
    """为 LangExtract 官方 OpenAI provider 增加只读成本审计。"""

    def __init__(self, delegate: Any, audit: ModelUsageAudit, max_in_flight: int) -> None:
        self._delegate = delegate
        self.chat = _ChatAuditProxy(delegate.chat, audit, max_in_flight)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)


class LangExtractKnowledgeCurator:
    """直接调用 LangExtract 1.6，从完整 evidence 文本发现高召回候选。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = REVIEW_LLM_MODEL,
        base_url: str = REVIEW_LLM_BASE_URL,
        reasoning_effort: str = REVIEW_LLM_REASONING_EFFORT,
        extraction_passes: int = 2,
        max_char_buffer: int = 8000,
        max_workers: int | None = None,
        max_model_requests: int = 32,
        timeout_seconds: float = 120.0,
        extract_function: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.reasoning_effort = reasoning_effort
        self.extraction_passes = max(1, min(5, int(extraction_passes)))
        self.max_char_buffer = max(1000, min(20000, int(max_char_buffer)))
        resolved_workers = max_workers if max_workers is not None else configured_io_workers(
            "REVIEW_LANGEXTRACT_MAX_WORKERS"
        )
        self.max_workers = max(1, min(10, int(resolved_workers)))
        self.max_model_requests = max(1, min(64, int(max_model_requests)))
        self.timeout_seconds = max(5.0, min(600.0, float(timeout_seconds)))
        self._extract_function = extract_function
        self.last_usage = ModelUsageAudit(max_requests=self.max_model_requests)

    def extract(self, title: str, evidences: list[Evidence]) -> LangExtractCuratorResult:
        """抽取全部陈述式与问答式知识，并只保留能精确回指原文的结果。"""
        source_text, spans = build_source_document(evidences)
        if not source_text:
            return LangExtractCuratorResult((), 0, 0, 0, 0.0, ModelUsageAudit(), 0)
        api_key = self.api_key or read_process_or_windows_user_environment("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY，无法运行 LangExtract Curator")

        audit = ModelUsageAudit(max_requests=self.max_model_requests)
        self.last_usage = audit
        started_at = time.perf_counter()
        annotated = self._run_langextract(title, source_text, api_key, audit)
        raw_extractions = list(getattr(annotated, "extractions", None) or [])
        grounded: list[CuratorCandidate] = []
        for extraction in raw_extractions:
            candidate = grounded_candidate(extraction, source_text, spans)
            if candidate is not None:
                grounded.append(candidate)
        unique, duplicate_count = deduplicate_curator_candidates(grounded)
        duration = time.perf_counter() - started_at
        return LangExtractCuratorResult(
            candidates=tuple(unique),
            raw_extraction_count=len(raw_extractions),
            grounded_extraction_count=len(grounded),
            duplicate_count=duplicate_count,
            duration_seconds=round(duration, 4),
            usage=audit,
            source_character_count=len(source_text),
        )

    def _run_langextract(
        self,
        title: str,
        source_text: str,
        api_key: str,
        audit: ModelUsageAudit,
    ) -> Any:
        """使用官方 OpenAI provider、中文 tokenizer、分块和多轮 passes。"""
        import langextract as lx
        from openai import OpenAI
        from langextract.providers.openai import OpenAILanguageModel
        from langextract.tokenizer import UnicodeTokenizer

        model = OpenAILanguageModel(
            model_id=self.model,
            api_key=api_key,
            base_url=self.base_url,
            max_workers=self.max_workers,
            reasoning_effort=self.reasoning_effort,
        )
        # 官方 provider 不暴露客户端 timeout 与 usage 聚合；这里保持其请求协议，只补齐与 A 臂相同的超时和审计。
        model._client = _OpenAIClientAuditProxy(  # noqa: SLF001
            OpenAI(api_key=api_key, base_url=self.base_url, timeout=self.timeout_seconds),
            audit,
            self.max_workers,
        )
        extractor = self._extract_function or lx.extract
        return extractor(
            text_or_documents=source_text,
            prompt_description=langextract_knowledge_prompt(),
            examples=langextract_knowledge_examples(lx),
            model=model,
            use_schema_constraints=False,
            fence_output=False,
            max_char_buffer=self.max_char_buffer,
            batch_length=max(self.max_workers, 1),
            max_workers=self.max_workers,
            extraction_passes=self.extraction_passes,
            context_window_chars=min(800, self.max_char_buffer // 5),
            additional_context=f"资料标题：{title}",
            resolver_params={
                "enable_fuzzy_alignment": False,
                "suppress_parse_errors": True,
            },
            tokenizer=UnicodeTokenizer(),
            show_progress=False,
        )


def langextract_knowledge_prompt() -> str:
    """返回高召回、强原文约束的知识单元抽取说明。"""
    return (
        "按原文出现顺序抽取所有值得单独复习的知识单元，不要求原文以问题形式表达。"
        "覆盖定义、组成、机制、原理、因果、流程、步骤、作用、优缺点、区别、条件、限制、参数、"
        "实践建议和原文明确列出的考点。列表中的每个独立项目分别抽取。"
        "extraction_text 必须逐字复制输入正文中的一个连续、能表达完整事实的片段，禁止改写、概括、"
        "补充外部知识或抽取字段说明。忽略寒暄、转场、确认语、广告和没有知识事实的口播。"
        "每条使用 extraction_class=knowledge_unit，并填写 topic 与 knowledge_type 属性。"
    )


def langextract_knowledge_examples(lx: Any) -> list[Any]:
    """提供与中文课程相符且可精确对齐的 few-shot 示例。"""
    return [
        lx.data.ExampleData(
            text=(
                "消息分区使存储不受单台服务器限制。顺序写通过追加数据减少磁盘寻址开销。"
                "好了，我们继续看下一点。"
            ),
            extractions=[
                lx.data.Extraction(
                    extraction_class="knowledge_unit",
                    extraction_text="消息分区使存储不受单台服务器限制",
                    attributes={"topic": "消息分区", "knowledge_type": "作用"},
                ),
                lx.data.Extraction(
                    extraction_class="knowledge_unit",
                    extraction_text="顺序写通过追加数据减少磁盘寻址开销",
                    attributes={"topic": "顺序写", "knowledge_type": "机制"},
                ),
            ],
        ),
        lx.data.ExampleData(
            text="浅拷贝和深拷贝有什么区别？浅拷贝只复制外层结构，深拷贝会递归复制内部可变对象。",
            extractions=[
                lx.data.Extraction(
                    extraction_class="knowledge_unit",
                    extraction_text="浅拷贝只复制外层结构，深拷贝会递归复制内部可变对象",
                    attributes={"topic": "浅拷贝和深拷贝", "knowledge_type": "区别"},
                ),
            ],
        ),
    ]


def build_source_document(evidences: list[Evidence]) -> tuple[str, tuple[EvidenceTextSpan, ...]]:
    """拼接完整 evidence，并记录每段的字符范围以便反向映射。"""
    parts: list[str] = []
    spans: list[EvidenceTextSpan] = []
    cursor = 0
    for evidence in evidences:
        snippet = compact_text(evidence.snippet, 20000)
        if not snippet:
            continue
        if parts:
            separator = "\n\n"
            parts.append(separator)
            cursor += len(separator)
        start = cursor
        parts.append(snippet)
        cursor += len(snippet)
        spans.append(EvidenceTextSpan(evidence.evidenceId, start, cursor))
    return "".join(parts), tuple(spans)


def grounded_candidate(
    extraction: Any,
    source_text: str,
    spans: tuple[EvidenceTextSpan, ...],
) -> CuratorCandidate | None:
    """拒绝未定位或模糊改写结果，并映射到最多两个真实 evidenceId。"""
    interval = getattr(extraction, "char_interval", None)
    start = getattr(interval, "start_pos", None)
    end = getattr(interval, "end_pos", None)
    if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= len(source_text):
        return None
    source_slice = source_text[start:end].strip()
    extracted_text = compact_text(getattr(extraction, "extraction_text", None), 1200)
    if not source_slice or not extracted_text:
        return None
    # 即使未来 resolver 配置变化，也不能让模糊对齐的改写内容进入候选池。
    if normalized_sentence(source_slice) != normalized_sentence(extracted_text):
        return None
    evidence_ids = tuple(
        span.evidence_id
        for span in spans
        if span.start < end and start < span.end
    )
    if not evidence_ids:
        return None
    attributes = getattr(extraction, "attributes", None)
    attributes = attributes if isinstance(attributes, dict) else {}
    status = getattr(extraction, "alignment_status", None)
    return CuratorCandidate(
        text=source_slice,
        topic=compact_text(attributes.get("topic"), 120),
        knowledge_type=compact_text(attributes.get("knowledge_type"), 80),
        evidence_ids=evidence_ids,
        char_start=start,
        char_end=end,
        alignment_status=str(getattr(status, "value", None) or status or "") or None,
    )


def deduplicate_curator_candidates(
    candidates: list[CuratorCandidate],
) -> tuple[list[CuratorCandidate], int]:
    """按原文事实归一化去重，保留最早出现且证据最稳定的一条。"""
    result: list[CuratorCandidate] = []
    seen: set[str] = set()
    duplicates = 0
    for candidate in sorted(candidates, key=lambda item: (item.char_start, item.char_end)):
        key = normalized_sentence(candidate.text)
        if not key or key in seen:
            duplicates += 1
            continue
        seen.add(key)
        result.append(candidate)
    return result, duplicates


def build_production_curator_context(
    result: LangExtractCuratorResult,
    *,
    limit: int = MAX_PRODUCTION_CURATOR_UNITS,
) -> dict[str, Any]:
    """把严格定位候选整理为线上生成图可消费的主题多样化知识单元。"""
    selected = select_production_curator_candidates(list(result.candidates), limit=limit)
    knowledge_units = [
        {
            "knowledgeUnitId": f"KU-{index:03d}",
            "text": candidate.text,
            "topic": candidate.topic,
            "knowledgeType": candidate.knowledge_type,
            # 卡片发布契约最多允许两个 evidenceId，候选上下文保持相同边界。
            "evidenceIds": list(candidate.evidence_ids[:2]),
        }
        for index, candidate in enumerate(selected, start=1)
    ]
    return {
        "status": "COMPLETED",
        "version": result.version,
        "knowledgeUnits": knowledge_units,
        "rawCandidateCount": result.raw_extraction_count,
        "groundedCandidateCount": result.grounded_extraction_count,
        "acceptedCandidateCount": len(result.candidates),
        "selectedKnowledgeUnitCount": len(knowledge_units),
        "duplicateCount": result.duplicate_count,
        "requestCount": result.usage.request_count,
        "durationSeconds": result.duration_seconds,
    }


def select_production_curator_candidates(
    candidates: list[CuratorCandidate],
    *,
    limit: int = MAX_PRODUCTION_CURATOR_UNITS,
) -> list[CuratorCandidate]:
    """先消除近重复，再按 topic 轮询，避免长资料前半段独占候选预算。"""
    bounded_limit = max(1, min(MAX_PRODUCTION_CURATOR_UNITS, int(limit)))
    unique: list[CuratorCandidate] = []
    for candidate in sorted(candidates, key=lambda item: (item.char_start, item.char_end)):
        if any(curator_candidates_are_near_duplicates(candidate, accepted) for accepted in unique):
            continue
        unique.append(candidate)

    groups: dict[str, list[CuratorCandidate]] = {}
    group_order: list[str] = []
    for candidate in unique:
        topic_key = normalized_sentence(candidate.topic or "")
        if not topic_key:
            # 缺少 topic 时使用较短原文前缀，避免把所有未知主题错误合并为一组。
            topic_key = f"unknown:{normalized_sentence(candidate.text)[:24]}"
        if topic_key not in groups:
            groups[topic_key] = []
            group_order.append(topic_key)
        groups[topic_key].append(candidate)

    selected: list[CuratorCandidate] = []
    offset = 0
    while len(selected) < bounded_limit:
        appended = False
        for group_key in group_order:
            group = groups[group_key]
            if offset >= len(group):
                continue
            selected.append(group[offset])
            appended = True
            if len(selected) >= bounded_limit:
                break
        if not appended:
            break
        offset += 1
    return sorted(selected, key=lambda item: (item.char_start, item.char_end))


def curator_candidates_are_near_duplicates(left: CuratorCandidate, right: CuratorCandidate) -> bool:
    """用保守的包含关系和字符二元组识别跨 pass 的近重复原文。"""
    left_text = normalized_sentence(left.text)
    right_text = normalized_sentence(right.text)
    if not left_text or not right_text:
        return False
    shorter, longer = sorted((left_text, right_text), key=len)
    if len(shorter) >= 10 and shorter in longer:
        return True
    left_pairs = {left_text[index : index + 2] for index in range(max(0, len(left_text) - 1))}
    right_pairs = {right_text[index : index + 2] for index in range(max(0, len(right_text) - 1))}
    if not left_pairs or not right_pairs:
        return False
    union = left_pairs | right_pairs
    return bool(union) and len(left_pairs & right_pairs) / len(union) >= 0.88


def estimate_chat_tokens(messages: list[dict[str, Any]]) -> int:
    """在供应商未返回 usage 时统一估算聊天输入 Token。"""
    text = "\n".join(str(item.get("content") or "") for item in messages)
    return estimate_text_tokens(text)


def estimate_text_tokens(text: str) -> int:
    """优先使用项目已有 tiktoken，缺失时采用保守字符估算。"""
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except (ImportError, KeyError, ValueError):
        return max(1, (len(text) + 1) // 2)
