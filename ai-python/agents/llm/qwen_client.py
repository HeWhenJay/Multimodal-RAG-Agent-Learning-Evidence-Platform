from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any


DEFAULT_AGENT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@dataclass(frozen=True)
class AgentQwenResult:
    """Agent 节点调用千问后的结构化结果。"""

    data: dict[str, Any]
    provider: str
    model: str

    def diagnostics(self) -> dict[str, str]:
        """返回可记录到状态机的非敏感诊断信息。"""
        return {"provider": self.provider, "model": self.model}


class AgentQwenClient:
    """阿里云百炼 OpenAI 兼容 Chat Completions JSON 客户端。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        temperature: float | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.base_url = (base_url or os.getenv("AGENT_QWEN_BASE_URL") or DEFAULT_AGENT_QWEN_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds or float(os.getenv("AGENT_QWEN_TIMEOUT_SECONDS", "30"))
        self.temperature = temperature if temperature is not None else float(os.getenv("AGENT_QWEN_TEMPERATURE", "0.2"))
        self.enabled = enabled if enabled is not None else os.getenv("AGENT_LLM_ENABLED", "true").strip().lower() != "false"

    @property
    def available(self) -> bool:
        """判断当前是否允许真实调用千问。"""
        return self.enabled and bool(self.api_key)

    def complete_json(self, *, node: str, model: str, system_prompt: str, user_prompt: str) -> AgentQwenResult:
        """调用千问并解析唯一 JSON 对象；失败交由上层 fallback。"""
        if not self.available:
            raise RuntimeError("DASHSCOPE_API_KEY 未配置或 AGENT_LLM_ENABLED=false")
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("使用 Agent Qwen LLM 需要安装 httpx 依赖") from exc

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code} {response.text[:300]}")
        data = response.json()
        content = extract_message_content(data).strip()
        parsed = parse_json_object(content)
        parsed.setdefault("node", node)
        return AgentQwenResult(data=parsed, provider="dashscope", model=model)


def get_agent_qwen_client() -> AgentQwenClient:
    """创建 Agent 默认千问客户端；测试可 monkeypatch 本函数。"""
    return AgentQwenClient()


def agent_qwen_model(role: str) -> str:
    """按节点角色读取模型配置。"""
    env_map = {
        "title": "AGENT_QWEN_TITLE_MODEL",
        "planner": "AGENT_QWEN_PLANNER_MODEL",
        "executor": "AGENT_QWEN_EXECUTOR_MODEL",
        "repair": "AGENT_QWEN_REPAIR_MODEL",
        "acceptance": "AGENT_QWEN_ACCEPTANCE_MODEL",
        "resume": "AGENT_QWEN_RESUME_MODEL",
        "answer": "AGENT_QWEN_ANSWER_MODEL",
        "compression": "AGENT_QWEN_COMPRESSION_MODEL",
    }
    default_map = {
        "title": "qwen-turbo",
        "planner": "qwen-plus",
        "executor": "qwen-turbo",
        "repair": "qwen-turbo",
        "acceptance": "qwen-turbo",
        "resume": "qwen-plus",
        "answer": "qwen-plus",
        "compression": "qwen-plus",
    }
    return os.getenv(env_map[role], default_map[role])


def extract_message_content(data: dict[str, Any]) -> str:
    """兼容 OpenAI Chat Completions 的 message.content 结构。"""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(parts)
    return str(content)


def parse_json_object(content: str) -> dict[str, Any]:
    """从模型返回中解析 JSON 对象。"""
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()
    if not text.startswith("{"):
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError("模型未返回 JSON 对象")
        text = match.group(0)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("模型 JSON 顶层必须是对象")
    return parsed
