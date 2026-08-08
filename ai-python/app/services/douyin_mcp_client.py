"""通过 SocialDataX MCP 获取抖音视频语音转写。"""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import timedelta
import json
import os
import re
from functools import partial
from typing import Any, AsyncIterator, Callable, Iterable
from urllib.parse import urlsplit

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import Implementation

from app.core.environment import read_process_or_windows_user_environment
from app.services.remote_video_import import RemoteVideoPermanentError, RemoteVideoTransientError
from rag.core.models import ParsedBlockDocument


DEFAULT_DOUYIN_MCP_ENDPOINT = "https://mcp.52choujiang.com/douyin/mcp"
DOUYIN_MCP_ENDPOINT_HOST = "mcp.52choujiang.com"
DOUYIN_DETAIL_TOOL = "douyin_get_video_detail_by_url"
DOUYIN_SUBMIT_TRANSCRIPT_TOOL = "douyin_submit_video_speech_text_by_video_url"
DOUYIN_GET_TRANSCRIPT_JOB_TOOL = "douyin_get_video_speech_text_job"
COMPLETED_STATUSES = {"complete", "completed", "done", "finish", "finished", "ready", "success", "succeeded"}
FAILED_STATUSES = {"cancelled", "canceled", "error", "failed", "failure", "rejected"}
TIMESTAMP_PATTERN = re.compile(r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[,.]\d{1,3})?")


@dataclass(frozen=True)
class McpToolPayload:
    """保留 MCP 结构化结果和可选文本结果。"""

    data: Any
    text: str = ""


@dataclass(frozen=True)
class DouyinTranscript:
    """可直接交给现有文本解析器的抖音语音转写。"""

    title: str
    aweme_id: str | None
    source_url: str
    text: str
    parser: str = "socialdatax-douyin-mcp-speech-text"

    def filename(self, fallback_video_id: str) -> str:
        """优先使用作品 ID 生成稳定的转写资料文件名。"""
        token = safe_identifier(self.aweme_id) or safe_identifier(fallback_video_id) or "douyin-video"
        return f"{token}.srt"


@dataclass(frozen=True)
class TranscriptJobState:
    """统一不同 MCP 响应包装下的转写任务状态。"""

    status: str
    job_id: str | None
    title: str | None
    aweme_id: str | None
    transcript: str

    @property
    def completed(self) -> bool:
        """任务已返回正文或进入供应商完成状态。"""
        return bool(self.transcript.strip()) or self.status in COMPLETED_STATUSES

    @property
    def failed(self) -> bool:
        """任务已进入无需继续轮询的失败状态。"""
        return self.status in FAILED_STATUSES


