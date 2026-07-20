"""持久化 Agent 任务 worker。

任务由公开 HTTP API 先写入 PostgreSQL，worker 再从 `agent_task` 读取并通过
进程内 LocalAgentGateway 执行统一图。没有外部 HTTP client、内部令牌或 callback。
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from typing import Any

from agents.gateway.local_gateway import LocalAgentGateway
from agents.orchestration.pae_react_graph import resume_unified_agent, start_unified_agent
from app.agent_runtime.service import AgentRuntimeService
from app.core.runtime_config import load_runtime_config, parse_args
from app.schemas.agent import AgentTaskResumeRequest, AgentTaskStartRequest


logger = logging.getLogger(__name__)


class AgentTaskWorker:
    """单进程顺序领取任务，保证图事件按同一任务顺序落库。"""

    def __init__(self, service: AgentRuntimeService | None = None) -> None:
        self._service = service or AgentRuntimeService()

    def process_available_tasks(self, limit: int | None = None) -> int:
        """处理当前批次可运行任务并返回实际尝试数。"""
        processed = 0
        seen: set[str] = set()
        for task in self._service.list_runnable_task_records(limit):
            task_id = text(task.get("id"))
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            if self.process_task(task):
                processed += 1
        return processed

    def process_task(self, task: dict[str, Any]) -> bool:
        """执行初始任务或根据最近审批恢复同一持久任务。"""
        task_id = text(task.get("id"))
        if not task_id:
            return False
        with self._service.task_execution_lock(task_id) as acquired:
            if not acquired:
                return False
            # 锁获取后刷新一次，避免等待锁期间审批或终态已改变而重复执行。
            current = self._service.task_record(task_id)
            if current.get("status") not in {"CREATED", "RUNNING"}:
                return False
            gateway = LocalAgentGateway(task_id, self._service)
            try:
                review = self._service.latest_resumable_review(task_id) if current.get("status") == "RUNNING" else None
                if review is None:
                    request = AgentTaskStartRequest(
                        taskId=task_id,
                        taskType=text(current.get("task_type")) or "pure_read_query",
                        input=json_object(current.get("input_json")),
                        threadId=text(current.get("python_thread_id")) or task_id,
                    )
                    start_unified_agent(request, gateway)
                    return True
                decision_payload = json_object(review.get("decision_json"))
                request = AgentTaskResumeRequest(
                    taskId=task_id,
                    taskType=text(current.get("task_type")) or "planning_task",
                    input=json_object(current.get("input_json")),
                    threadId=text(current.get("python_thread_id")) or task_id,
                    reviewType=text(review.get("review_type")) or "PLAN",
                    decision=text(review.get("status")) or "APPROVED",
                    decisionPayload=decision_payload,
                )
                resume_unified_agent(request, gateway)
                return True
            except Exception as exc:
                # 不泄露模型、数据库或资料正文；任务终态可由前端轮询和 SSE 获取。
                logger.exception("Python Agent worker 执行失败: taskId=%s", task_id)
                self._service.mark_worker_failure(task_id, "AGENT_PYTHON_UNEXPECTED_ERROR", f"Python Agent worker 执行失败：{exc.__class__.__name__}")
                return True


def main() -> None:
    """启动独立的 durable worker，必须由统一启动入口监督。"""
    load_runtime_config(parse_args(None))
    if not agent_worker_enabled():
        raise RuntimeError("AI_AGENT_WORKER_ENABLED 未开启，已拒绝启动 Agent worker")
    worker = AgentTaskWorker()
    run_forever(worker)


def run_forever(worker: AgentTaskWorker, *, sleep: Any = time.sleep) -> None:
    """持续轮询 PostgreSQL；收到退出信号后完成当前轮并结束。"""
    running = True

    def stop(_signum: int, _frame: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    while running:
        try:
            worker.process_available_tasks(worker_batch_size())
        except Exception:
            # PostgreSQL 临时不可用时保留进程并在下一轮重新连接，避免 supervisor 留下静默退出的 worker。
            logger.exception("Agent worker 领取任务失败，将在下一轮重试")
        sleep(worker_poll_seconds())


def worker_batch_size() -> int:
    """读取每轮任务上限，避免启动时一次处理无限历史恢复任务。"""
    return bounded_int(os.getenv("AGENT_WORKER_BATCH_SIZE"), default=4, lower=1, upper=32)


def worker_poll_seconds() -> float:
    """读取 worker 空闲轮询间隔，非法值使用一秒。"""
    try:
        value = float(os.getenv("AI_AGENT_WORKER_POLL_INTERVAL_SECONDS", os.getenv("AGENT_WORKER_POLL_SECONDS", "1")))
    except ValueError:
        return 1.0
    return max(0.1, min(value, 30.0))


def json_object(value: Any) -> dict[str, Any]:
    """兼容 PostgreSQL JSONB/TEXT 和内存仓储的任务、审批字段。"""
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def text(value: Any) -> str:
    """读取非空文本字段。"""
    return value.strip() if isinstance(value, str) else ""


def truthy(value: str) -> bool:
    """解析开关环境变量。"""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def agent_worker_enabled() -> bool:
    """与统一启动配置的 `AI_AGENT_WORKER_ENABLED` 保持一致。"""
    return truthy(os.getenv("AI_AGENT_WORKER_ENABLED", os.getenv("AGENT_WORKER_ENABLED", "false")))


def bounded_int(value: str | None, *, default: int, lower: int, upper: int) -> int:
    """解析并限制 worker 数值配置。"""
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        return default
    return max(lower, min(parsed, upper))


if __name__ == "__main__":
    main()
