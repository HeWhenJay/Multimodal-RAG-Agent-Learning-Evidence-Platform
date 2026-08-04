"""纯 Python Agent 进程内网关。

统一图只能通过本模块访问任务事实、记忆和 RAG 控制面。这里不创建 HTTP
客户端，也不接受浏览器提供的用户身份，避免重新引入跨服务回调链路。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
from typing import Any, Protocol

from agents.tokenization import count_tokens, tokenizer_metadata
from app.agent_runtime.service import AgentRuntimeService
from app.agent_runtime.runtime_state_cache import AgentRuntimeStateCache
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
        recent_limit: int | None = None,
        summary_limit: int = 6,
        best_window_tokens: int = 256_000,
        summary_target_tokens: int = 25_000,
    ) -> dict[str, Any]: ...

    def save_context_summary(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def recall_context_messages(self, task_id: str, params: dict[str, Any]) -> list[dict[str, Any]]: ...

    def claim_benchmark_pause(self, turn_id: str, ttl_seconds: int) -> bool: ...


class LocalAgentGateway:
    """将统一图操作投影到 Python 的任务、记忆和 RAG 服务。"""

    def __init__(
        self,
        task_id: str,
        runtime_service: AgentRuntimeService | None = None,
        rag_service_factory: Callable[[], RagControlService] | None = None,
        runtime_state_cache: AgentRuntimeStateCache | None = None,
    ) -> None:
        self._task_id = task_id
        self._runtime_service = runtime_service or AgentRuntimeService()
        self._rag_service_factory = rag_service_factory or RagControlService
        self._runtime_state_cache = runtime_state_cache or AgentRuntimeStateCache()

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
        recent_limit: int | None = None,
        summary_limit: int = 6,
        best_window_tokens: int = 256_000,
        summary_target_tokens: int = 25_000,
    ) -> dict[str, Any]:
        """从 PostgreSQL 消息和压缩摘要恢复统一图上下文。"""
        task = self._require_task_id(task_id)
        user_id = str(task["user_id"])
        thread_id = string_value(task.get("python_thread_id")) or task_id
        if self._redis_context_enabled(task):
            cached = self._runtime_state_cache.load_context(user_id, task_id, thread_id)
            if cached is not None and self._context_cache_compatible(cached):
                return self._context_with_source(cached, "redis_l2", query, summary_limit, best_window_tokens, summary_target_tokens)
        context = self._postgres_context(task, user_id, query, summary_limit, best_window_tokens, summary_target_tokens)
        if self._redis_context_enabled(task):
            self._runtime_state_cache.save_context(user_id, task_id, thread_id, context)
        return context

    def _postgres_context(
        self,
        task: dict[str, Any],
        user_id: str,
        query: str,
        summary_limit: int,
        best_window_tokens: int,
        summary_target_tokens: int,
    ) -> dict[str, Any]:
        """从 PostgreSQL 重建上下文；Redis 不可用时仍使用此权威路径。"""
        task_id = str(task["id"])
        trigger_tokens = positive_int(best_window_tokens, 256_000, upper=2_000_000)
        target_tokens = positive_int(summary_target_tokens, 25_000, upper=200_000)
        raw_fetch_limit = self._context_raw_message_fetch_limit(task)
        messages = self._list_context_messages(task_id, user_id, raw_fetch_limit)
        summaries = self._runtime_service.list_context_summaries(task_id, user_id, summary_limit)
        active_summaries = [item for item in summaries if item.get("status") in {"ACTIVE", "HIGH_LOSS_RISK"}]
        window = self._split_unsummarized_messages(messages, active_summaries, trigger_tokens, target_tokens) if self._context_compression_enabled(task) else self._uncompressed_message_window(messages, active_summaries)
        return {
            "taskId": task_id,
            "messageWindow": window["messageWindow"],
            "compressionCandidateMessages": window["compressionCandidateMessages"],
            "activeSummaries": active_summaries,
            "summarySegments": summaries,
            "budgetMetadata": {
                "promptTargetTokens": trigger_tokens,
                "summaryTargetTokens": target_tokens,
                "restoreSource": "postgresql",
                "queryPresent": bool(query.strip()),
                "summaryLimit": positive_int(summary_limit, 6, upper=20),
                "rawMessageFetchLimit": raw_fetch_limit,
                "rawMessageCount": len(messages),
                "unsummarizedMessageCount": window["unsummarizedMessageCount"],
                "messageWindowCount": len(window["messageWindow"]),
                "compressionCandidateCount": len(window["compressionCandidateMessages"]),
                "rawTokenEstimate": window["rawTokenEstimate"],
                "messageWindowTokenEstimate": window["messageWindowTokenEstimate"],
                "compressionCandidateTokenEstimate": window["compressionCandidateTokenEstimate"],
                "compressionEnabled": self._context_compression_enabled(task),
                "windowPolicy": "token_threshold_v2",
                **tokenizer_metadata(),
            },
        }

    def save_context_summary(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """将压缩摘要直接持久化到当前任务的 PostgreSQL 事实记录。"""
        task = self._require_task_id(task_id)
        saved = self._runtime_service.save_context_summary(task_id, payload)
        if self._redis_context_enabled(task):
            user_id = str(task["user_id"])
            thread_id = string_value(task.get("python_thread_id")) or task_id
            context = self._postgres_context(
                task,
                user_id,
                "",
                self._context_summary_limit(task),
                self._context_best_window_tokens(task),
                self._context_summary_target_tokens(task),
            )
            self._runtime_state_cache.save_context(user_id, task_id, thread_id, context)
        return saved

    def recall_context_messages(self, task_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """按持久化序列回捞当前任务的最近消息，避免跨用户读取。"""
        task = self._require_task_id(task_id)
        page = self._runtime_service.list_messages(task_id, str(task["user_id"]), None, None, 100)
        return list(page.get("messages") or [])

    def claim_benchmark_pause(self, turn_id: str, ttl_seconds: int) -> bool:
        """为线上基准提供一次性恢复暂停标记；普通任务永不调用。"""
        task = self._task()
        if not self._redis_context_enabled(task):
            return False
        return self._runtime_state_cache.claim_benchmark_pause(str(task["user_id"]), self._task_id, turn_id, ttl_seconds)

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
        if self._rag_offline_fallback_enabled(task):
            return self._succeeded(base, self._deterministic_rag_fallback(question, reason="offline-fallback"))
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
            return self._succeeded(base, self._deterministic_rag_fallback(question, reason=exc.__class__.__name__))

    @staticmethod
    def _deterministic_rag_fallback(question: str, *, reason: str) -> dict[str, Any]:
        """离线或 RAG 依赖不可用时返回空 evidence 的受控结果。"""
        return {
            "answer": "当前 Python RAG 暂无可用 evidence，建议先上传并完成资料索引。",
            "expandedQueries": [question],
            "evidences": [],
            "evidenceCount": 0,
            "diagnostics": {"provider": "deterministic-fallback", "reason": reason},
        }

    @staticmethod
    def _rag_offline_fallback_enabled(task: dict[str, Any]) -> bool:
        """离线测试或 Worker 基准可跳过 RAG 存储构造，避免数据库连接等待。"""
        benchmark = task_benchmark_config(task)
        if isinstance(benchmark.get("ragOfflineFallback"), bool):
            return bool(benchmark["ragOfflineFallback"])
        if os.getenv("AGENT_RAG_OFFLINE_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
        return os.getenv("AGENT_LLM_ENABLED", "true").strip().lower() in {"0", "false", "no", "off"}

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
    def _context_with_source(
        context: dict[str, Any],
        source: str,
        query: str,
        summary_limit: int,
        best_window_tokens: int,
        summary_target_tokens: int,
    ) -> dict[str, Any]:
        """为缓存命中补齐本次请求预算元数据，避免复用旧请求参数。"""
        copied = dict(context)
        budget = copied.get("budgetMetadata") if isinstance(copied.get("budgetMetadata"), dict) else {}
        copied["budgetMetadata"] = {
            **budget,
            "promptTargetTokens": positive_int(best_window_tokens, 256_000, upper=2_000_000),
            "summaryTargetTokens": positive_int(summary_target_tokens, 25_000, upper=200_000),
            "restoreSource": source,
            "queryPresent": bool(query.strip()),
            "summaryLimit": positive_int(summary_limit, 6, upper=20),
            "windowPolicy": "token_threshold_v2",
            **tokenizer_metadata(),
        }
        return copied

    @staticmethod
    def _context_cache_compatible(context: dict[str, Any]) -> bool:
        """旧缓存可能只保存最近 12 条，遇到旧策略缓存必须回源重建。"""
        budget = context.get("budgetMetadata") if isinstance(context.get("budgetMetadata"), dict) else {}
        return budget.get("windowPolicy") == "token_threshold_v2"

    def _list_context_messages(self, task_id: str, user_id: str, raw_fetch_limit: int) -> list[dict[str, Any]]:
        """分页读取当前任务消息，避免服务层 100 条公开窗口限制影响 Agent 恢复。"""
        messages: list[dict[str, Any]] = []
        before_sequence: int | None = None
        remaining = raw_fetch_limit
        while remaining > 0:
            page = self._runtime_service.list_messages(task_id, user_id, before_sequence, None, min(100, remaining))
            batch = [item for item in list(page.get("messages") or []) if isinstance(item, dict)]
            if not batch:
                break
            messages = batch + messages
            remaining -= len(batch)
            if not page.get("hasMoreBefore"):
                break
            before_sequence = non_negative_int(batch[0].get("sequenceNo"), 0, upper=10_000_000)
            if before_sequence <= 0:
                break
        return messages[-raw_fetch_limit:]

    def _split_unsummarized_messages(
        self,
        messages: list[dict[str, Any]],
        active_summaries: list[dict[str, Any]],
        trigger_tokens: int,
        summary_target_tokens: int,
    ) -> dict[str, Any]:
        """按 token 预算保留最近原文；超过阈值时仅把早期未摘要消息交给压缩节点。"""
        unsummarized = self._unsummarized_messages(messages, active_summaries)
        estimates = [self._message_token_estimate(item) for item in unsummarized]
        raw_tokens = sum(estimates)
        if raw_tokens <= trigger_tokens or len(unsummarized) <= 1:
            return {
                "messageWindow": unsummarized,
                "compressionCandidateMessages": [],
                "unsummarizedMessageCount": len(unsummarized),
                "rawTokenEstimate": raw_tokens,
                "messageWindowTokenEstimate": raw_tokens,
                "compressionCandidateTokenEstimate": 0,
            }
        reserved_tokens = max(summary_target_tokens * 2, trigger_tokens // 10)
        minimum_tail_budget = min(4_000, max(1, trigger_tokens // 2))
        tail_budget = max(minimum_tail_budget, trigger_tokens - reserved_tokens)
        tail_start = len(unsummarized)
        tail_tokens = 0
        for index in range(len(unsummarized) - 1, -1, -1):
            token_count = max(1, estimates[index])
            if tail_start < len(unsummarized) and tail_tokens + token_count > tail_budget:
                break
            tail_start = index
            tail_tokens += token_count
        if tail_start <= 0:
            tail_start = max(0, len(unsummarized) - 1)
            tail_tokens = sum(estimates[tail_start:])
        candidate_tokens = sum(estimates[:tail_start])
        return {
            "messageWindow": unsummarized[tail_start:],
            "compressionCandidateMessages": unsummarized[:tail_start],
            "unsummarizedMessageCount": len(unsummarized),
            "rawTokenEstimate": raw_tokens,
            "messageWindowTokenEstimate": tail_tokens,
            "compressionCandidateTokenEstimate": candidate_tokens,
        }

    def _uncompressed_message_window(self, messages: list[dict[str, Any]], active_summaries: list[dict[str, Any]]) -> dict[str, Any]:
        """压缩关闭时保留所有未被摘要覆盖的原文，用于 A 组对照。"""
        unsummarized = self._unsummarized_messages(messages, active_summaries)
        raw_tokens = sum(self._message_token_estimate(item) for item in unsummarized)
        return {
            "messageWindow": unsummarized,
            "compressionCandidateMessages": [],
            "unsummarizedMessageCount": len(unsummarized),
            "rawTokenEstimate": raw_tokens,
            "messageWindowTokenEstimate": raw_tokens,
            "compressionCandidateTokenEstimate": 0,
        }

    @staticmethod
    def _unsummarized_messages(messages: list[dict[str, Any]], active_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """排除已被 ACTIVE 摘要覆盖的早期原文，保留后续未摘要原文。"""
        index_by_id = {string_value(item.get("id")): index for index, item in enumerate(messages) if isinstance(item, dict) and string_value(item.get("id"))}
        last_covered_index = -1
        for summary in active_summaries:
            end_id = string_value(summary.get("coveredMessageEndId"))
            if end_id in index_by_id:
                last_covered_index = max(last_covered_index, index_by_id[end_id])
        return messages[last_covered_index + 1 :]

    @staticmethod
    def _message_token_estimate(message: dict[str, Any]) -> int:
        """按完整消息正文估算 token，用于动态窗口选择。"""
        payload = {
            "role": message.get("role"),
            "messageType": message.get("messageType"),
            "content": string_value(message.get("content")),
        }
        return count_tokens(payload)

    @staticmethod
    def _context_best_window_tokens(task: dict[str, Any]) -> int:
        benchmark = task_benchmark_config(task)
        return positive_int(benchmark.get("bestWindowTokens") if benchmark else os.getenv("AGENT_CONTEXT_BEST_WINDOW_TOKENS", 256_000), 256_000, upper=2_000_000)

    @staticmethod
    def _context_summary_target_tokens(task: dict[str, Any]) -> int:
        benchmark = task_benchmark_config(task)
        return positive_int(benchmark.get("summaryTargetTokens") if benchmark else os.getenv("AGENT_CONTEXT_SUMMARY_TARGET_TOKENS", 25_000), 25_000, upper=200_000)

    @staticmethod
    def _context_summary_limit(task: dict[str, Any]) -> int:
        benchmark = task_benchmark_config(task)
        return positive_int(benchmark.get("summaryLimit") if benchmark else os.getenv("AGENT_CONTEXT_SUMMARY_LIMIT", 6), 6, upper=20)

    @staticmethod
    def _context_raw_message_fetch_limit(task: dict[str, Any]) -> int:
        benchmark = task_benchmark_config(task)
        return positive_int(benchmark.get("rawMessageFetchLimit") if benchmark else os.getenv("AGENT_CONTEXT_RAW_MESSAGE_FETCH_LIMIT", 2000), 2000, upper=10_000)

    @staticmethod
    def _context_compression_enabled(task: dict[str, Any]) -> bool:
        benchmark = task_benchmark_config(task)
        if isinstance(benchmark.get("compressionEnabled"), bool):
            return bool(benchmark["compressionEnabled"])
        return os.getenv("AGENT_CONTEXT_COMPRESSION_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}

    @staticmethod
    def _redis_context_enabled(task: dict[str, Any]) -> bool:
        """基准可按任务关闭 Redis；正常 Agent 默认在配置可用时启用 L2。"""
        benchmark = task_benchmark_config(task)
        value = benchmark.get("redisEnabled")
        return bool(value) if isinstance(value, bool) else True

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


def task_benchmark_config(task: dict[str, Any]) -> dict[str, Any]:
    """兼容 PostgreSQL TEXT 与内存字典，读取本地基准预算覆盖项。"""
    raw_input = task.get("input_json")
    if isinstance(raw_input, dict):
        task_input = raw_input
    elif isinstance(raw_input, str):
        try:
            parsed = json.loads(raw_input)
        except json.JSONDecodeError:
            parsed = {}
        task_input = parsed if isinstance(parsed, dict) else {}
    else:
        task_input = {}
    benchmark = task_input.get("agentBenchmark") if isinstance(task_input.get("agentBenchmark"), dict) else {}
    return benchmark if benchmark.get("scenarioSetId") == "agent-control-long-context-v1" else {}


def string_value(value: Any) -> str:
    """规整网关入参中的可展示文本。"""
    return value.strip() if isinstance(value, str) else ""


def positive_int(value: Any, default: int, *, upper: int) -> int:
    """读取有上限的正整数，防止请求用过大窗口撑爆上下文。"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(parsed, upper))


def non_negative_int(value: Any, default: int, *, upper: int) -> int:
    """读取有上限的非负整数，用于分页水位等可为 0 的内部值。"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(parsed, upper))
