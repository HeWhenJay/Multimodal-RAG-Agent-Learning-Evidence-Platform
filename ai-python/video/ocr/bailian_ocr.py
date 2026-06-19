from __future__ import annotations

import base64
import mimetypes
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from rag.model_logging import log_model_call
from rag.process_logger import process_event


DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen3.5-ocr"
DEFAULT_PROMPT = (
    "请只返回图片中的 OCR 文本，保留自然段、标题和表格结构。"
    "如果是表格，请优先使用 Markdown 表格；不要输出解释、免责声明或额外说明。"
)


@dataclass(frozen=True)
class OcrResult:
    text: str
    parser: str
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


OcrRetryCallback = Callable[[dict[str, Any]], None]


class BailianOcrClient:
    """Small adapter for Bailian/DashScope OpenAI-compatible OCR models."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        enabled: str | bool | None = None,
        timeout_seconds: float | None = None,
        max_image_bytes: int | None = None,
        max_attempts: int | None = None,
        retry_delay_seconds: float | None = None,
        http_client: Any | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.base_url = (base_url or os.getenv("BAILIAN_OCR_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.getenv("BAILIAN_OCR_MODEL") or DEFAULT_MODEL
        self.timeout_seconds = timeout_seconds or float(os.getenv("BAILIAN_OCR_TIMEOUT_SECONDS", "60"))
        self.max_image_bytes = max_image_bytes or int(os.getenv("BAILIAN_OCR_MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
        self.max_attempts = max(1, max_attempts or int(os.getenv("BAILIAN_OCR_MAX_ATTEMPTS", "3")))
        self.retry_delay_seconds = max(
            0.0,
            retry_delay_seconds
            if retry_delay_seconds is not None
            else float(os.getenv("BAILIAN_OCR_RETRY_DELAY_SECONDS", "2")),
        )
        self._http_client = http_client

        configured_flag = enabled if enabled is not None else os.getenv("BAILIAN_OCR_ENABLED", "auto")
        self.enabled = self._resolve_enabled(configured_flag)

    @classmethod
    def from_env(cls) -> "BailianOcrClient":
        return cls()

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def recognize_image_bytes(
        self,
        *,
        image_bytes: bytes,
        filename: str,
        mime_type: str | None = None,
        prompt: str = DEFAULT_PROMPT,
        retry_callback: OcrRetryCallback | None = None,
    ) -> OcrResult:
        if not self.enabled:
            return OcrResult(text="", parser="bailian-qwen-ocr-disabled")
        if not self.api_key:
            return OcrResult(text="", parser="bailian-qwen-ocr", warnings=["DASHSCOPE_API_KEY is missing"])
        if len(image_bytes) > self.max_image_bytes:
            return OcrResult(
                text="",
                parser="bailian-qwen-ocr",
                warnings=[f"Bailian OCR skipped: image is larger than {self.max_image_bytes} bytes"],
            )

        payload = self._build_payload(image_bytes=image_bytes, filename=filename, mime_type=mime_type, prompt=prompt)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        failures: list[dict[str, Any]] = []
        for attempt in range(1, self.max_attempts + 1):
            try:
                with log_model_call(
                    stage="parse.video.ocr",
                    action="bailian_ocr",
                    model_name=self.model,
                    event="图片或关键帧 OCR 识别",
                    extra_context={
                        "filename": filename,
                        "imageBytes": len(image_bytes),
                        "attempt": attempt,
                        "maxAttempts": self.max_attempts,
                    },
                    recoverable=True,
                    fallback_message=(
                        f"使用 {self.model} 模型完成图片或关键帧 OCR 识别事件失败，"
                        f"已超过 {self.max_attempts} 次重试并降级到本地 OCR 或跳过该帧继续处理"
                    ),
                ):
                    response = self._post(payload, headers)
                    if response.status_code >= 400:
                        raise RuntimeError(f"Bailian OCR returned HTTP {response.status_code}")
                    try:
                        data = response.json()
                    except Exception as exc:
                        raise RuntimeError(f"Bailian OCR response is not JSON: {exc}") from exc
                    text = normalize_text_for_ocr(_extract_message_content(data))
                    if not text:
                        raise RuntimeError("Bailian OCR returned empty text")
                break
            except Exception as exc:
                failure = self._build_retry_failure(
                    exc=exc,
                    attempt=attempt,
                    filename=filename,
                    image_bytes=len(image_bytes),
                )
                failures.append(failure)
                self._emit_retry_event(failure, retry_callback)
                if attempt < self.max_attempts and self.retry_delay_seconds > 0:
                    time.sleep(self.retry_delay_seconds)
        else:
            return OcrResult(
                text="",
                parser="bailian-qwen-ocr",
                warnings=[
                    format_retry_exhausted_warning(
                        model=self.model,
                        filename=filename,
                        failures=failures,
                    )
                ],
            )

        request_id = (
            data.get("request_id")
            or data.get("requestId")
            or getattr(response, "headers", {}).get("X-DashScope-Request-Id")
            or getattr(response, "headers", {}).get("x-request-id")
        )
        metadata = {"ocrModel": self.model}
        if request_id:
            metadata["requestId"] = request_id
        return OcrResult(
            text=text,
            parser="bailian-qwen-ocr",
            confidence=0.9,
            metadata=metadata,
        )

    def _build_retry_failure(self, *, exc: Exception, attempt: int, filename: str, image_bytes: int) -> dict[str, Any]:
        message = str(exc)
        normalized_message = message if message.startswith("Bailian OCR ") else f"Bailian OCR request failed: {message}"
        return {
            "filename": filename,
            "imageBytes": image_bytes,
            "modelName": self.model,
            "attempt": attempt,
            "maxAttempts": self.max_attempts,
            "nextAttempt": attempt + 1 if attempt < self.max_attempts else None,
            "retryDelaySeconds": self.retry_delay_seconds if attempt < self.max_attempts else 0,
            "errorType": exc.__class__.__name__,
            "errorMessage": normalized_message,
        }

    def _emit_retry_event(self, failure: dict[str, Any], retry_callback: OcrRetryCallback | None) -> None:
        attempt = int(failure["attempt"])
        max_attempts = int(failure["maxAttempts"])
        next_attempt = failure.get("nextAttempt")
        if next_attempt:
            message = (
                f"第 {attempt}/{max_attempts} 次 OCR 失败：{failure['errorMessage']}，"
                f"准备重试第 {next_attempt} 次"
            )
            action = "bailian_ocr_retry_scheduled"
        else:
            message = (
                f"第 {attempt}/{max_attempts} 次 OCR 失败：{failure['errorMessage']}，"
                "已达到最大重试次数，等待降级处理"
            )
            action = "bailian_ocr_retry_exhausted"
        log_ocr_retry_event(action=action, message=message, failure=failure)
        if retry_callback is not None:
            retry_callback({**failure, "message": message, "action": action})

    def _resolve_enabled(self, value: str | bool) -> bool:
        if isinstance(value, bool):
            return value
        normalized = str(value).strip().lower()
        if normalized in {"false", "0", "no", "off", "disabled"}:
            return False
        if normalized in {"true", "1", "yes", "on", "enabled"}:
            return True
        return bool(self.api_key)

    def _build_payload(self, *, image_bytes: bytes, filename: str, mime_type: str | None, prompt: str) -> dict[str, Any]:
        resolved_mime = mime_type or mimetypes.guess_type(filename)[0] or "image/png"
        if not resolved_mime.startswith("image/"):
            resolved_mime = "image/png"
        data_url = f"data:{resolved_mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "temperature": 0,
        }

    def _post(self, payload: dict[str, Any], headers: dict[str, str]) -> Any:
        url = f"{self.base_url}/chat/completions"
        if self._http_client is not None:
            return self._http_client.post(url, headers=headers, json=payload, timeout=self.timeout_seconds)

        import httpx

        with httpx.Client(timeout=self.timeout_seconds) as client:
            return client.post(url, headers=headers, json=payload)


def _extract_message_content(data: dict[str, Any]) -> str:
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
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif item.get("type") in {"text", "output_text"} and isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(parts)
    return str(content)


def normalize_text_for_ocr(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def log_ocr_retry_event(*, action: str, message: str, failure: dict[str, Any]) -> None:
    """记录 OCR 重试过程，便于控制面板展示第几次失败和下一次重试。"""
    process_event(
        stage="parse.video.ocr",
        action=action,
        message=message,
        level="WARN",
        success=True,
        context=failure,
    )


def format_retry_exhausted_warning(*, model: str, filename: str, failures: list[dict[str, Any]]) -> str:
    """压缩 OCR 多次失败详情，进入 parseQuality.messages 等待后续修复定位。"""
    if not failures:
        return f"Bailian OCR exhausted retries for {filename}: unknown error"
    details = "; ".join(
        f"第 {failure.get('attempt')}/{failure.get('maxAttempts')} 次 "
        f"{failure.get('errorType')}: {failure.get('errorMessage')}"
        for failure in failures
    )
    return f"Bailian OCR exhausted retries for {filename} using {model}: {details}"
