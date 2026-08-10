"""在切块和索引前纠正 ASR/OCR 的明显识别错误。"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Protocol

from app.core.io_concurrency import run_llm_io
from app.schemas.rag import DocumentBlock
from prompts.media import (
    RECOGNITION_TEXT_CORRECTION_PROMPT_VERSION,
    recognition_text_correction_system_prompt,
    recognition_text_correction_user_prompt,
)
from rag.observability.model_logging import log_model_call
from rag.observability.process_logger import logged_rag_method, process_event
from rag.observability.progress import RagProgressReporter


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
CORRECTION_NODE = "recognition-text-correction"
FALSE_VALUES = {"false", "0", "no", "off", "disabled"}
TRUE_VALUES = {"true", "1", "yes", "on", "enabled"}
VIDEO_FRAME_HEADING_PATTERN = re.compile(r"^(视频画面\s+\d{2}:\d{2}:\d{2}\s*\n)(.+)$", re.DOTALL)


@dataclass(frozen=True)
class RecognitionTextItem:
    """保存一个待纠错识别块的稳定 ID、通道和纯识别文本。"""

    block_id: str
    channel: str
    text: str


@dataclass(frozen=True)
class CorrectionBatchResult:
    """保存一次批量模型纠错结果及非敏感诊断信息。"""

    corrected_texts: dict[str, str]
    provider: str
    model: str


class RecognitionTextCorrector(Protocol):
    """定义纠错节点所需的最小客户端契约，便于离线测试替换。"""

    available: bool
    enabled: bool
    skip_reason: str
    max_batch_items: int
    max_batch_chars: int

    def correct_batch(self, items: list[RecognitionTextItem]) -> CorrectionBatchResult:
        """纠正一批识别文本并按 blockId 返回。"""


class BailianRecognitionTextCorrector:
    """调用百炼 OpenAI 兼容接口执行批量 ASR/OCR 语义纠错。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        enabled: bool | str | None = None,
        timeout_seconds: float | None = None,
        max_batch_items: int | None = None,
        max_batch_chars: int | None = None,
        http_client: Any | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.base_url = (base_url or os.getenv("RAG_TEXT_CORRECTION_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.getenv("RAG_TEXT_CORRECTION_MODEL") or DEFAULT_MODEL
        raw_enabled = enabled if enabled is not None else os.getenv("RAG_TEXT_CORRECTION_ENABLED", "auto")
        self.enabled_mode = normalize_enabled_mode(raw_enabled)
        self.timeout_seconds = timeout_seconds or float(os.getenv("RAG_TEXT_CORRECTION_TIMEOUT_SECONDS", "45"))
        self.max_batch_items = bounded_positive_int(
            max_batch_items,
            env_name="RAG_TEXT_CORRECTION_BATCH_MAX_ITEMS",
            default=32,
            maximum=100,
        )
        self.max_batch_chars = bounded_positive_int(
            max_batch_chars,
            env_name="RAG_TEXT_CORRECTION_BATCH_MAX_CHARS",
            default=12000,
            maximum=50000,
        )
        self.http_client = http_client

    @classmethod
    def from_env(cls) -> "BailianRecognitionTextCorrector":
        """从统一环境变量创建默认纠错客户端。"""
        return cls()

    @property
    def enabled(self) -> bool:
        """判断配置是否允许执行纠错；auto 模式仅在存在密钥时开启。"""
        if self.enabled_mode == "false":
            return False
        if self.enabled_mode == "true":
            return True
        return bool(self.api_key)

    @property
    def available(self) -> bool:
        """判断当前是否具备真实调用纠错模型的条件。"""
        return self.enabled and bool(self.api_key)

    @property
    def skip_reason(self) -> str:
        """返回跳过节点的稳定原因，避免把密钥或正文写入日志。"""
        if self.enabled_mode == "false":
            return "disabled"
        if not self.api_key:
            return "missing_api_key"
        return "unavailable"

    def correct_batch(self, items: list[RecognitionTextItem]) -> CorrectionBatchResult:
        """调用百炼纠正一批识别文本；网络或响应异常交由节点降级。"""
        if not items:
            return CorrectionBatchResult(corrected_texts={}, provider="dashscope", model=self.model)
        if not self.available:
            raise RuntimeError("ASR/OCR 纠错模型未启用或 DASHSCOPE_API_KEY 未配置")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": recognition_text_correction_system_prompt()},
                {
                    "role": "user",
                    "content": recognition_text_correction_user_prompt(
                        [
                            {"blockId": item.block_id, "channel": item.channel, "text": item.text}
                            for item in items
                        ]
                    ),
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with log_model_call(
            stage="parse.text_correction",
            action="recognition_text_correction",
            model_name=self.model,
            event="ASR/OCR 识别文本错别字纠正",
            extra_context={
                "itemCount": len(items),
                "textChars": sum(len(item.text) for item in items),
                "channels": sorted({item.channel for item in items}),
                "promptVersion": RECOGNITION_TEXT_CORRECTION_PROMPT_VERSION,
            },
            recoverable=True,
            fallback_message=f"使用 {self.model} 模型纠正 ASR/OCR 文本失败，已保留原识别结果继续处理",
        ):
            response = run_llm_io(lambda: self._post(payload, headers))
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code} {str(getattr(response, 'text', ''))[:300]}")
            try:
                data = response.json()
            except Exception as exc:
                raise RuntimeError("纠错模型响应不是合法 JSON") from exc
            content = extract_message_content(data).strip()
            corrected_texts = parse_correction_response(content)
        return CorrectionBatchResult(
            corrected_texts=corrected_texts,
            provider="dashscope",
            model=self.model,
        )

    def _post(self, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        """发送 OpenAI 兼容请求，测试可注入不访问网络的 HTTP 客户端。"""
        if self.http_client is not None:
            return self.http_client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self.timeout_seconds,
            )
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("使用 ASR/OCR 纠错模型需要安装 httpx 依赖") from exc
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)


@logged_rag_method("parse.text_correction", "correct_recognition_blocks", "纠正 ASR/OCR 识别文本")
def correct_recognition_blocks(
    blocks: list[DocumentBlock],
    *,
    corrector: RecognitionTextCorrector,
    progress_reporter: RagProgressReporter | None = None,
) -> tuple[list[DocumentBlock], list[str]]:
    """在摘要、切块和索引前纠正识别块，并保留原文与失败降级信息。"""
    prepared = prepare_items(blocks)
    if not prepared:
        return blocks, []
    if not corrector.available:
        skipped = annotate_skipped_blocks(blocks, prepared, corrector.skip_reason)
        warnings = []
        if corrector.enabled and corrector.skip_reason == "missing_api_key":
            warnings.append("parse.text_correction: 已启用 ASR/OCR 纠错，但未配置 DASHSCOPE_API_KEY，保留原识别结果")
        return skipped, warnings

    emit_progress(progress_reporter, "正在纠正 ASR/OCR 识别文本中的明显错别字", status="RUNNING")
    updated = list(blocks)
    warnings: list[str] = []
    batches, oversized = build_batches(
        prepared,
        max_items=corrector.max_batch_items,
        max_chars=corrector.max_batch_chars,
    )
    for block_index, item, _prefix in oversized:
        updated[block_index] = annotate_block(
            updated[block_index],
            status="skipped_too_long",
            applied=False,
            reason=f"文本长度超过单批上限 {corrector.max_batch_chars}",
        )
    if oversized:
        warnings.append(
            f"parse.text_correction: {len(oversized)} 个识别块超过 {corrector.max_batch_chars} 字符，已保留原文"
        )

    corrected_count = 0
    rejected_count = 0
    for batch in batches:
        items = [entry[1] for entry in batch]
        try:
            result = corrector.correct_batch(items)
        except Exception as exc:
            warnings.append(f"parse.text_correction: 纠错批次失败（{exc.__class__.__name__}），已保留原识别结果")
            for block_index, _item, _prefix in batch:
                updated[block_index] = annotate_block(
                    updated[block_index],
                    status="failed",
                    applied=False,
                    reason=exc.__class__.__name__,
                )
            continue

        for block_index, item, prefix in batch:
            block = updated[block_index]
            candidate = normalize_text(result.corrected_texts.get(item.block_id, ""))
            if not candidate:
                updated[block_index] = annotate_block(
                    block,
                    status="unchanged",
                    applied=False,
                    provider=result.provider,
                    model=result.model,
                )
                continue
            if candidate == normalize_text(item.text):
                updated[block_index] = annotate_block(
                    block,
                    status="unchanged",
                    applied=False,
                    provider=result.provider,
                    model=result.model,
                )
                continue
            if not safe_correction(item.text, candidate):
                rejected_count += 1
                updated[block_index] = annotate_block(
                    block,
                    status="rejected",
                    applied=False,
                    reason="模型结果与原文差异过大",
                    provider=result.provider,
                    model=result.model,
                )
                continue
            corrected_count += 1
            corrected_content = f"{prefix}{candidate}" if prefix else candidate
            metadata = {
                **block.metadata,
                "originalContentText": block.contentText,
                "correctionNode": CORRECTION_NODE,
                "correctionStatus": "applied",
                "correctionApplied": True,
                "correctionProvider": result.provider,
                "correctionModel": result.model,
                "correctionPromptVersion": RECOGNITION_TEXT_CORRECTION_PROMPT_VERSION,
            }
            parse_engine = block.parseEngine
            if "+text-correction" not in parse_engine:
                parse_engine = f"{parse_engine}+text-correction"
            updated[block_index] = block.model_copy(
                update={
                    "contentText": corrected_content,
                    "parseEngine": parse_engine,
                    "metadata": metadata,
                }
            )

    process_event(
        stage="parse.text_correction",
        action="recognition_text_correction_summary",
        message="ASR/OCR 识别文本纠错节点处理完成",
        context={
            "eligibleBlockCount": len(prepared),
            "correctedBlockCount": corrected_count,
            "rejectedBlockCount": rejected_count,
            "batchCount": len(batches),
        },
    )
    emit_progress(
        progress_reporter,
        f"ASR/OCR 错别字纠正完成：修改 {corrected_count} 个识别块",
        status="COMPLETED",
    )
    return updated, warnings


def prepare_items(blocks: list[DocumentBlock]) -> list[tuple[int, RecognitionTextItem, str]]:
    """筛选 ASR/OCR 原始块，并把视频画面标题从模型输入中剥离。"""
    prepared: list[tuple[int, RecognitionTextItem, str]] = []
    for index, block in enumerate(blocks):
        if block.metadata.get("correctionNode") == CORRECTION_NODE:
            continue
        channel = recognition_channel(block)
        if not channel:
            continue
        prefix, text = split_preserved_prefix(block, channel)
        normalized = normalize_text(text)
        if not normalized or is_ocr_placeholder(normalized):
            continue
        prepared.append((index, RecognitionTextItem(block.blockId, channel, normalized), prefix))
    return prepared


def recognition_channel(block: DocumentBlock) -> str | None:
    """判断块是否来自 ASR/字幕或 OCR，派生摘要和原生文本不参与二次纠错。"""
    channel = str(block.metadata.get("evidenceChannel") or "").strip().lower()
    engine = block.parseEngine.lower()
    if channel == "subtitle":
        return "asr"
    if channel == "frame_ocr":
        return "ocr"
    if "ocr" in engine and block.blockType in {"text", "image", "table"}:
        return "ocr"
    if "asr" in engine or "transcript" in engine:
        return "asr"
    return None


def split_preserved_prefix(block: DocumentBlock, channel: str) -> tuple[str, str]:
    """保留视频画面时间标题，避免模型改动 evidence 时间定位。"""
    if channel != "ocr" or block.metadata.get("evidenceChannel") != "frame_ocr":
        return "", block.contentText
    match = VIDEO_FRAME_HEADING_PATTERN.match(block.contentText)
    if not match:
        return "", block.contentText
    return match.group(1), match.group(2)


def build_batches(
    prepared: list[tuple[int, RecognitionTextItem, str]],
    *,
    max_items: int,
    max_chars: int,
) -> tuple[list[list[tuple[int, RecognitionTextItem, str]]], list[tuple[int, RecognitionTextItem, str]]]:
    """按条数和字符预算组成批次，避免长视频产生逐句模型请求。"""
    batches: list[list[tuple[int, RecognitionTextItem, str]]] = []
    oversized: list[tuple[int, RecognitionTextItem, str]] = []
    current: list[tuple[int, RecognitionTextItem, str]] = []
    current_chars = 0
    for entry in prepared:
        item_chars = len(entry[1].text)
        if item_chars > max_chars:
            oversized.append(entry)
            continue
        if current and (len(current) >= max_items or current_chars + item_chars > max_chars):
            batches.append(current)
            current = []
            current_chars = 0
        current.append(entry)
        current_chars += item_chars
    if current:
        batches.append(current)
    return batches, oversized


def annotate_skipped_blocks(
    blocks: list[DocumentBlock],
    prepared: list[tuple[int, RecognitionTextItem, str]],
    reason: str,
) -> list[DocumentBlock]:
    """标记未启用节点的识别块，避免同一解析流程重复判断。"""
    updated = list(blocks)
    for block_index, _item, _prefix in prepared:
        updated[block_index] = annotate_block(
            updated[block_index],
            status="skipped",
            applied=False,
            reason=reason,
        )
    return updated


def annotate_block(
    block: DocumentBlock,
    *,
    status: str,
    applied: bool,
    reason: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> DocumentBlock:
    """写入统一纠错诊断元数据，不覆盖原 evidence 字段。"""
    metadata = {
        **block.metadata,
        "correctionNode": CORRECTION_NODE,
        "correctionStatus": status,
        "correctionApplied": applied,
        "correctionPromptVersion": RECOGNITION_TEXT_CORRECTION_PROMPT_VERSION,
    }
    if reason:
        metadata["correctionReason"] = reason
    if provider:
        metadata["correctionProvider"] = provider
    if model:
        metadata["correctionModel"] = model
    return block.model_copy(update={"metadata": metadata})


def safe_correction(original: str, candidate: str) -> bool:
    """拒绝疑似总结或扩写，只允许与原识别文本高度相似的修正。"""
    original_compact = re.sub(r"\s+", "", original)
    candidate_compact = re.sub(r"\s+", "", candidate)
    if not original_compact or not candidate_compact:
        return False
    length_ratio = len(candidate_compact) / len(original_compact)
    if length_ratio < 0.55 or length_ratio > 1.45:
        return False
    try:
        min_similarity = float(os.getenv("RAG_TEXT_CORRECTION_MIN_SIMILARITY", "0.55"))
    except ValueError:
        min_similarity = 0.55
    min_similarity = max(0.0, min(1.0, min_similarity))
    return SequenceMatcher(None, original_compact, candidate_compact).ratio() >= min_similarity


def parse_correction_response(content: str) -> dict[str, str]:
    """解析纠错模型唯一 JSON 对象，并忽略缺少稳定 ID 的条目。"""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("纠错模型未返回合法 JSON 对象") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise ValueError("纠错模型 JSON 缺少 items 数组")
    result: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        block_id = item.get("blockId")
        corrected_text = item.get("correctedText")
        if isinstance(block_id, str) and block_id.strip() and isinstance(corrected_text, str):
            result[block_id.strip()] = corrected_text
    return result


def extract_message_content(data: dict[str, Any]) -> str:
    """兼容 OpenAI Chat Completions 的字符串或分段 content。"""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item.get("content"), str):
                parts.append(item["content"])
        return "\n".join(parts)
    return str(content)


def normalize_enabled_mode(value: bool | str) -> str:
    """把布尔值和常见开关字符串归一化为 true/false/auto。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    normalized = str(value).strip().lower()
    if normalized in TRUE_VALUES:
        return "true"
    if normalized in FALSE_VALUES:
        return "false"
    return "auto"


def bounded_positive_int(value: int | None, *, env_name: str, default: int, maximum: int) -> int:
    """读取正整数配置并限制上界，避免单次 Prompt 无界增长。"""
    raw: Any = value if value is not None else os.getenv(env_name, str(default))
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


def normalize_text(text: str) -> str:
    """仅归一化换行和空白，不在确定性阶段猜测错别字。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def is_ocr_placeholder(text: str) -> bool:
    """跳过没有真实识别内容的图片占位文本。"""
    return text.startswith("[图片]") and "未获得可索引文字" in text


def emit_progress(progress_reporter: RagProgressReporter | None, message: str, *, status: str) -> None:
    """把纠错节点状态同步到资料处理进度，不改变既有八步主流程。"""
    if progress_reporter is None:
        return
    progress_reporter.emit(
        "parse.text_correction",
        message,
        status=status,
        current_step=3,
        total_steps=8,
        percent=23,
    )
