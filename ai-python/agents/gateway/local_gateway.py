"""纯 Python Agent 进程内网关。

统一图只能通过本模块访问任务事实、记忆和 RAG 控制面。这里不创建 HTTP
客户端，也不接受浏览器提供的用户身份，避免重新引入跨服务回调链路。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import os
from typing import Any, Protocol

from app.agent_runtime.service import AgentRuntimeService
from app.schemas.agent import AgentTaskEvent
from app.schemas.rag_control import RagQueryPublicRequest
from app.services.rag_control_service import RagControlService


class AgentGateway(Protocol):
    """统一图需要的最小进程内能力边界。"""

    def execute_read_tool(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def execute_mutation_tool(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def publish_event(self, event: AgentTaskEvent) -> None: ...

    def restore_context(
        self,
        task_id: str,
        *,
        query: str = "",
        recent_limit: int = 12,
        summary_limit: int = 6,
        best_window_tokens: int = 18_000,
    ) -> dict[str, Any]: ...

    def save_context_summary(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def recall_context_messages(self, task_id: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...


class LocalAgentGateway:
    """将统一图操作投影到 Python 的任务、记忆和 RAG 服务。"""

    def __init__(
        self,
        task_id: str,
        runtime_service: AgentRuntimeService | None = None,
        rag_service_factory: Callable[[], RagControlService] | None = None,
    ) -> None:
        self._task_id = task_id
        self._runtime_service = runtime_service or AgentRuntimeService()
        self._rag_service_factory = rag_service_factory or RagControlService

    def execute_read_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        """执行白名单只读工具，并由任务记录推导当前用户。"""
        task = self._task()
        tool_name = string_value(payload.get("toolName"))
        arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
        base = self._tool_result_base(payload, tool_name)
        if tool_name == "agent_memory_retriever":
            memories = self._runtime_service.memory_context(
                str(task["user_id"]),
                string_value(arguments.get("query")),
                positive_int(arguments.get("topK"), 5, upper=20),
            )
            return self._succeeded(
                base,
                {
                    "memoryContext": memories,
                    "memories": memories,
                    "memoryCount": len(memories),
                    "diagnostics": {"provider": "python-agent-memory"},
                },
            )
        if tool_name == "agent_memory_candidate_proposer":
            return self._succeeded(base, self._memory_candidates(task, arguments))
        if tool_name == "utc_time_provider":
            return self._succeeded(base, {"utcTime": datetime.now(timezone.utc).isoformat()})
        if tool_name in {"rag_query_probe_non_persistent", "retrieval_coverage_probe"}:
            return self._query_rag(base, task, arguments, coverage_only=tool_name == "retrieval_coverage_probe")
        if tool_name == "web_search_probe":
            return self._failed(
                base,
                "AGENT_TAVILY_NOT_CONFIGURED",
                "当前纯 Python Agent 尚未启用联网搜索，已降级为本地 RAG evidence。",
                retryable=False,
            )
        return self._failed(base, "AGENT_TOOL_FORBIDDEN", f"未开放的只读工具：{tool_name or 'unknown'}", retryable=False)

    def execute_mutation_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        """只执行已由当前用户审批的最小变更投影。"""
        if string_value(payload.get("taskId")) not in {"", self._task_id}:
            return self._failed(self._tool_result_base(payload, string_value(payload.get("toolName"))), "AGENT_RESOURCE_FORBIDDEN", "任务归属校验失败", retryable=False)
        return self._runtime_service.apply_approved_mutation(self._task_id, payload)

    def publish_event(self, event: AgentTaskEvent) -> None:
        """将图事件直接写入 PostgreSQL 任务投影，不发起跨服务回调。"""
        payload = event.model_dump(by_alias=True, exclude_none=True)
        self._runtime_service.apply_agent_event(self._task_id, payload)

    def restore_context(
        self,
        task_id: str,
        *,
        query: str = "",
        recent_limit: int = 12,
        summary_limit: int = 6,
        best_window_tokens: int = 18_000,
    ) -> dict[str, Any]:
        """从 PostgreSQL 消息和压缩摘要恢复统一图上下文。"""
        task = self._require_task_id(task_id)
        page = self._runtime_service.list_messages(
            task_id,
            str(task["user_id"]),
            None,
            None,
            max(positive_int(recent_limit, 12, upper=100), 1),
        )
        messages = list(page.get("messages") or [])
        summaries = self._runtime_service.list_context_summaries(task_id, str(task["user_id"]), summary_limit)
        active_summaries = [item for item in summaries if item.get("status") in {"ACTIVE", "HIGH_LOSS_RISK"}]
        return {
            "taskId": task_id,
            "messageWindow": messages[-recent_limit:],
            "compressionCandidateMessages": messages[:-recent_limit] if len(messages) > recent_limit else [],
            "activeSummaries": active_summaries,
            "summarySegments": summaries,
            "budgetMetadata": {
                "promptTargetTokens": positive_int(best_window_tokens, 18_000, upper=100_000),
                "restoreSource": "postgresql",
                "queryPresent": bool(query.strip()),
                "summaryLimit": positive_int(summary_limit, 6, upper=20),
            },
        }

    def save_context_summary(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """将压缩摘要直接持久化到当前任务的 PostgreSQL 事实记录。"""
        self._require_task_id(task_id)
        return self._runtime_service.save_context_summary(task_id, payload)

    def recall_context_messages(self, task_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """按持久化序列回捞当前任务的最近消息，避免跨用户读取。"""
        task = self._require_task_id(task_id)
        page = self._runtime_service.list_messages(task_id, str(task["user_id"]), None, None, 100)
        return list(page.get("messages") or [])

    def _query_rag(
        self,
        base: dict[str, Any],
        task: dict[str, Any],
        arguments: dict[str, Any],
        *,
        coverage_only: bool,
    ) -> dict[str, Any]:
        """复用 Python RAG 控制面，并在不可用时返回确定性降级结果。"""
        question = string_value(arguments.get("question"))
        if not question:
            return self._failed(base, "AGENT_VALIDATION_FAILED", "RAG 检索问题不能为空", retryable=False)
        try:
            response = self._rag_service_factory().query(
                RagQueryPublicRequest(
                    question=question,
                    topK=positive_int(arguments.get("topK"), 5, upper=20),
                    candidateMultiplier=positive_int(arguments.get("candidateMultiplier"), 4, upper=10),
                    metadataFilter=arguments.get("metadataFilter") if isinstance(arguments.get("metadataFilter"), dict) else {},
                ),
                str(task["user_id"]),
            )
            result = response.model_dump(mode="json") if hasattr(response, "model_dump") else dict(response)
            evidences = result.get("evidences") if isinstance(result.get("evidences"), list) else []
            data = {
                "answer": result.get("answer") or "当前资料未找到足够 evidence。",
                "expandedQueries": result.get("expandedQueries") if isinstance(result.get("expandedQueries"), list) else [question],
                "evidences": evidences,
                "evidenceCount": len(evidences),
                "diagnostics": result.get("diagnostics") if isinstance(result.get("diagnostics"), dict) else {},
            }
            if coverage_only:
                data["coverage"] = {"evidenceCount": len(evidences), "answerStatus": result.get("answerStatus")}
            return self._succeeded(base, data)
        except Exception as exc:
            # 无资料、模型或索引依赖时可完成只读任务，不让 durable worker 永久卡在 RUNNING。
            return self._succeeded(
                base,
                {
                    "answer": "当前 Python RAG 暂无可用 evidence，建议先上传并完成资料索引。",
                    "expandedQueries": [question],
                    "evidences": [],
                    "evidenceCount": 0,
                    "diagnostics": {"provider": "deterministic-fallback", "reason": exc.__class__.__name__},
                },
            )

    def _memory_candidates(self, task: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
        """生成待确认记忆候选，候选本身不自动写入长期记忆。"""
        task_input = task.get("input_json") if isinstance(task.get("input_json"), dict) else {}
        goal = string_value(task_input.get("goal")) or string_value(task_input.get("question"))
        candidate = {
            "memoryType": "EPISODIC",
            "namespace": "agent_task",
            "scopeType": "USER",
            "subjectKey": "recent_task_insight",
            "content": f"用户近期 Agent 任务：{goal[:300]}" if goal else "用户完成了一次 Agent 任务。",
            "summary": f"近期任务：{goal[:120]}" if goal else "近期 Agent 任务摘要",
            "sourceTaskId": self._task_id,
            "confidence": 0.6,
            "importance": 0.5,
        }
        return {"candidates": [candidate], "conflicts": [], "provider": "python-deterministic-memory-candidate"}

    def _task(self) -> dict[str, Any]:
        return self._runtime_service.task_record(self._task_id)

    def _require_task_id(self, task_id: str) -> dict[str, Any]:
        if task_id != self._task_id:
            raise ValueError("Agent 任务 ID 不匹配")
        return self._task()

    @staticmethod
    def _tool_result_base(payload: dict[str, Any], tool_name: str) -> dict[str, Any]:
        return {
            "taskId": string_value(payload.get("taskId")),
            "toolCallId": string_value(payload.get("toolCallId")),
            "toolName": tool_name,
            "ownershipVerified": True,
            "scope": "current_user",
        }

    @staticmethod
    def _succeeded(base: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
        return {**base, "status": "SUCCEEDED", "data": data, "diagnostics": data.get("diagnostics", {}), "retryable": False}

    @staticmethod
    def _failed(base: dict[str, Any], code: str, message: str, *, retryable: bool) -> dict[str, Any]:
        return {
            **base,
            "status": "FAILED",
            "data": {},
            "diagnostics": {},
            "retryable": retryable,
            "errorCode": code,
            "errorMessage": message,
        }


def string_value(value: Any) -> str:
    """规整网关入参中的可展示文本。"""
    return value.strip() if isinstance(value, str) else ""


def positive_int(value: Any, default: int, *, upper: int) -> int:
    """读取有上限的正整数，防止请求用过大 topK 撑爆检索。"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, upper))
