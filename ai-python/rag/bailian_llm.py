from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from app.schemas.rag import Evidence
from rag.process_logger import logged_rag_method, process_event


DEFAULT_CHAT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_CHAT_MODEL = "qwen-plus"


@dataclass(frozen=True)
class GeneratedAnswer:
    answer: str
    provider: str
    model: str
    fallback_reason: str | None = None

    def diagnostics(self) -> dict[str, str]:
        result = {
            "answerProvider": self.provider,
            "answerModel": self.model,
        }
        if self.fallback_reason:
            result["answerFallbackReason"] = self.fallback_reason
        return result


class BailianChatClient:
    """百炼 OpenAI 兼容 Chat Completions 客户端。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.base_url = (base_url or os.getenv("RAG_LLM_BASE_URL") or DEFAULT_CHAT_BASE_URL).rstrip("/")
        self.model = model or os.getenv("RAG_LLM_MODEL") or DEFAULT_CHAT_MODEL
        self.provider = (provider or os.getenv("RAG_ANSWER_PROVIDER") or "auto").strip().lower()
        self.timeout_seconds = timeout_seconds or float(os.getenv("RAG_LLM_TIMEOUT_SECONDS", "45"))

    @property
    def should_call_dashscope(self) -> bool:
        if self.provider == "local":
            return False
        if self.provider == "dashscope":
            return True
        return bool(self.api_key)

    @logged_rag_method("query.answer", "bailian_answer", "执行百炼或本地回答生成")
    def generate(self, question: str, evidences: list[Evidence]) -> GeneratedAnswer:
        process_event(
            stage="query.answer",
            action="answer_select_provider",
            message="已选择回答生成提供方",
            context={
                "provider": "dashscope" if self.should_call_dashscope else "local",
                "evidenceCount": len(evidences),
            },
        )
        if not evidences:
            return GeneratedAnswer(
                answer="当前知识库没有检索到足够相关的证据，请先上传或索引学习资料。",
                provider="local",
                model="deterministic-grounded-answer",
            )
        if not self.should_call_dashscope:
            return GeneratedAnswer(
                answer=append_evidence_reference_summary(deterministic_grounded_answer(question, evidences), evidences),
                provider="local",
                model="deterministic-grounded-answer",
            )
        if not self.api_key:
            return GeneratedAnswer(
                answer=append_evidence_reference_summary(deterministic_grounded_answer(question, evidences), evidences),
                provider="local",
                model="deterministic-grounded-answer",
                fallback_reason="DASHSCOPE_API_KEY 未配置",
            )
        try:
            return GeneratedAnswer(
                answer=append_evidence_reference_summary(self._call_chat(question, evidences), evidences),
                provider="dashscope",
                model=self.model,
            )
        except Exception as exc:
            return GeneratedAnswer(
                answer=append_evidence_reference_summary(deterministic_grounded_answer(question, evidences), evidences),
                provider="local",
                model="deterministic-grounded-answer",
                fallback_reason=f"百炼回答生成失败: {exc}",
            )

    @logged_rag_method("query.answer", "bailian_chat_call", "调用百炼回答生成接口")
    def _call_chat(self, question: str, evidences: list[Evidence]) -> str:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("使用百炼 LLM 需要安装 httpx 依赖") from exc

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是学迹智配的 RAG 回答生成器。只能根据用户提供的 evidence 回答，"
                        "不得编造 evidence 中不存在的事实。回答必须使用中文，并保留引用标记，"
                        "引用格式为 [evidenceId]。如果 evidence 不足，明确说明缺口和需要补充的资料。"
                    ),
                },
                {
                    "role": "user",
                    "content": build_prompt(question, evidences),
                },
            ],
            "temperature": float(os.getenv("RAG_LLM_TEMPERATURE", "0.2")),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        content = extract_message_content(data).strip()
        if not content:
            raise RuntimeError("百炼返回空回答")
        return content


def generate_grounded_answer(question: str, evidences: list[Evidence]) -> GeneratedAnswer:
    """生成带证据引用约束的回答，生产优先走百炼，测试可本地降级。"""
    return BailianChatClient().generate(question, evidences)


def build_prompt(question: str, evidences: list[Evidence]) -> str:
    evidence_text = "\n".join(render_evidence(item, index) for index, item in enumerate(evidences, start=1))
    return (
        f"用户问题：{question}\n\n"
        "可用 evidence：\n"
        f"{evidence_text}\n\n"
        "请输出：\n"
        "1. 直接回答用户问题。\n"
        "2. 对每个关键判断追加 [evidenceId] 引用。\n"
        "3. 如果包含视频 evidence，写出时间范围并提醒可从证据卡片播放定位。\n"
        "4. 如果证据不足，不要猜测，列出还需要上传的资料。"
    )


def render_evidence(item: Evidence, index: int) -> str:
    location_parts = []
    if item.sectionName:
        location_parts.append(f"章节={item.sectionName}")
    if item.pageIndex is not None:
        location_parts.append(f"页码={item.pageIndex}")
    if item.slideIndex is not None:
        location_parts.append(f"幻灯片={item.slideIndex}")
    if item.startTime:
        time_range = f"{item.startTime}-{item.endTime}" if item.endTime else item.startTime
        location_parts.append(f"视频时间={time_range}")
    location = "，".join(location_parts) or "全文"
    return (
        f"{index}. evidenceId={item.evidenceId}；资料={item.title}；位置={location}；"
        f"来源={item.sourcePath or item.source}；分数={item.score:.4f}；片段={item.snippet}"
    )


def deterministic_grounded_answer(question: str, evidences: list[Evidence]) -> str:
    if not evidences:
        return "当前知识库没有检索到足够相关的证据，请先上传或索引学习资料。"
    top = evidences[:3]
    evidence_text = "；".join(
        f"{item.title} / {item.sectionName} [{item.evidenceId}]"
        for item in top
    )
    video_evidences = [item for item in top if item.startTime]
    video_text = ""
    if video_evidences:
        locations = "；".join(
            f"{item.title} {item.startTime}-{item.endTime}" if item.endTime else f"{item.title} {item.startTime}"
            for item in video_evidences
        )
        video_text = f"视频证据命中：{locations}，可在证据卡片点击“从这里播放”定位。"
    return (
        f"针对“{question}”，已从个人学习证据库检索到 {len(evidences)} 条相关证据。"
        f"{video_text}"
        f"优先参考：{evidence_text}。请基于这些证据整理正式回答，并保留方括号中的 evidenceId 引用。"
    )


def append_evidence_reference_summary(answer: str, evidences: list[Evidence]) -> str:
    """程序化追加证据引用摘要，确保来源、位置和分数不会完全依赖模型生成。"""
    if not evidences:
        return answer
    if "证据引用：" in answer:
        return answer
    lines = ["", "证据引用："]
    for item in evidences[:5]:
        location = item.sectionName or item.sectionTitle or "全文"
        if item.startTime:
            location = f"{location}，时间={item.startTime}-{item.endTime}" if item.endTime else f"{location}，时间={item.startTime}"
        source = item.sourcePath or item.source or "未知来源"
        lines.append(f"- [{item.evidenceId}] {item.title}；位置：{location}；来源：{source}；分数：{item.score:.4f}")
    return answer.rstrip() + "\n" + "\n".join(lines)


def extract_message_content(data: dict[str, Any]) -> str:
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
                elif item.get("type") in {"text", "output_text"} and isinstance(item.get("content"), str):
                    parts.append(item["content"])
        return "\n".join(parts)
    return str(content)
