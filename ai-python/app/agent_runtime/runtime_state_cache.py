"""Agent 运行态 Redis L2 缓存；PostgreSQL 始终保留恢复权威。"""

from __future__ import annotations

from datetime import date, datetime
import json
import os
from typing import Any, Protocol


class RedisLike(Protocol):
    """约束本模块实际使用的最小 Redis API，方便测试替换。"""

    def get(self, name: str) -> Any: ...

    def set(self, name: str, value: str, ex: int | None = None, nx: bool = False) -> Any: ...

    def delete(self, *names: str) -> Any: ...

    def ping(self) -> Any: ...


class AgentRuntimeStateCache:
    """缓存可由 PostgreSQL 事实表重建的 Agent 上下文快照。"""

    def __init__(self, redis_url: str | None = None, client: RedisLike | None = None) -> None:
        self._redis_url = (redis_url if redis_url is not None else os.getenv("REDIS_URL", "")).strip()
        self._client = client
        self._unavailable = False

    def is_available(self) -> bool:
        """确认 Redis 可用，失败时保持 PostgreSQL 回退而不向调用方抛异常。"""
        client = self._get_client()
        if client is None:
            return False
        try:
            client.ping()
            return True
        except Exception:
            self._unavailable = True
            return False

    def load_context(self, user_id: str, task_id: str, thread_id: str) -> dict[str, Any] | None:
        """读取并校验同一用户、任务和线程的缓存快照。"""
        client = self._get_client()
        if client is None:
            return None
        try:
            raw = client.get(self.context_key(user_id, task_id))
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            payload = json.loads(raw) if isinstance(raw, str) else None
            if not isinstance(payload, dict):
                return None
            if payload.get("taskId") != task_id or payload.get("userId") != str(user_id) or payload.get("threadId") != thread_id:
                return None
            context = payload.get("context")
            return dict(context) if isinstance(context, dict) else None
        except Exception:
            self._unavailable = True
            return None

    def save_context(self, user_id: str, task_id: str, thread_id: str, context: dict[str, Any], *, terminal: bool = False) -> bool:
        """写入低风险上下文快照，缓存失效不会影响业务任务。"""
        client = self._get_client()
        if client is None:
            return False
        payload = {
            "userId": str(user_id),
            "taskId": task_id,
            "threadId": thread_id,
            "context": context,
        }
        try:
            client.set(
                self.context_key(user_id, task_id),
                json.dumps(payload, ensure_ascii=False, default=cache_json_default),
                ex=self.context_ttl_seconds(terminal),
            )
            return True
        except Exception:
            self._unavailable = True
            return False

    def invalidate_context(self, user_id: str, task_id: str) -> None:
        """追加用户轮次后删除旧上下文，避免新 Worker 读取过期窗口。"""
        client = self._get_client()
        if client is None:
            return
        try:
            client.delete(self.context_key(user_id, task_id))
        except Exception:
            self._unavailable = True

    def claim_benchmark_pause(self, user_id: str, task_id: str, turn_id: str, ttl_seconds: int) -> bool:
        """仅线上基准使用，确保同一轮只让第一个 Worker 在恢复屏障暂停。"""
        client = self._get_client()
        if client is None:
            return False
        try:
            claimed = client.set(self.benchmark_pause_key(user_id, task_id, turn_id), "1", ex=max(1, ttl_seconds), nx=True)
            return bool(claimed)
        except Exception:
            self._unavailable = True
            return False

    @staticmethod
    def context_key(user_id: str, task_id: str) -> str:
        """返回与 Agent 设计文档一致的 Redis L2 键。"""
        return f"agent:ctx:{user_id}:{task_id}"

    @staticmethod
    def benchmark_pause_key(user_id: str, task_id: str, turn_id: str) -> str:
        """返回基准专用的一次性暂停标记键。"""
        return f"agent:benchmark:pause:{user_id}:{task_id}:{turn_id}"

    @staticmethod
    def context_ttl_seconds(terminal: bool) -> int:
        """按运行态或终态读取缓存 TTL，异常配置回退到安全默认值。"""
        if terminal:
            return positive_hours("EVIDENCE_AGENT_REDIS_COMPLETED_CONTEXT_TTL_DAYS", 7, days=True)
        return positive_hours("EVIDENCE_AGENT_REDIS_RUNNING_CONTEXT_TTL_HOURS", 24)

    def _get_client(self) -> RedisLike | None:
        if self._client is not None:
            return self._client
        if self._unavailable or not self._redis_url:
            return None
        try:
            from redis import Redis

            self._client = Redis.from_url(self._redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
            return self._client
        except Exception:
            self._unavailable = True
            return None


def positive_hours(name: str, default: int, *, days: bool = False) -> int:
    """读取 TTL 配置并限制范围，避免异常配置造成永久缓存。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = min(max(value, 1), 365 if days else 24 * 31)
    return value * 86400 if days else value * 3600


def cache_json_default(value: Any) -> str:
    """将 PostgreSQL 时间值稳定转换为可跨进程恢复的 ISO 文本。"""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Redis 运行态快照包含不支持的类型：{value.__class__.__name__}")