class SocialDataXDouyinClient:
    """用官方 MCP SDK 管理抖音详情、提交转写和轮询会话。"""

    def __init__(
        self,
        *,
        endpoint: str | None = None,
        api_key: str | None = None,
        enabled: bool | None = None,
        connection_timeout_seconds: float | None = None,
        tool_timeout_seconds: float | None = None,
        poll_interval_seconds: float | None = None,
        max_wait_seconds: float | None = None,
    ) -> None:
        """读取固定 endpoint、密钥和受限超时配置。"""
        try:
            self.endpoint = validate_douyin_mcp_endpoint(
                endpoint or os.getenv("RAG_DOUYIN_MCP_ENDPOINT", DEFAULT_DOUYIN_MCP_ENDPOINT)
            )
        except ValueError as exc:
            raise RemoteVideoPermanentError(str(exc)) from exc
        self.api_key = (
            api_key
            if api_key is not None
            else read_process_or_windows_user_environment("SOCIALDATAX_API_KEY")
        ).strip()
        self.enabled = enabled if enabled is not None else bool_env("RAG_DOUYIN_MCP_ENABLED", True)
        self.connection_timeout_seconds = connection_timeout_seconds or bounded_float_env(
            "RAG_DOUYIN_MCP_CONNECTION_TIMEOUT_SECONDS", 30.0, minimum=1.0, maximum=120.0
        )
        self.tool_timeout_seconds = tool_timeout_seconds or bounded_float_env(
            "RAG_DOUYIN_MCP_TOOL_TIMEOUT_SECONDS", 60.0, minimum=5.0, maximum=180.0
        )
        self.poll_interval_seconds = poll_interval_seconds or bounded_float_env(
            "RAG_DOUYIN_TRANSCRIPT_POLL_INTERVAL_SECONDS", 5.0, minimum=0.1, maximum=60.0
        )
        self.max_wait_seconds = max_wait_seconds or bounded_float_env(
            "RAG_DOUYIN_TRANSCRIPT_MAX_WAIT_SECONDS", 900.0, minimum=30.0, maximum=3600.0
        )

    def transcribe_video(
        self,
        url: str,
        *,
        on_poll: Callable[[int, str], None] | None = None,
        cancel_check: Callable[[], None] | None = None,
    ) -> DouyinTranscript:
        """同步 worker 中建立一次 MCP 会话并等待转写终态。"""
        if not self.enabled:
            raise RemoteVideoPermanentError("抖音 MCP 接入已关闭")
        if not self.api_key:
            raise RemoteVideoPermanentError("未配置 SOCIALDATAX_API_KEY，暂时无法接入抖音视频")
        runner = partial(self._transcribe_video, url, on_poll=on_poll, cancel_check=cancel_check)
        try:
            return anyio.run(runner)
        except (RemoteVideoPermanentError, RemoteVideoTransientError):
            raise
        except BaseExceptionGroup as exc:
            control_exception = find_control_exception(exc)
            if control_exception is not None:
                raise control_exception
            raise classify_mcp_failure(exc) from exc
        except TimeoutError as exc:
            raise RemoteVideoTransientError("抖音视频转写超过等待时限") from exc
        except Exception as exc:
            control_exception = find_control_exception(exc)
            if control_exception is not None:
                raise control_exception
            raise classify_mcp_failure(exc) from exc

    async def _transcribe_video(
        self,
        url: str,
        *,
        on_poll: Callable[[int, str], None] | None,
        cancel_check: Callable[[], None] | None,
    ) -> DouyinTranscript:
        """在总等待时限内读取元数据、提交任务并轮询转写。"""
        with anyio.fail_after(self.max_wait_seconds):
            async with self._open_session() as session:
                run_cancel_check(cancel_check)
                detail_payload = await self._call_tool(session, DOUYIN_DETAIL_TOOL, {"url": url})
                title = extract_title(detail_payload)
                aweme_id = extract_aweme_id(detail_payload)

                run_cancel_check(cancel_check)
                submit_payload = await self._call_tool(
                    session,
                    DOUYIN_SUBMIT_TRANSCRIPT_TOOL,
                    {"url": url},
                )
                state = parse_transcript_job_state(submit_payload, source_url=url)
                title = state.title or title
                aweme_id = state.aweme_id or aweme_id
                poll_count = 0

                while not state.completed:
                    if state.failed:
                        raise RemoteVideoPermanentError("抖音视频语音转写失败或不包含可识别语音")
                    if not state.job_id:
                        raise RemoteVideoPermanentError("抖音 MCP 未返回可轮询的转写任务标识")
                    poll_count += 1
                    run_cancel_check(cancel_check)
                    if on_poll is not None:
                        on_poll(poll_count, state.status or "processing")
                    await anyio.sleep(self.poll_interval_seconds)
                    run_cancel_check(cancel_check)
                    job_payload = await self._call_tool(
                        session,
                        DOUYIN_GET_TRANSCRIPT_JOB_TOOL,
                        {"job_id": state.job_id},
                    )
                    state = parse_transcript_job_state(job_payload, source_url=url, fallback_job_id=state.job_id)
                    title = state.title or title
                    aweme_id = state.aweme_id or aweme_id

                if not state.transcript.strip():
                    raise RemoteVideoPermanentError("抖音视频没有可用于 RAG 的语音转写文本")
                return DouyinTranscript(
                    title=compact_title(title, aweme_id),
                    aweme_id=aweme_id,
                    source_url=url,
                    text=state.transcript.strip(),
                )

    @asynccontextmanager
    async def _open_session(self) -> AsyncIterator[ClientSession]:
        """只向固定 SocialDataX endpoint 发送 Bearer 密钥。"""
        timeout = httpx.Timeout(self.tool_timeout_seconds, connect=self.connection_timeout_seconds)
        headers = {"Authorization": f"Bearer {self.api_key}"}
        async with AsyncExitStack() as stack:
            try:
                http_client = await stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=headers,
                        timeout=timeout,
                        follow_redirects=False,
                        # MCP 密钥不应经过本机通用代理；固定官方 endpoint 直接走 HTTPS。
                        trust_env=False,
                    )
                )
                streams = await stack.enter_async_context(
                    streamable_http_client(self.endpoint, http_client=http_client)
                )
                read_stream, write_stream, _session_id = streams
                session = await stack.enter_async_context(
                    ClientSession(
                        read_stream,
                        write_stream,
                        read_timeout_seconds=timedelta(seconds=self.tool_timeout_seconds),
                        client_info=Implementation(name="learning-evidence-rag", version="1.0.0"),
                    )
                )
                await session.initialize()
            except (RemoteVideoPermanentError, RemoteVideoTransientError):
                raise
            except BaseExceptionGroup as exc:
                control_exception = find_control_exception(exc)
                if control_exception is not None:
                    raise control_exception
                raise classify_mcp_failure(exc) from exc
            except Exception as exc:
                raise classify_mcp_failure(exc) from exc
            # yield 位于异常转换范围之外，保证 worker 的失租异常原样向上传播。
            yield session

    async def _call_tool(self, session: ClientSession, name: str, arguments: dict[str, Any]) -> McpToolPayload:
        """调用单个工具并把第三方错误收敛为稳定中文业务错误。"""
        try:
            result = await session.call_tool(
                name,
                arguments,
                read_timeout_seconds=timedelta(seconds=self.tool_timeout_seconds),
            )
        except (RemoteVideoPermanentError, RemoteVideoTransientError):
            raise
        except BaseExceptionGroup as exc:
            control_exception = find_control_exception(exc)
            if control_exception is not None:
                raise control_exception
            raise classify_mcp_failure(exc) from exc
        except Exception as exc:
            raise classify_mcp_failure(exc) from exc
        payload = build_mcp_tool_payload(result)
        if bool(getattr(result, "isError", False)):
            raise classify_mcp_failure(payload.text or json.dumps(payload.data, ensure_ascii=False))
        return payload


