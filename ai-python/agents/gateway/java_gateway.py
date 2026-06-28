from __future__ import annotations

import logging
from typing import Any

import httpx

from app.schemas.agent import AgentTaskEvent

logger = logging.getLogger(__name__)


def agent_log(message: str, **fields: object) -> None:
    """输出 Python Agent 到 Java Gateway 的关键链路日志。"""
    suffix = " ".join(f"{key}={value}" for key, value in fields.items())
    text = f"Agent链路 | {message}" + (f" | {suffix}" if suffix else "")
    logger.info(text)
    print(text, flush=True)


class JavaAgentGatewayClient:
    """通过 Java 内部接口执行工具并回写任务事件。"""

    def __init__(
        self,
        java_tool_gateway_base_url: str,
        callback_url: str,
        internal_token: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.java_tool_gateway_base_url = java_tool_gateway_base_url.rstrip("/")
        self.callback_url = callback_url
        self.internal_token = internal_token
        self.timeout_seconds = timeout_seconds

    def execute_read_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        """调用 Java Read Tool Gateway，禁止 Python Agent 直连 RAG 内部接口。"""
        url = f"{self.java_tool_gateway_base_url}/api/internal/agent/tools/read"
        agent_log(
            "调用 Java 只读工具",
            taskId=payload.get("taskId"),
            toolCallId=payload.get("toolCallId"),
            toolName=payload.get("toolName"),
        )
        return self._post_json(url, payload)

    def execute_mutation_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        """调用 Java Mutation Tool Gateway，变更前由 Java 二次校验审批和幂等键。"""
        url = f"{self.java_tool_gateway_base_url}/api/internal/agent/tools/mutation/execute"
        agent_log(
            "调用 Java 变更工具",
            taskId=payload.get("taskId"),
            toolCallId=payload.get("toolCallId"),
            toolName=payload.get("toolName"),
        )
        return self._post_json(url, payload)

    def publish_event(self, event: AgentTaskEvent) -> None:
        """向 Java 回写任务状态、工具观察和最终结果。"""
        payload = event.model_dump(by_alias=True, exclude_none=True)
        agent_log(
            "回写 Java 任务事件",
            taskId=self._task_id_from_callback(),
            eventType=payload.get("eventType"),
            status=payload.get("status"),
        )
        self._post_json(self.callback_url, payload)

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if "/internal/rag/" in url:
            raise RuntimeError("Python Agent 不允许直连 Python RAG 内部接口")
        headers = {"X-Agent-Internal-Token": self.internal_token}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            if not response.content:
                return {}
            result = response.json()
            agent_log(
                "Java 调用响应",
                statusCode=response.status_code,
                resultStatus=result.get("status") if isinstance(result, dict) else None,
                accepted=result.get("accepted") if isinstance(result, dict) else None,
            )
            return result

    def _task_id_from_callback(self) -> str:
        """从回调 URL 中提取任务 ID，仅用于日志排障。"""
        marker = "/tasks/"
        if marker not in self.callback_url:
            return ""
        tail = self.callback_url.split(marker, 1)[1]
        return tail.split("/", 1)[0]
