from __future__ import annotations

import base64
import os
from pathlib import Path
from typing import Any


DEFAULT_ASR_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_ASR_MODEL = "qwen3-asr-flash"
DEFAULT_ASR_PROMPT = (
    "请将音频转写为 SRT 字幕格式，只输出字幕内容。"
    "每段必须包含序号、HH:MM:SS,mmm --> HH:MM:SS,mmm 时间范围和中文转写文本。"
)


class BailianAsrClient:
    """百炼 ASR 客户端，用于把视频音频轨转成带时间戳字幕。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        timeout_seconds: float | None = None,
        max_audio_bytes: int | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.base_url = (base_url or os.getenv("RAG_ASR_BASE_URL") or DEFAULT_ASR_BASE_URL).rstrip("/")
        self.model = model or os.getenv("RAG_ASR_MODEL") or DEFAULT_ASR_MODEL
        self.provider = (provider or os.getenv("RAG_ASR_PROVIDER") or "auto").strip().lower()
        self.timeout_seconds = timeout_seconds or float(os.getenv("RAG_ASR_TIMEOUT_SECONDS", "120"))
        self.max_audio_bytes = max_audio_bytes or int(os.getenv("RAG_ASR_MAX_AUDIO_BYTES", str(10 * 1024 * 1024)))

    @property
    def should_call_dashscope(self) -> bool:
        if self.provider == "local":
            return False
        if self.provider == "dashscope":
            return True
        return bool(self.api_key)

    def transcribe_audio_file(self, audio_path: Path) -> tuple[str, list[str]]:
        if not self.should_call_dashscope:
            return "", ["百炼 ASR 未启用，跳过音频转写"]
        if not self.api_key:
            return "", ["DASHSCOPE_API_KEY 未配置，无法调用百炼 ASR"]
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            return "", ["抽取的音频文件为空，无法调用百炼 ASR"]
        if audio_path.stat().st_size > self.max_audio_bytes:
            return "", [f"抽取的音频超过 {self.max_audio_bytes} 字节，当前同步 ASR 已跳过"]
        try:
            return self._call_chat_audio(audio_path), []
        except Exception as exc:
            return "", [f"百炼 ASR 调用失败: {exc}"]

    def _call_chat_audio(self, audio_path: Path) -> str:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("使用百炼 ASR 需要安装 httpx 依赖") from exc

        audio_base64 = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": audio_base64,
                                "format": audio_path.suffix.lstrip(".") or "wav",
                            },
                        },
                        {"type": "text", "text": DEFAULT_ASR_PROMPT},
                    ],
                }
            ],
            "temperature": 0,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        text = extract_message_content(data).strip()
        if not text:
            raise RuntimeError("百炼 ASR 返回空转写")
        return text


def extract_message_content(data: dict[str, Any]) -> str:
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