def build_mcp_tool_payload(result: Any) -> McpToolPayload:
    """兼容 MCP structuredContent 与文本 JSON 两种公开返回方式。"""
    structured = getattr(result, "structuredContent", None)
    text_parts = [
        str(getattr(item, "text", "")).strip()
        for item in (getattr(result, "content", None) or [])
        if getattr(item, "type", None) == "text" and str(getattr(item, "text", "")).strip()
    ]
    joined_text = "\n".join(text_parts).strip()
    if structured not in (None, {}, []):
        return McpToolPayload(structured, joined_text)
    parsed_text = parse_json_text(joined_text)
    return McpToolPayload(parsed_text if parsed_text is not None else joined_text, joined_text)


def parse_transcript_job_state(
    payload: McpToolPayload,
    *,
    source_url: str,
    fallback_job_id: str | None = None,
) -> TranscriptJobState:
    """从嵌套响应中提取任务状态、标识和可索引转写。"""
    status = normalize_status(first_nested_value(payload.data, {"jobstatus", "state", "status", "taskstatus"}))
    job_id = clean_optional_text(
        first_nested_value(payload.data, {"jobid", "taskid", "transcriptionjobid"})
    ) or fallback_job_id
    title = extract_title(payload)
    aweme_id = extract_aweme_id(payload)
    transcript = extract_transcript_text(payload.data, payload.text, source_url=source_url)
    return TranscriptJobState(status=status, job_id=job_id, title=title, aweme_id=aweme_id, transcript=transcript)


