from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from agents.gateway.local_gateway import AgentGateway


def utc_time_provider() -> dict[str, str]:
    """返回 UTC 时间；该系统工具不访问用户数据。"""
    return {"utcTime": datetime.now(timezone.utc).isoformat()}


def tool_observation_summary(result: dict[str, Any]) -> dict[str, Any]:
    """持久化 Observation 只保留摘要，避免保存正文。"""
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    evidences = data.get("evidences") if isinstance(data.get("evidences"), list) else []
    diagnostics = data.get("diagnostics") if isinstance(data.get("diagnostics"), dict) else result.get("diagnostics")
    return {
        "status": result.get("status"),
        "toolName": result.get("toolName"),
        "answerLength": len(str(data.get("answer") or "")),
        "evidenceCount": len(evidences) if evidences else int_value(data.get("evidenceCount"), 0),
        "expandedQueryCount": len(data.get("expandedQueries")) if isinstance(data.get("expandedQueries"), list) else 0,
        "memoryCount": int_value(data.get("memoryCount"), 0),
        "candidateCount": int_value(data.get("candidateCount"), 0),
        "diagnosticKeys": list(diagnostics.keys()) if isinstance(diagnostics, dict) else [],
    }


def prefetch_memory_context(
    *,
    task_id: str,
    thread_id: str,
    task_input: dict[str, Any],
    query: str,
    client: AgentGateway,
) -> list[dict[str, Any]]:
    """通过 Python 本地 Gateway 预取可注入记忆，Python 不自行判断用户权限。"""
    if not query:
        return []
    payload = {
        "taskId": task_id,
        "toolCallId": f"tool-call-memory-{uuid.uuid4().hex}",
        "toolName": "agent_memory_retriever",
        "arguments": {
            "query": query,
            "topK": int_value(task_input.get("memoryTopK"), 5),
        },
    }
    try:
        result = client.execute_read_tool(payload)
    except Exception:
        return []
    if result.get("status") != "SUCCEEDED":
        return []
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    memory_context = data.get("memoryContext") if isinstance(data.get("memoryContext"), list) else data.get("memories")
    if not isinstance(memory_context, list):
        return []
    return [item for item in memory_context if isinstance(item, dict)]


def task_query(task_input: dict[str, Any], task_type: str | None = None) -> str:
    """生成记忆预取使用的查询文本。"""
    goal = text_value(task_input.get("question")) or text_value(task_input.get("goal"))
    if task_type == "planning_task":
        jd_text = text_value(task_input.get("jobDescription"))
        resume_text = text_value(task_input.get("resumeText"))
        return "\n".join(item for item in [goal, jd_text[:300], resume_text[:240]] if item)
    return goal


def text_value(value: Any) -> str:
    """读取非空文本。"""
    return "" if value is None else str(value).strip()


def int_value(value: Any, default: int) -> int:
    """读取整数，失败时使用默认值。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
