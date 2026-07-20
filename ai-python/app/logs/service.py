"""保持 Java 日志语义的 Python 业务服务。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime
import hashlib
import json
import os
import re
import uuid
from numbers import Number
from typing import Any, Final

from app.core.result import BusinessError
from app.logs.repository import LogErrorRecord, LogEventRecord, LogRepository, LogRepositoryProtocol, LogTransaction
from app.schemas.logs import (
    LogErrorCreateRequest,
    LogErrorResponse,
    LogEventCreateRequest,
    LogEventResponse,
    LogOverviewResponse,
)


DEFAULT_USER_ID: Final[str] = "anonymous"
UUID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
LARGE_NUMBER_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b\d{4,}\b")
SENSITIVE_KEY_PARTS: Final[tuple[str, ...]] = (
    "password",
    "token",
    "authorization",
    "cookie",
    "secret",
    "apikey",
    "api_key",
    "dashscope",
)
SENSITIVE_EXACT_KEYS: Final[frozenset[str]] = frozenset({"content", "question", "answer", "resume", "jd"})


class LogBusinessError(BusinessError):
    """日志领域可安全返回给调用方的业务错误。"""


class LogService:
    """写入、查询、聚合日志，并同步 RAG 资料解析状态。"""

    def __init__(
        self,
        repository: LogRepositoryProtocol | None = None,
        clock: Callable[[], datetime] | None = None,
        enabled: bool | None = None,
        internal_token: str | None = None,
        max_batch_size: int | None = None,
        max_context_bytes: int | None = None,
        max_stack_trace_bytes: int | None = None,
    ) -> None:
        self._repository = repository or LogRepository()
        self._clock = clock or (lambda: datetime.now().astimezone())
        self._enabled_override = enabled
        self._internal_token_override = internal_token
        self._max_batch_size_override = max_batch_size
        self._max_context_bytes_override = max_context_bytes
        self._max_stack_trace_bytes_override = max_stack_trace_bytes

    def record_event(self, request: LogEventCreateRequest) -> int | None:
        """写入一条业务事件，并在需要时同步资料解析状态。"""
        self._validate_event(request)
        if not self.enabled:
            return None
        context = normalize_context(request.context)
        enriched = enrich_event_request(request, context)
        event = LogEventRecord(
            id=None,
            trace_id=default_text(enriched.traceId, new_trace_id()),
            session_id=enriched.sessionId,
            user_id=default_text(enriched.userId, DEFAULT_USER_ID),
            source=default_text(enriched.source, "java"),
            domain=default_text(enriched.domain, "system"),
            level=default_text(enriched.level, "INFO").upper(),
            module=truncate(enriched.module, 80) or "",
            stage=truncate(enriched.stage, 80),
            event_type=default_text(enriched.eventType, "business_state"),
            action=truncate(enriched.action, 120) or "",
            message=truncate(enriched.message, 500),
            route=truncate(enriched.route, 255),
            http_method=truncate(enriched.httpMethod, 20),
            request_path=truncate(enriched.requestPath, 500),
            status_code=enriched.statusCode,
            success=True if enriched.success is None else bool(enriched.success),
            duration_ms=enriched.durationMs,
            material_id=enriched.materialId,
            document_id=truncate(enriched.documentId, 120),
            parser=truncate(enriched.parser, 80),
            client_time=to_offset_datetime(enriched.clientTime, self._clock()),
            server_time=self._clock(),
            context_json=to_context_json(context, self.max_context_bytes),
        )
        with self._repository.transaction() as transaction:
            event_id = transaction.insert_event(event)
            persisted = replace(event, id=event_id)
            sync_material_status_from_rag_progress(transaction, persisted, context)
        return event_id

    def record_events(self, requests: list[LogEventCreateRequest] | None) -> int:
        """按配置上限逐条写入事件，保持 Java 批量接口返回条数语义。"""
        if not requests:
            return 0
        count = 0
        for request in requests[: self.max_batch_size]:
            self.record_event(request)
            count += 1
        return count

    def record_internal_event(self, token: str | None, request: LogEventCreateRequest) -> int | None:
        """校验内部令牌并把 Java 或缺失来源统一标记为 Python。"""
        self.require_internal_token(token)
        source = request.source
        if is_blank(source) or source == "java":
            request = request.model_copy(update={"source": "python"})
        return self.record_event(request)

    def record_error(self, request: LogErrorCreateRequest) -> int | None:
        """写入错误日志，已存在相同指纹时只累计出现次数。"""
        self._validate_error(request)
        if not self.enabled:
            return None
        context = normalize_context(request.context)
        enriched = enrich_error_request(request, context)
        stack_trace = truncate(default_text(enriched.stackTrace, ""), self.max_stack_trace_bytes) or ""
        fingerprint = default_text(enriched.fingerprint, build_fingerprint(enriched, stack_trace))
        now = self._clock()
        error = LogErrorRecord(
            id=None,
            trace_id=default_text(enriched.traceId, new_trace_id()),
            session_id=enriched.sessionId,
            user_id=default_text(enriched.userId, DEFAULT_USER_ID),
            source=default_text(enriched.source, "java"),
            domain=default_text(enriched.domain, "system"),
            severity=default_text(enriched.severity, "ERROR").upper(),
            module=truncate(enriched.module, 80) or "",
            stage=truncate(enriched.stage, 80),
            action=truncate(enriched.action, 120),
            error_type=truncate(enriched.errorType, 120) or "",
            error_code=truncate(enriched.errorCode, 120),
            message=truncate(enriched.message, 1000) or "",
            stack_trace=stack_trace,
            fingerprint=fingerprint,
            route=truncate(enriched.route, 255),
            http_method=truncate(enriched.httpMethod, 20),
            request_path=truncate(enriched.requestPath, 500),
            status_code=enriched.statusCode,
            duration_ms=enriched.durationMs,
            material_id=enriched.materialId,
            document_id=truncate(enriched.documentId, 120),
            parser=truncate(enriched.parser, 80),
            client_time=to_offset_datetime(enriched.clientTime, now),
            server_time=now,
            context_json=to_context_json(context, self.max_context_bytes),
        )
        with self._repository.transaction() as transaction:
            existing_id = transaction.find_error_id_by_fingerprint(fingerprint)
            if existing_id is not None:
                transaction.increase_error_occurrence(fingerprint, now)
                return existing_id
            return transaction.insert_error(error)

    def record_internal_error(self, token: str | None, request: LogErrorCreateRequest) -> int | None:
        """校验内部令牌并写入 Python 内部错误。"""
        self.require_internal_token(token)
        source = request.source
        if is_blank(source) or source == "java":
            request = request.model_copy(update={"source": "python"})
        return self.record_error(request)

    def list_recent_events(self, limit: int | str | None = None) -> list[LogEventResponse]:
        """查询最近事件，条数限制保持 Java 的 1..200。"""
        normalized_limit = safe_limit(limit)
        with self._repository.transaction() as transaction:
            return [to_event_response(record) for record in transaction.list_recent_events(normalized_limit)]

    def list_recent_errors(self, limit: int | str | None = None) -> list[LogErrorResponse]:
        """查询最近错误聚合，条数限制保持 Java 的 1..200。"""
        normalized_limit = safe_limit(limit)
        with self._repository.transaction() as transaction:
            return [to_error_response(record) for record in transaction.list_recent_errors(normalized_limit)]

    def overview(self, days: int | str | None = None) -> LogOverviewResponse:
        """统计指定天数内的事件和错误概览。"""
        normalized_days = safe_days(days)
        start_at = self._clock() - timedelta_days(normalized_days)
        with self._repository.transaction() as transaction:
            return LogOverviewResponse(
                eventCount=transaction.count_events_since(start_at),
                errorCount=transaction.count_errors_since(start_at),
                openErrorCount=transaction.count_open_errors_since(start_at),
                frontendErrorCount=transaction.count_errors_by_source_since("frontend", start_at),
                javaErrorCount=transaction.count_errors_by_source_since("java", start_at),
                pythonErrorCount=transaction.count_errors_by_source_since("python", start_at),
            )

    def require_internal_token(self, token: str | None) -> None:
        """仅在配置内部令牌后执行严格相等校验。"""
        configured = self.internal_token
        if configured and configured != (token or ""):
            raise LogBusinessError("内部日志令牌无效")

    @property
    def enabled(self) -> bool:
        """读取日志写入总开关。"""
        if self._enabled_override is not None:
            return self._enabled_override
        return read_bool_env("EVIDENCE_LOGS_ENABLED", True)

    @property
    def internal_token(self) -> str:
        """读取可选内部日志令牌。"""
        if self._internal_token_override is not None:
            return self._internal_token_override.strip()
        return os.getenv("EVIDENCE_INTERNAL_LOG_TOKEN", "").strip()

    @property
    def max_batch_size(self) -> int:
        """读取批量写入上限。"""
        return safe_positive(self._max_batch_size_override, read_positive_env("EVIDENCE_LOGS_MAX_BATCH_SIZE", 50))

    @property
    def max_context_bytes(self) -> int:
        """读取上下文 JSON 字符上限，名称沿用 Java 配置。"""
        return safe_positive(self._max_context_bytes_override, read_positive_env("EVIDENCE_LOGS_MAX_CONTEXT_BYTES", 20480))

    @property
    def max_stack_trace_bytes(self) -> int:
        """读取堆栈字符上限，名称沿用 Java 配置。"""
        return safe_positive(self._max_stack_trace_bytes_override, read_positive_env("EVIDENCE_LOGS_MAX_STACK_TRACE_BYTES", 20480))

    @staticmethod
    def _validate_event(request: LogEventCreateRequest) -> None:
        """执行 Java DTO 的必填字段校验。"""
        if is_blank(request.module):
            raise LogBusinessError("模块不能为空")
        if is_blank(request.action):
            raise LogBusinessError("动作不能为空")

    @staticmethod
    def _validate_error(request: LogErrorCreateRequest) -> None:
        """执行 Java 错误 DTO 的必填字段校验。"""
        if is_blank(request.module):
            raise LogBusinessError("模块不能为空")
        if is_blank(request.errorType):
            raise LogBusinessError("错误类型不能为空")
        if is_blank(request.message):
            raise LogBusinessError("错误消息不能为空")


def enrich_event_request(request: LogEventCreateRequest, context: dict[str, Any]) -> LogEventCreateRequest:
    """用 RAG 上下文中的资料标识补齐事件字段。"""
    updates = rag_identifier_updates(context)
    return request.model_copy(update=updates) if updates else request


def enrich_error_request(request: LogErrorCreateRequest, context: dict[str, Any]) -> LogErrorCreateRequest:
    """用 RAG 上下文中的资料标识补齐错误字段。"""
    updates = rag_identifier_updates(context)
    return request.model_copy(update=updates) if updates else request


def rag_identifier_updates(context: Mapping[str, Any]) -> dict[str, object]:
    """提取 materialId、documentId、parser，兼容 Java 的上下文优先规则。"""
    updates: dict[str, object] = {}
    material_id = context.get("materialId")
    if isinstance(material_id, Number) and not isinstance(material_id, bool):
        updates["materialId"] = int(material_id)
    if context.get("documentId") is not None:
        updates["documentId"] = str(context["documentId"])
    if context.get("parser") is not None:
        updates["parser"] = str(context["parser"])
    return updates


def sync_material_status_from_rag_progress(
    transaction: LogTransaction,
    event: LogEventRecord,
    context: Mapping[str, Any],
) -> None:
    """依据用户可见 RAG 进度同步 `learning_material` 主状态。"""
    if event.domain != "rag" or event.event_type != "rag_progress" or event.material_id is None:
        return
    stage = default_text(event.stage, text_value(context, "stageCode"))
    progress_status = normalize_status(text_value(context, "status"))
    parser = default_text(event.parser, text_value(context, "parser"))
    if stage == "index.completed" and context.get("stagingDocumentId") is not None and context.get("promoteConfirmed") is None:
        transaction.update_material_progress(event.material_id, "PARSING", parser, None)
        return
    if stage == "index.completed":
        parse_status = normalize_status(text_value(context, "parseStatus"))
        final_status = parse_status if parse_status in {"READY", "PARTIAL"} else "READY"
        transaction.update_material_progress(event.material_id, final_status, parser, completed_chunk_count(context))
        return
    if stage == "index.failed" or progress_status == "FAILED":
        transaction.update_material_progress(event.material_id, "FAILED", parser, integer_value(context, "chunkCount"))
        return
    if progress_status == "RUNNING" or has_unfinished_progress(context):
        transaction.update_material_progress(event.material_id, "PARSING", parser, None)


def to_context_json(context: Mapping[str, Any], max_length: int) -> str:
    """递归脱敏上下文并限制最终 JSON 文本长度。"""
    try:
        safe_context = sanitize_map(context, 0)
        return truncate(json.dumps(safe_context, ensure_ascii=False, separators=(",", ":")), max_length) or "{}"
    except (TypeError, ValueError):
        return '{"serializationError":true}'


def sanitize_map(source: Mapping[object, object], depth: int) -> dict[str, object]:
    """递归清理嵌套上下文，最大深度与 Java 服务一致。"""
    if depth > 4:
        return {"truncatedDepth": True}
    result: dict[str, object] = {}
    for key, value in source.items():
        safe_key = str(key)
        result[safe_key] = sanitize_value(safe_key, value, depth + 1)
    return result


def sanitize_value(key: str, value: object, depth: int) -> object:
    """对单个上下文值执行敏感字段遮罩、列表和文本限制。"""
    if value is None:
        return None
    if is_sensitive_key(key):
        return "***"
    if isinstance(value, Mapping):
        return sanitize_map(value, depth)
    if isinstance(value, list):
        return [sanitize_value(key, item, depth + 1) for item in value[:50]]
    if isinstance(value, tuple):
        return [sanitize_value(key, item, depth + 1) for item in value[:50]]
    if isinstance(value, str):
        return truncate(value, 500)
    return value


def is_sensitive_key(key: str) -> bool:
    """判断键名是否包含敏感业务内容或密钥。"""
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS) or lowered in SENSITIVE_EXACT_KEYS


def build_fingerprint(request: LogErrorCreateRequest, stack_trace: str) -> str:
    """按 Java 规则生成可聚合同类错误的 SHA-256 指纹。"""
    raw = "|".join(
        (
            default_text(request.source, "java"),
            default_text(request.domain, "system"),
            default_text(request.module, "unknown"),
            default_text(request.errorType, "UnknownError"),
            default_text(request.errorCode, "UNKNOWN"),
            normalize_variable_parts(default_text(request.message, "")),
            normalize_variable_parts(top_stack_frame(stack_trace)),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def top_stack_frame(stack_trace: str) -> str:
    """选取首个业务相关或首个通用堆栈帧。"""
    if not stack_trace.strip():
        return ""
    lines = stack_trace.splitlines()
    stripped = [line.strip() for line in lines]
    for line in stripped:
        if line.startswith("at com.itxiang"):
            return line
    for line in stripped:
        if line.startswith("at "):
            return line
    return stripped[0] if stripped else ""


def normalize_variable_parts(value: str) -> str:
    """将 UUID 和四位以上数字归一化，避免相同错误被拆分。"""
    return LARGE_NUMBER_PATTERN.sub("{num}", UUID_PATTERN.sub("{uuid}", value))


def to_event_response(record: LogEventRecord) -> LogEventResponse:
    """转换前端展示所需事件字段。"""
    return LogEventResponse(
        id=record.id or 0,
        traceId=record.trace_id,
        source=record.source,
        domain=record.domain,
        level=record.level,
        module=record.module,
        stage=record.stage,
        eventType=record.event_type,
        action=record.action,
        message=record.message,
        success=record.success,
        durationMs=record.duration_ms,
        materialId=record.material_id,
        documentId=record.document_id,
        parser=record.parser,
        contextJson=record.context_json,
        createdAt=to_local_datetime(record.created_at),
    )


def to_error_response(record: LogErrorRecord) -> LogErrorResponse:
    """转换前端展示所需错误聚合字段。"""
    return LogErrorResponse(
        id=record.id or 0,
        traceId=record.trace_id,
        source=record.source,
        domain=record.domain,
        severity=record.severity,
        module=record.module,
        stage=record.stage,
        action=record.action,
        errorType=record.error_type,
        errorCode=record.error_code,
        message=record.message,
        fingerprint=record.fingerprint,
        statusCode=record.status_code,
        durationMs=record.duration_ms,
        materialId=record.material_id,
        documentId=record.document_id,
        parser=record.parser,
        contextJson=record.context_json,
        occurrenceCount=record.occurrence_count,
        status=record.status,
        firstSeenAt=to_local_datetime(record.first_seen_at),
        lastSeenAt=to_local_datetime(record.last_seen_at),
        createdAt=to_local_datetime(record.created_at),
    )


def normalize_context(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """将空上下文归一化为空对象，拒绝非对象结构。"""
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise LogBusinessError("日志上下文必须是对象")
    return dict(value)


def new_trace_id() -> str:
    """生成与 Java 格式一致的本地追踪 ID。"""
    return "tr_" + uuid.uuid4().hex


def to_offset_datetime(value: datetime | None, fallback: datetime) -> datetime | None:
    """将客户端本地时间转换为带时区时间以匹配 TIMESTAMPTZ 列。"""
    if value is None:
        return None
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=fallback.tzinfo)


def to_local_datetime(value: datetime | None) -> datetime | None:
    """移除数据库时间的偏移量，保持 Java VO 的 LocalDateTime 输出形态。"""
    return value.replace(tzinfo=None) if value is not None else None


def safe_positive(value: int | None, default: int) -> int:
    """读取正数配置，非法时回退默认值。"""
    return value if value is not None and value > 0 else default


def read_positive_env(name: str, default: int) -> int:
    """读取正整数环境变量。"""
    try:
        return safe_positive(int(os.getenv(name, "")), default)
    except ValueError:
        return default


def read_bool_env(name: str, default: bool) -> bool:
    """读取布尔环境变量。"""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def safe_limit(value: int | str | None) -> int:
    """把日志查询条数限制在 Java 的 1..200 区间。"""
    parsed = parse_optional_integer(value, "日志条数")
    return 50 if parsed is None else max(1, min(parsed, 200))


def safe_days(value: int | str | None) -> int:
    """把日志统计天数限制在 Java 的 1..90 区间。"""
    parsed = parse_optional_integer(value, "统计天数")
    return 7 if parsed is None else max(1, min(parsed, 90))


def parse_optional_integer(value: int | str | None, label: str) -> int | None:
    """解析可选整数参数，保留业务错误信封而非返回框架 422。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise LogBusinessError(f"{label}参数不合法") from None