def extract_transcript_text(data: Any, raw_text: str, *, source_url: str) -> str:
    """优先把带时间信息的分段转换为 SRT，否则保留原始转写正文。"""
    segments = find_transcript_segments(data)
    if segments:
        srt = segments_to_srt(segments)
        if srt:
            return srt

    value = first_nested_value(
        data,
        {
            "fulltext",
            "resulttext",
            "speechtext",
            "transcript",
            "transcripttext",
        },
    )
    transcript = normalize_transcript_value(value)
    if transcript:
        return transcript

    text_value = first_nested_value(data, {"text"})
    transcript = normalize_transcript_value(text_value)
    if transcript and not looks_like_job_message(transcript):
        return transcript

    raw = raw_text.strip()
    if raw and parse_json_text(raw) is None and not looks_like_job_message(raw):
        return raw
    return ""


def find_transcript_segments(value: Any) -> list[dict[str, Any]]:
    """递归寻找常见 segments/sentences/cues 数组。"""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = normalize_key(key)
            if normalized_key in {"cues", "segments", "sentences", "utterances"} and isinstance(child, list):
                candidates = [item for item in child if isinstance(item, dict)]
                if candidates:
                    return candidates
        for child in value.values():
            found = find_transcript_segments(child)
            if found:
                return found
    elif isinstance(value, list):
        candidates = [item for item in value if isinstance(item, dict)]
        if candidates and any(segment_text(item) for item in candidates):
            return candidates
        for child in value:
            found = find_transcript_segments(child)
            if found:
                return found
    return []


def segments_to_srt(segments: Iterable[dict[str, Any]]) -> str:
    """将供应商分段转换为现有字幕解析器可识别的 SRT。"""
    cues: list[str] = []
    previous_end = 0.0
    for segment in segments:
        text = segment_text(segment)
        if not text:
            continue
        start = segment_seconds(segment, ("start_ms", "startMs", "begin_ms", "beginMs"), ("start", "start_time", "startTime", "begin"))
        end = segment_seconds(segment, ("end_ms", "endMs", "finish_ms", "finishMs"), ("end", "end_time", "endTime", "finish"))
        if start is None:
            start = previous_end
        if end is None or end <= start:
            end = start + 1.0
        previous_end = end
        cues.append(f"{len(cues) + 1}\n{format_srt_time(start)} --> {format_srt_time(end)}\n{text}")
    return "\n\n".join(cues)


def annotate_douyin_transcript_result(
    parsed: ParsedBlockDocument,
    transcript: DouyinTranscript,
) -> ParsedBlockDocument:
    """为文本解析块补齐抖音视频 evidence 的来源与通道元数据。"""
    blocks = []
    for block in parsed.blocks:
        metadata = {
            **block.metadata,
            "mediaType": "video",
            "evidenceChannel": "subtitle",
            "sourcePlatform": "douyin",
            "sourceVideoUrl": transcript.source_url,
            "transcriptProvider": "SocialDataX",
        }
        if transcript.aweme_id:
            metadata["awemeId"] = transcript.aweme_id
        blocks.append(
            block.model_copy(
                update={
                    "fileType": "srt",
                    "parseEngine": transcript.parser,
                    "sourceTitle": transcript.title,
                    "sourcePath": transcript.source_url,
                    "metadata": metadata,
                }
            )
        )
    return replace(parsed, blocks=blocks, parser=transcript.parser)


