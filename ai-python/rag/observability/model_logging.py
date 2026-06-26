from __future__ import annotations

from contextlib import contextmanager
from time import perf_counter
from typing import Iterator

from rag.observability.process_logger import process_event


# 记录百炼模型调用生命周期，同时同步到 Python 控制台和 Java 控制面板。
@contextmanager
def log_model_call(
    *,
    stage: str,
    action: str,
    model_name: str,
    event: str,
    provider: str = "dashscope",
    extra_context: dict | None = None,
    recoverable: bool = False,
    fallback_message: str | None = None,
) -> Iterator[None]:
    """记录模型调用生命周期；可降级失败按告警处理，避免误标资料失败。"""
    context = {
        "modelProvider": provider,
        "modelName": model_name,
        "modelEvent": event,
        "modelPhase": "before",
        "modelRecoverable": recoverable,
        **(extra_context or {}),
    }
    process_event(
        stage=stage,
        action=f"{action}_model_before",
        message=f"目前在使用 {model_name} 模型完成{event}事件",
        context=context,
    )
    start = perf_counter()
    try:
        yield
    except Exception as exc:
        duration_ms = round((perf_counter() - start) * 1000)
        if recoverable:
            failed_action = f"{action}_model_degraded"
            failed_message = fallback_message or f"使用 {model_name} 模型完成{event}事件失败，已降级继续处理"
        else:
            failed_action = f"{action}_model_failed"
            failed_message = f"使用 {model_name} 模型完成{event}事件失败"
        process_event(
            stage=stage,
            action=failed_action,
            message=failed_message,
            level="WARN" if recoverable else "ERROR",
            success=recoverable,
            context={
                **context,
                "modelPhase": "degraded" if recoverable else "failed",
                "durationMs": duration_ms,
                "errorType": exc.__class__.__name__,
                "errorMessage": str(exc)[:500],
                "fallbackMessage": fallback_message or ("已降级继续处理" if recoverable else None),
            },
        )
        raise
    else:
        duration_ms = round((perf_counter() - start) * 1000)
        process_event(
            stage=stage,
            action=f"{action}_model_after",
            message=f"已使用 {model_name} 模型完成{event}事件",
            context={
                **context,
                "modelPhase": "after",
                "durationMs": duration_ms,
            },
        )