def text_value(context: Mapping[str, Any], key: str) -> str | None:
    """从上下文读取非空文本字段。"""
    value = context.get(key)
    return None if value is None else str(value)


def integer_value(context: Mapping[str, Any], key: str) -> int | None:
    """从上下文读取整数，无法解析时返回空。"""
    value = context.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def normalize_status(value: str | None) -> str:
    """统一进度状态大小写。"""
    return value.strip().upper() if value else ""


def has_unfinished_progress(context: Mapping[str, Any]) -> bool:
    """判断进度上下文是否明确表示任务仍在进行。"""
    current_chunk = integer_value(context, "currentChunk")
    total_chunks = integer_value(context, "totalChunks")
    if current_chunk is not None and total_chunks is not None and current_chunk < total_chunks:
        return True
    percent = integer_value(context, "percent")
    return percent is not None and 0 < percent < 100


def completed_chunk_count(context: Mapping[str, Any]) -> int | None:
    """兼容 chunkCount、totalChunks、currentChunk 三种索引完成回调。"""
    return (
        integer_value(context, "chunkCount")
        or integer_value(context, "totalChunks")
        or integer_value(context, "currentChunk")
    )


def default_text(value: str | None, default: str | None) -> str | None:
    """为空文本提供默认值。"""
    return default if is_blank(value) else value.strip()


def is_blank(value: str | None) -> bool:
    """判断字符串是否缺失或仅由空白组成。"""
    return value is None or not value.strip()


def truncate(value: str | None, max_length: int) -> str | None:
    """按 Java 字符串长度规则截断文本。"""
    if value is None or len(value) <= max_length:
        return value
    return value[:max_length]


def timedelta_days(days: int):
    """延迟导入 timedelta，避免模块常量与 clock 注入耦合。"""
    from datetime import timedelta

    return timedelta(days=days)