def extract_title(payload: McpToolPayload) -> str | None:
    """提取作品标题或描述，并限制资料标题长度。"""
    value = first_nested_value(payload.data, {"awemedesc", "description", "desc", "title", "videotitle"})
    text = clean_optional_text(value)
    return " ".join(text.split())[:240] if text else None


def extract_aweme_id(payload: McpToolPayload) -> str | None:
    """只接受短小可展示的作品标识。"""
    value = clean_optional_text(first_nested_value(payload.data, {"awemeid", "itemid", "videoid"}))
    return safe_identifier(value) if value else None


def first_nested_value(value: Any, normalized_keys: set[str]) -> Any:
    """先查当前层键，再递归查子结构，保持供应商包装层兼容性。"""
    if isinstance(value, dict):
        for key, child in value.items():
            if normalize_key(key) in normalized_keys and child not in (None, "", [], {}):
                return child
        for child in value.values():
            found = first_nested_value(child, normalized_keys)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for child in value:
            found = first_nested_value(child, normalized_keys)
            if found not in (None, "", [], {}):
                return found
    return None


def segment_text(segment: dict[str, Any]) -> str:
    """从单条转写分段中提取可索引正文。"""
    value = first_nested_value(segment, {"content", "sentence", "speechtext", "text", "transcript"})
    return normalize_transcript_value(value)


def segment_seconds(
    segment: dict[str, Any],
    millisecond_keys: tuple[str, ...],
    second_keys: tuple[str, ...],
) -> float | None:
    """兼容毫秒、秒和时分秒格式的分段时间字段。"""
    for key in millisecond_keys:
        if key in segment:
            number = number_or_none(segment[key])
            return number / 1000.0 if number is not None else None
    for key in second_keys:
        if key not in segment:
            continue
        value = segment[key]
        if isinstance(value, str) and ":" in value:
            return timestamp_seconds(value)
        return number_or_none(value)
    return None


def format_srt_time(seconds: float) -> str:
    """把秒数格式化为 SRT 使用的时分秒毫秒。"""
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


def timestamp_seconds(value: str) -> float | None:
    """把 MM:SS 或 HH:MM:SS 文本转换为秒数。"""
    try:
        parts = value.strip().replace(",", ".").split(":")
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except (TypeError, ValueError):
        return None
    return None


def normalize_transcript_value(value: Any) -> str:
    """规范化字符串或字符串数组形式的转写正文。"""
    if isinstance(value, str):
        return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "\n".join(str(item).strip() for item in value if str(item).strip())
    return ""


def looks_like_job_message(value: str) -> bool:
    """识别只有任务状态而不包含真实转写的短消息。"""
    normalized = " ".join(value.lower().split())
    if TIMESTAMP_PATTERN.search(value):
        return False
    markers = ("job_id", "job id", "task_id", "任务已提交", "正在处理", "processing", "queued")
    return any(marker in normalized for marker in markers) and len(normalized) < 500


def parse_json_text(value: str) -> Any | None:
    """解析 TextContent 中的裸 JSON 或 Markdown JSON 代码块。"""
    text = value.strip()
    if not text:
        return None
    candidates = [text]
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        candidates.append("\n".join(lines[1:-1]).strip())
    for candidate in candidates:
        if candidate.lower().startswith("json\n"):
            candidate = candidate[5:].strip()
        try:
            return json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    return None


def normalize_status(value: Any) -> str:
    """把供应商状态规整为仅含小写字母的比较值。"""
    return re.sub(r"[^a-z]", "", str(value or "").strip().lower())


def normalize_key(value: Any) -> str:
    """规整第三方字段名，兼容蛇形、驼峰和连字符。"""
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def compact_title(title: str | None, aweme_id: str | None) -> str:
    """生成长度受限且始终非空的资料标题。"""
    fallback = f"抖音视频 {aweme_id}" if aweme_id else "抖音视频语音转写"
    text = " ".join(str(title or fallback).split()).strip()
    return (text or fallback)[:240]


