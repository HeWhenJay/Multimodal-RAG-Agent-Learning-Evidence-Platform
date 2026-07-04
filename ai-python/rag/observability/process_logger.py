from __future__ import annotations

import inspect
import json
import logging
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import perf_counter
from typing import Any

from rag.observability.log_callback import post_log_error, post_log_event
from rag.observability.progress import parse_material_id, truncate


logger = logging.getLogger(__name__)
_CURRENT_PROCESS_LOGGER: ContextVar[RagProcessLogger | None] = ContextVar("rag_process_logger", default=None)
SENSITIVE_KEYS = {"content", "text", "question", "answer", "resume", "resumetext", "jobdescription", "file"}


class RagProcessLogger:
    """写入 Python RAG 处理过程日志，供 Java 控制面板查看。"""

    def __init__(
        self,
        *,
        document_id: str,
        user_id: str = "anonymous",
        module: str = "material",
        database_url: str | None = None,
        persist: bool = True,
    ) -> None:
        self.trace_id = "py_" + uuid.uuid4().hex
        self.document_id = document_id
        self.user_id = user_id or "anonymous"
        self.module = module
        self.schema = os.getenv("RAG_DATABASE_SCHEMA", "learning_evidence")
        self.database_url = database_url or os.getenv("RAG_DATABASE_URL") or os.getenv("DATABASE_URL")
        self.persist = persist and bool(self.database_url) and process_log_persist_enabled()
        self.material_id = parse_material_id(document_id)

    @contextmanager
    def step(
        self,
        stage: str,
        action: str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        parser: str | None = None,
    ):
        """记录方法开始、完成和失败三种状态。"""
        start = perf_counter()
        self.event(
            stage=stage,
            action=f"{action}_start",
            message=f"开始：{message}",
            context={**(context or {}), "phase": "start"},
            parser=parser,
        )
        try:
            yield
        except Exception as exc:
            duration_ms = round((perf_counter() - start) * 1000)
            failed_context = {
                **(context or {}),
                "phase": "failed",
                "errorType": exc.__class__.__name__,
                "errorMessage": truncate(str(exc), 500),
            }
            self.event(
                stage=stage,
                action=f"{action}_failed",
                message=f"失败：{message}",
                context=failed_context,
                level="ERROR",
                success=False,
                duration_ms=duration_ms,
                parser=parser,
            )
            self.error(
                stage=stage,
                action=f"{action}_failed",
                error_code="RAG_PYTHON_PROCESS_FAILED",
                message=f"Python RAG 处理失败：{message}",
                throwable=exc,
                context=failed_context,
                parser=parser,
            )
            raise
        else:
            duration_ms = round((perf_counter() - start) * 1000)
            self.event(
                stage=stage,
                action=f"{action}_completed",
                message=f"完成：{message}",
                context={**(context or {}), "phase": "completed"},
                success=True,
                duration_ms=duration_ms,
                parser=parser,
            )

    def event(
        self,
        *,
        stage: str,
        action: str,
        message: str,
        context: dict[str, Any] | None = None,
        level: str = "INFO",
        success: bool = True,
        duration_ms: int | None = None,
        parser: str | None = None,
    ) -> None:
        """写入一条 RAG 处理事件；落库失败不影响主流程。"""
        safe_context = sanitize_context(context or {})
        safe_context["documentId"] = self.document_id
        safe_context["materialId"] = self.material_id
        safe_context["processStage"] = stage
        safe_context["processAction"] = action
        if duration_ms is not None:
            safe_context["durationMs"] = duration_ms

        log_message = "Python RAG 处理日志: documentId=%s stage=%s action=%s message=%s"
        normalized_level = level.upper()
        if not success or normalized_level == "ERROR":
            logger.error(log_message, self.document_id, stage, action, message)
        elif normalized_level in {"WARN", "WARNING"}:
            logger.warning(log_message, self.document_id, stage, action, message)
        else:
            logger.info(log_message, self.document_id, stage, action, message)
        self._console(stage, action, message, normalized_level, success, duration_ms, safe_context)

        if self._post_event_callback(stage, action, message, safe_context, level, success, duration_ms, parser):
            return
        if not self.persist:
            return
        self._persist(stage, action, message, safe_context, level, success, duration_ms, parser)

    def _console(
        self,
        stage: str,
        action: str,
        message: str,
        level: str,
        success: bool,
        duration_ms: int | None,
        context: dict[str, Any],
    ) -> None:
        """把方法级处理日志同步打印到 Python 控制台。"""
        if os.getenv("RAG_CONSOLE_PROCESS_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}:
            return
        parts = [
            f"traceId={self.trace_id}",
            f"documentId={self.document_id}",
            f"stage={stage}",
            f"action={action}",
            f"level={level}",
            f"success={str(success).lower()}",
        ]
        if duration_ms is not None:
            parts.append(f"durationMs={duration_ms}")
        for key in ("filename", "fileType", "documentType", "contentType", "parser", "status", "phase"):
            value = context.get(key)
            if value not in (None, ""):
                parts.append(f"{key}={value}")
        if "chunkIndex" in context and "totalChunks" in context:
            parts.append(f"chunk={context.get('chunkIndex')}/{context.get('totalChunks')}")
        if "chunkId" in context:
            parts.append(f"chunkId={context.get('chunkId')}")
        if "blockCount" in context:
            parts.append(f"blockCount={context.get('blockCount')}")
        if "chunkCount" in context:
            parts.append(f"chunkCount={context.get('chunkCount')}")
        if "highPrecision" in context:
            parts.append(f"highPrecision={context.get('highPrecision')}")
        parts.append(f"message={message}")
        print("RAG处理 | " + " | ".join(parts), flush=True)

    def _post_event_callback(
        self,
        stage: str,
        action: str,
        message: str,
        context: dict[str, Any],
        level: str,
        success: bool,
        duration_ms: int | None,
        parser: str | None,
    ) -> bool:
        """优先把方法级处理日志回调给 Java 控制面板。"""
        payload = {
            "traceId": self.trace_id,
            "userId": truncate(self.user_id, 120),
            "source": "python",
            "domain": "rag",
            "level": truncate(level.upper(), 20),
            "module": truncate(self.module, 80),
            "stage": truncate(stage, 80),
            "eventType": "rag_process",
            "action": truncate(action, 120),
            "message": truncate(message, 500),
            "success": success,
            "durationMs": duration_ms,
            "materialId": self.material_id,
            "documentId": truncate(self.document_id, 120),
            "parser": truncate(parser, 80),
            "context": context,
        }
        return post_log_event(payload)

    def _persist(
        self,
        stage: str,
        action: str,
        message: str,
        context: dict[str, Any],
        level: str,
        success: bool,
        duration_ms: int | None,
        parser: str | None,
    ) -> None:
        try:
            import psycopg
            from psycopg import sql
        except ImportError:
            return

        try:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
                    cursor.execute(
                        """
                        INSERT INTO log_event (
                            trace_id,
                            user_id,
                            source,
                            domain,
                            level,
                            module,
                            stage,
                            event_type,
                            action,
                            message,
                            success,
                            duration_ms,
                            material_id,
                            document_id,
                            parser,
                            context_json
                        )
                        VALUES (%s, %s, 'python', 'rag', %s, %s, %s,
                                'rag_process', %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            self.trace_id,
                            truncate(self.user_id, 120),
                            truncate(level.upper(), 20),
                            truncate(self.module, 80),
                            truncate(stage, 80),
                            truncate(action, 120),
                            truncate(message, 500),
                            success,
                            duration_ms,
                            self.material_id,
                            truncate(self.document_id, 120),
                            truncate(parser, 80),
                            truncate(json.dumps(context, ensure_ascii=False), 20000),
                        ),
                    )
        except Exception:
            return

    def error(
        self,
        *,
        stage: str,
        action: str,
        error_code: str,
        message: str,
        throwable: Exception,
        context: dict[str, Any] | None = None,
        parser: str | None = None,
    ) -> None:
        """写入 Python RAG 错误日志，供控制面板错误列表聚合。"""
        if not self.persist:
            return
        try:
            import psycopg
            from psycopg import sql
        except ImportError:
            return

        safe_context = sanitize_context(context or {})
        safe_context["documentId"] = self.document_id
        safe_context["materialId"] = self.material_id
        safe_context["ragStage"] = stage
        safe_context["action"] = action
        stack_trace = truncate(format_stack_trace(throwable), 20000)
        fingerprint = "py_" + uuid.uuid5(
            uuid.NAMESPACE_URL,
            "|".join([
                "python",
                "rag",
                stage,
                action,
                error_code,
                throwable.__class__.__name__,
                truncate(str(throwable), 500) or "",
            ]),
        ).hex
        if self._post_error_callback(
            stage=stage,
            action=action,
            error_code=error_code,
            message=message,
            throwable=throwable,
            context=safe_context,
            stack_trace=stack_trace,
            fingerprint=fingerprint,
            parser=parser,
        ):
            return
        try:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
                    cursor.execute(
                        """
                        INSERT INTO log_error (
                            trace_id,
                            user_id,
                            source,
                            domain,
                            severity,
                            module,
                            stage,
                            action,
                            error_type,
                            error_code,
                            message,
                            stack_trace,
                            fingerprint,
                            material_id,
                            document_id,
                            parser,
                            context_json,
                            status
                        )
                        VALUES (%s, %s, 'python', 'rag', 'ERROR', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN')
                        ON CONFLICT (fingerprint)
                        DO UPDATE SET
                            occurrence_count = log_error.occurrence_count + 1,
                            last_seen_at = CURRENT_TIMESTAMP,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            self.trace_id,
                            truncate(self.user_id, 120),
                            truncate(self.module, 80),
                            truncate(stage, 80),
                            truncate(action, 120),
                            truncate(throwable.__class__.__name__, 120),
                            truncate(error_code, 120),
                            truncate(message, 1000),
                            stack_trace,
                            fingerprint,
                            self.material_id,
                            truncate(self.document_id, 120),
                            truncate(parser, 80),
                            truncate(json.dumps(safe_context, ensure_ascii=False), 20000),
                        ),
                    )
        except Exception:
            return

    def _post_error_callback(
        self,
        *,
        stage: str,
        action: str,
        error_code: str,
        message: str,
        throwable: Exception,
        context: dict[str, Any],
        stack_trace: str | None,
        fingerprint: str,
        parser: str | None,
    ) -> bool:
        """优先把 Python 错误回调给 Java 错误面板。"""
        payload = {
            "traceId": self.trace_id,
            "userId": truncate(self.user_id, 120),
            "source": "python",
            "domain": "rag",
            "severity": "ERROR",
            "module": truncate(self.module, 80),
            "stage": truncate(stage, 80),
            "action": truncate(action, 120),
            "errorType": truncate(throwable.__class__.__name__, 120),
            "errorCode": truncate(error_code, 120),
            "message": truncate(message, 1000),
            "stackTrace": stack_trace,
            "fingerprint": fingerprint,
            "materialId": self.material_id,
            "documentId": truncate(self.document_id, 120),
            "parser": truncate(parser, 80),
            "context": context,
        }
        return post_log_error(payload)


@contextmanager
def use_process_logger(process_logger: RagProcessLogger):
    """绑定当前请求的处理日志上下文。"""
    token = _CURRENT_PROCESS_LOGGER.set(process_logger)
    try:
        yield
    finally:
        _CURRENT_PROCESS_LOGGER.reset(token)


def current_process_logger() -> RagProcessLogger | None:
    """读取当前请求绑定的处理日志器。"""
    return _CURRENT_PROCESS_LOGGER.get()


def process_log_persist_enabled() -> bool:
    """判断过程日志是否需要写入数据库，内存后端默认跳过落库。"""
    configured = os.getenv("RAG_PROCESS_LOG_PERSIST_ENABLED")
    if configured is not None and configured.strip() != "":
        return configured.strip().lower() in {"1", "true", "yes", "y", "on"}
    return os.getenv("RAG_STORE_BACKEND", "").strip().lower() != "memory"


def logged_rag_method(stage: str, action: str, message: str):
    """装饰 RAG 关键方法，自动记录进入、完成和失败。"""

    def decorator(func):
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                process_logger = current_process_logger()
                if process_logger is None:
                    return await func(*args, **kwargs)
                context = method_context(func, args, kwargs)
                with process_logger.step(stage, action, message, context=context):
                    return await func(*args, **kwargs)

            return async_wrapper

        @wraps(func)
        def wrapper(*args, **kwargs):
            process_logger = current_process_logger()
            if process_logger is None:
                return func(*args, **kwargs)
            context = method_context(func, args, kwargs)
            with process_logger.step(stage, action, message, context=context):
                return func(*args, **kwargs)

        return wrapper

    return decorator


def process_event(
    *,
    stage: str,
    action: str,
    message: str,
    context: dict[str, Any] | None = None,
    level: str = "INFO",
    success: bool = True,
) -> None:
    """在当前请求上下文中写入一条处理日志。"""
    process_logger = current_process_logger()
    if process_logger is None:
        return
    process_logger.event(stage=stage, action=action, message=message, context=context, level=level, success=success)


def method_context(func, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """从方法参数中提取安全摘要，避免记录正文和问题全文。"""
    context: dict[str, Any] = {
        "method": f"{func.__module__}.{func.__qualname__}",
    }
    try:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
    except Exception:
        return context
    for key, value in bound.arguments.items():
        if key == "self":
            continue
        context[key] = summarize_value(key, value)
    return context


def sanitize_context(context: dict[str, Any]) -> dict[str, Any]:
    """递归清理日志上下文，限制文本长度和嵌套层级。"""
    return {str(key): summarize_value(str(key), value) for key, value in context.items()}


def summarize_value(key: str, value: Any, depth: int = 0) -> Any:
    if depth > 3:
        return {"truncatedDepth": True}
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "size": len(value)}
    if isinstance(value, str):
        if key.lower() in SENSITIVE_KEYS:
            return {"type": "text", "length": len(value)}
        return truncate(value, 240)
    if isinstance(value, (list, tuple, set)):
        return {"type": value.__class__.__name__, "count": len(value)}
    if isinstance(value, dict):
        return {
            str(item_key): summarize_value(str(item_key), item_value, depth + 1)
            for item_key, item_value in value.items()
        }
    if hasattr(value, "model_dump"):
        return summarize_value(key, value.model_dump(mode="python"), depth + 1)
    return truncate(repr(value), 240)


def format_stack_trace(throwable: Exception) -> str:
    """格式化异常堆栈，落库失败时供错误面板定位。"""
    import traceback

    return "".join(traceback.format_exception(type(throwable), throwable, throwable.__traceback__))
