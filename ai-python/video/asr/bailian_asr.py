from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any, Callable

from rag.model_logging import log_model_call


DEFAULT_ASR_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_ASR_TASK_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
DEFAULT_ASR_MODEL = "qwen3-asr-flash"
DEFAULT_ASR_FILETRANS_MODEL = "qwen3-asr-flash-filetrans"
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
        task_base_url: str | None = None,
        model: str | None = None,
        filetrans_model: str | None = None,
        provider: str | None = None,
        timeout_seconds: float | None = None,
        max_audio_bytes: int | None = None,
        max_polls: int | None = None,
        poll_interval_seconds: float | None = None,
        filetrans_max_attempts: int | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.base_url = (base_url or os.getenv("RAG_ASR_BASE_URL") or DEFAULT_ASR_BASE_URL).rstrip("/")
        self.task_base_url = (task_base_url or os.getenv("RAG_ASR_TASK_BASE_URL") or DEFAULT_ASR_TASK_BASE_URL).rstrip("/")
        self.model = model or os.getenv("RAG_ASR_MODEL") or DEFAULT_ASR_MODEL
        self.filetrans_model = (
            filetrans_model
            or os.getenv("RAG_ASR_FILETRANS_MODEL")
            or DEFAULT_ASR_FILETRANS_MODEL
        )
        self.provider = (provider or os.getenv("RAG_ASR_PROVIDER") or "auto").strip().lower()
        self.timeout_seconds = timeout_seconds or float(os.getenv("RAG_ASR_TIMEOUT_SECONDS", "120"))
        self.max_audio_bytes = max_audio_bytes or int(os.getenv("RAG_ASR_MAX_AUDIO_BYTES", str(10 * 1024 * 1024)))
        self.max_polls = max_polls or int(os.getenv("RAG_ASR_FILETRANS_MAX_POLLS", "30"))
        self.poll_interval_seconds = poll_interval_seconds if poll_interval_seconds is not None else float(
            os.getenv("RAG_ASR_FILETRANS_POLL_INTERVAL_SECONDS", "2")
        )
        self.filetrans_max_attempts = filetrans_max_attempts or int(os.getenv("RAG_ASR_FILETRANS_MAX_ATTEMPTS", "2"))

    @property
    def should_call_dashscope(self) -> bool:
        if self.provider == "local":
            return False
        if self.provider in {"dashscope", "filetrans", "dashscope_filetrans"}:
            return True
        return bool(self.api_key)

    def transcribe_audio_file(
        self,
        audio_path: Path,
        source_url: str | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[str, list[str]]:
        if not self.should_call_dashscope:
            return "", ["百炼 ASR 未启用，跳过音频转写"]
        if not self.api_key:
            return "", ["DASHSCOPE_API_KEY 未配置，无法调用百炼 ASR"]

        warnings: list[str] = []
        if self.should_call_filetrans(source_url):
            try:
                return self._call_filetrans(str(source_url), progress_callback=progress_callback), []
            except Exception as exc:
                warnings.append(f"百炼 ASR 异步时间戳转写失败，降级同步识别: {exc}")

        if not audio_path.exists() or audio_path.stat().st_size == 0:
            return "", [*warnings, "抽取的音频文件为空，无法调用百炼 ASR"]

        if audio_path.stat().st_size > self.max_audio_bytes:
            warnings.append(f"抽取的音频超过 {self.max_audio_bytes} 字节，当前同步 ASR 已跳过")
            return "", warnings
        try:
            transcript = self._call_chat_audio(audio_path)
            return transcript, warnings
        except Exception as exc:
            warnings.append(f"百炼 ASR 调用失败: {exc}")
            return "", warnings

    def transcribe_source_url(
        self,
        source_url: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> tuple[str, list[str]]:
        """对公开视频 URL 直接发起百炼异步文件转写。"""
        if not self.should_call_dashscope:
            return "", ["百炼 ASR 未启用，跳过音频转写"]
        if not self.api_key:
            return "", ["DASHSCOPE_API_KEY 未配置，无法调用百炼 ASR"]
        if not self.should_call_filetrans(source_url):
            return "", ["当前配置未启用公开视频 URL 异步转写"]
        try:
            return self._call_filetrans(source_url, progress_callback=progress_callback), []
        except Exception as exc:
            return "", [f"百炼 ASR 异步时间戳转写失败: {exc}"]

    def should_call_filetrans(self, source_url: str | None) -> bool:
        """公开视频 URL 可用时优先使用带时间戳的异步转写模型。"""
        enabled = (os.getenv("RAG_ASR_FILETRANS_ENABLED") or "auto").strip().lower()
        if self.provider in {"filetrans", "dashscope_filetrans"}:
            return bool(source_url and source_url.startswith(("http://", "https://")))
        if enabled in {"false", "0", "no", "off"}:
            return False
        if enabled in {"true", "1", "yes", "on"}:
            return bool(source_url and source_url.startswith(("http://", "https://")))
        return bool(source_url and source_url.startswith(("http://", "https://")))

    def _call_filetrans(
        self,
        file_url: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("使用百炼 ASR 异步转写需要安装 httpx 依赖") from exc

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        payload = {
            "model": self.filetrans_model,
            "input": {"file_url": file_url},
            "parameters": {
                "channel_id": [0],
                "enable_itn": False,
                "enable_words": os.getenv("RAG_ASR_ENABLE_WORDS", "false").lower() in {"true", "1", "yes", "on"},
            },
        }
        with log_model_call(
            stage="parse.video.asr",
            action="bailian_filetrans_asr",
            model_name=self.filetrans_model,
            event="视频异步 ASR 转写",
            extra_context={"sourceType": "url"},
            recoverable=True,
            fallback_message=f"使用 {self.filetrans_model} 模型完成视频异步 ASR 转写事件失败，已降级到同步 ASR、字幕或视频元数据继续处理",
        ):
            errors: list[str] = []
            max_attempts = max(1, self.filetrans_max_attempts)
            with httpx.Client(timeout=self.timeout_seconds) as client:
                for attempt in range(1, max_attempts + 1):
                    notify_progress(progress_callback, phase="submit", attempt=attempt, maxAttempts=max_attempts)
                    try:
                        response = client.post(f"{self.task_base_url}/services/audio/asr/transcription", headers=headers, json=payload)
                        if response.status_code >= 400:
                            raise RuntimeError(f"HTTP {response.status_code} {response.text[:500]}")
                        task_id = ((response.json().get("output") or {}).get("task_id"))
                        if not task_id:
                            raise RuntimeError("百炼 ASR 异步任务未返回 task_id")
                        notify_progress(progress_callback, phase="submitted", attempt=attempt, maxAttempts=max_attempts, taskId=task_id)
                        transcription_url = self._wait_filetrans_result(
                            client,
                            task_id,
                            headers,
                            progress_callback=progress_callback,
                            attempt=attempt,
                            max_attempts=max_attempts,
                        )
                        notify_progress(progress_callback, phase="download", attempt=attempt, maxAttempts=max_attempts, taskId=task_id)
                        result_response = client.get(transcription_url)
                        if result_response.status_code >= 400:
                            raise RuntimeError(f"下载 ASR 转写结果失败: HTTP {result_response.status_code}")
                        return transcription_json_to_srt(result_response.json())
                    except Exception as exc:
                        errors.append(str(exc))
                        notify_progress(
                            progress_callback,
                            phase="retry" if attempt < max_attempts else "failed",
                            attempt=attempt,
                            maxAttempts=max_attempts,
                            errorMessage=str(exc),
                            nextAttempt=attempt + 1 if attempt < max_attempts else None,
                        )
                        if attempt >= max_attempts:
                            break
                        time.sleep(max(0.0, self.poll_interval_seconds))
                raise RuntimeError("；".join(errors) if errors else "百炼 ASR 异步任务失败")

    def _wait_filetrans_result(
        self,
        client: Any,
        task_id: str,
        headers: dict[str, str],
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        attempt: int = 1,
        max_attempts: int = 1,
    ) -> str:
        max_polls = max(1, self.max_polls)
        for poll_index in range(1, max_polls + 1):
            response = client.get(f"{self.task_base_url}/tasks/{task_id}", headers=headers)
            if response.status_code >= 400:
                raise RuntimeError(f"查询 ASR 任务失败: HTTP {response.status_code} {response.text[:500]}")
            output = response.json().get("output") or {}
            status = output.get("task_status")
            notify_progress(
                progress_callback,
                phase="poll",
                attempt=attempt,
                maxAttempts=max_attempts,
                pollIndex=poll_index,
                maxPolls=max_polls,
                taskStatus=status,
                taskId=task_id,
            )
            if status == "SUCCEEDED":
                transcription_url = extract_transcription_url(output)
                if not transcription_url:
                    raise RuntimeError("百炼 ASR 任务成功但未返回 transcription_url")
                return transcription_url
            if status == "FAILED":
                raise RuntimeError(output.get("message") or output.get("code") or "百炼 ASR 任务失败")
            time.sleep(max(0.0, self.poll_interval_seconds))
        raise RuntimeError("等待百炼 ASR 异步任务超时")

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
        with log_model_call(
            stage="parse.video.asr",
            action="bailian_sync_asr",
            model_name=self.model,
            event="视频音频同步 ASR 转写",
            extra_context={"audioFilename": audio_path.name, "audioBytes": audio_path.stat().st_size},
            recoverable=True,
            fallback_message=f"使用 {self.model} 模型完成视频音频同步 ASR 转写事件失败，已降级到字幕、关键帧 OCR 或视频元数据继续处理",
        ):
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


def extract_transcription_url(output: dict[str, Any]) -> str | None:
    result = output.get("result")
    if isinstance(result, dict) and isinstance(result.get("transcription_url"), str):
        return result["transcription_url"]
    results = output.get("results")
    if isinstance(results, list):
        for item in results:
            if isinstance(item, dict) and isinstance(item.get("transcription_url"), str):
                return item["transcription_url"]
    return None


def transcription_json_to_srt(data: dict[str, Any]) -> str:
    lines: list[str] = []
    cue_index = 1
    for transcript in data.get("transcripts") or []:
        if not isinstance(transcript, dict):
            continue
        for sentence in transcript.get("sentences") or []:
            if not isinstance(sentence, dict):
                continue
            text = str(sentence.get("text") or "").strip()
            if not text:
                continue
            begin_time = int(sentence.get("begin_time") or 0)
            end_time = int(sentence.get("end_time") or begin_time)
            lines.extend(
                [
                    str(cue_index),
                    f"{milliseconds_to_srt_timestamp(begin_time)} --> {milliseconds_to_srt_timestamp(end_time)}",
                    text,
                    "",
                ]
            )
            cue_index += 1
    if not lines:
        raise RuntimeError("百炼 ASR 转写结果中没有可用句级时间戳")
    return "\n".join(lines).strip()


def milliseconds_to_srt_timestamp(value: int) -> str:
    safe_value = max(0, value)
    hours = safe_value // 3_600_000
    minutes = (safe_value % 3_600_000) // 60_000
    seconds = (safe_value % 60_000) // 1000
    milliseconds = safe_value % 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def notify_progress(callback: Callable[[dict[str, Any]], None] | None, **event: Any) -> None:
    """向调用方报告 ASR 异步任务状态，回调失败不影响主流程。"""
    if callback is None:
        return
    try:
        callback(event)
    except Exception:
        return