def safe_identifier(value: Any) -> str | None:
    """只保留可安全用于文件名的短标识。"""
    text = str(value or "").strip()
    if not text or len(text) > 80 or not re.fullmatch(r"[A-Za-z0-9_-]+", text):
        return None
    return text


def clean_optional_text(value: Any) -> str | None:
    """把第三方标量值转换为可选非空文本。"""
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    return text or None


def number_or_none(value: Any) -> float | None:
    """把第三方数值转换为非负浮点数。"""
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return None


def run_cancel_check(callback: Callable[[], None] | None) -> None:
    """在外部调用和轮询边界检查 worker 是否仍持有租约。"""
    if callback is not None:
        callback()


def classify_mcp_failure(error: BaseException | str) -> RemoteVideoPermanentError | RemoteVideoTransientError:
    """不回显供应商正文，只按认证、限流和网络类别输出稳定错误。"""
    message = exception_message_text(error).lower()
    if any(marker in message for marker in ("401", "403", "api key", "apikey", "bearer", "unauthorized", "forbidden", "鉴权")):
        return RemoteVideoPermanentError("抖音 MCP 鉴权失败，请检查 SOCIALDATAX_API_KEY")
    if any(marker in message for marker in ("404", "not found", "private", "deleted", "无权限", "不存在", "已删除")):
        return RemoteVideoPermanentError("抖音视频无法公开访问或已经失效")
    if any(marker in message for marker in ("no speech", "no audio", "unsupported", "没有语音", "不支持")):
        return RemoteVideoPermanentError("抖音视频不包含可识别语音或暂不支持转写")
    if any(
        marker in message
        for marker in (
            "408",
            "429",
            "500",
            "502",
            "503",
            "504",
            "connection",
            "rate limit",
            "timeout",
            "timed out",
            "too many requests",
            "网络",
            "限流",
            "超时",
        )
    ):
        return RemoteVideoTransientError("抖音转写服务暂时不可用，请稍后重试")
    return RemoteVideoTransientError("抖音 MCP 调用暂时失败，请稍后重试")


def exception_message_text(error: BaseException | str) -> str:
    """展开 AnyIO ExceptionGroup 的叶子异常，供错误分类使用，不直接对外回显。"""
    if isinstance(error, BaseExceptionGroup):
        return " ".join(exception_message_text(child) for child in error.exceptions)
    return str(error)


def find_control_exception(error: BaseException) -> BaseException | None:
    """保留 worker 失租等控制异常，避免远程客户端把任务错误改写成普通重试。"""
    if isinstance(error, BaseExceptionGroup):
        for child in error.exceptions:
            found = find_control_exception(child)
            if found is not None:
                return found
        return None
    if isinstance(error, (RemoteVideoPermanentError, RemoteVideoTransientError)):
        return error
    if error.__class__.__name__ in {"IndexExecutionLostError", "RetryNotReady"}:
        return error
    return None


def validate_douyin_mcp_endpoint(value: str) -> str:
    """密钥只允许发送到固定 HTTPS endpoint，防止配置误改造成泄露。"""
    try:
        parsed = urlsplit(str(value or "").strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("抖音 MCP endpoint 配置不合法") from exc
    if (
        parsed.scheme.lower() != "https"
        or (parsed.hostname or "").lower() != DOUYIN_MCP_ENDPOINT_HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path.rstrip("/") != "/douyin/mcp"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("抖音 MCP endpoint 仅允许使用 SocialDataX 官方 HTTPS 地址")
    return DEFAULT_DOUYIN_MCP_ENDPOINT


def bool_env(name: str, default: bool) -> bool:
    """读取布尔环境变量，空值回退到默认值。"""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def bounded_float_env(name: str, default: float, *, minimum: float, maximum: float) -> float:
    """读取浮点环境变量并限制到允许区间。"""
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))
