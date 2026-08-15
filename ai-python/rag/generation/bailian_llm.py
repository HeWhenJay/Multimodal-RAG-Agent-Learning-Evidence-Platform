from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from app.core.io_concurrency import AsyncModelHttpClientPool, async_model_http_pool, run_llm_io
from app.schemas.rag import Evidence
from rag.core.source_references import evidence_source_label
from rag.observability.model_logging import log_model_call
from rag.observability.process_logger import logged_rag_method, process_event
from prompts.rag import rag_answer_system_prompt, rag_answer_user_prompt


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
        async_http_pool: AsyncModelHttpClientPool | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self.base_url = (base_url or os.getenv("RAG_LLM_BASE_URL") or DEFAULT_CHAT_BASE_URL).rstrip("/")
        self.model = model or os.getenv("RAG_LLM_MODEL") or DEFAULT_CHAT_MODEL
        self.provider = (provider or os.getenv("RAG_ANSWER_PROVIDER") or "auto").strip().lower()
        self.timeout_seconds = timeout_seconds or float(os.getenv("RAG_LLM_TIMEOUT_SECONDS", "45"))
        self.async_http_pool = async_http_pool or async_model_http_pool

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

    @logged_rag_method("query.answer", "bailian_answer_async", "异步执行百炼或本地回答生成")
    async def generate_async(self, question: str, evidences: list[Evidence]) -> GeneratedAnswer:
        """在 FastAPI 事件循环中直接等待异步 HTTP，并保持同步入口的降级语义。"""
        process_event(
            stage="query.answer",
            action="answer_select_provider",
            message="已选择回答生成提供方",
            context={
                "provider": "dashscope" if self.should_call_dashscope else "local",
                "evidenceCount": len(evidences),
                "ioMode": "async-http",
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
            content = await self._call_chat_async(question, evidences)
            return GeneratedAnswer(
                answer=append_evidence_reference_summary(content, evidences),
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

        payload = self._payload(question, evidences)
        headers = self._headers()
        with log_model_call(
            stage="query.answer",
            action="bailian_chat",
            model_name=self.model,
            event="基于 evidence 生成回答",
            extra_context={"evidenceCount": len(evidences), "questionLength": len(question)},
            recoverable=True,
            fallback_message=f"使用 {self.model} 模型完成基于 evidence 生成回答事件失败，已降级到本地证据摘要继续处理",
        ):
            def request_completion():
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    return client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)

            response = run_llm_io(request_completion)
        return self._response_content(response)

    @logged_rag_method("query.answer", "bailian_chat_call_async", "异步调用百炼回答生成接口")
    async def _call_chat_async(self, question: str, evidences: list[Evidence]) -> str:
        """复用生命周期级 AsyncClient，不占用 llm-io 线程等待网络响应。"""
        with log_model_call(
            stage="query.answer",
            action="bailian_chat_async",
            model_name=self.model,
            event="基于 evidence 异步生成回答",
            extra_context={"evidenceCount": len(evidences), "questionLength": len(question)},
            recoverable=True,
            fallback_message=f"使用 {self.model} 模型完成异步回答事件失败，已降级到本地证据摘要继续处理",
        ):
            response = await self.async_http_pool.request(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=self._headers(),
                json=self._payload(question, evidences),
                timeout_seconds=self.timeout_seconds,
            )
        return self._response_content(response)

    def _payload(self, question: str, evidences: list[Evidence]) -> dict[str, Any]:
        """构造同步与异步入口共用的 Chat Completions 请求体。"""
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": rag_answer_system_prompt(),
                },
                {
                    "role": "user",
                    "content": build_prompt(question, evidences),
                },
            ],
            "temperature": float(os.getenv("RAG_LLM_TEMPERATURE", "0.2")),
        }

    def _headers(self) -> dict[str, str]:
        """构造不写入日志的百炼鉴权请求头。"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _response_content(self, response: Any) -> str:
        """统一映射限流、HTTP 错误、空响应和兼容响应结构。"""
        if response.status_code == 429:
            retry_after = str(response.headers.get("Retry-After") or "").strip()
            retry_hint = f"，建议 {retry_after} 秒后重试" if retry_after else ""
            raise RuntimeError(f"百炼回答生成触发限流{retry_hint}")
        if response.status_code >= 400:
            raise RuntimeError(f"百炼回答生成 HTTP {response.status_code} {response.text[:500]}")
        data = response.json()
        content = extract_message_content(data).strip()
        if not content:
            raise RuntimeError("百炼返回空回答")
        return content


def generate_grounded_answer(question: str, evidences: list[Evidence]) -> GeneratedAnswer:
    """生成带证据引用约束的回答，生产优先走百炼，测试可本地降级。"""
    return BailianChatClient().generate(question, evidences)


async def generate_grounded_answer_async(question: str, evidences: list[Evidence]) -> GeneratedAnswer:
    """供异步 FastAPI 查询链直接调用共享连接池生成回答。"""
    return await BailianChatClient().generate_async(question, evidences)


def build_prompt(question: str, evidences: list[Evidence]) -> str:
    """构造 RAG user Prompt，模板正文统一由 prompts 目录维护。"""
    evidence_text = "\n".join(render_evidence(item, index) for index, item in enumerate(evidences, start=1))
    return rag_answer_user_prompt(question, evidence_text)


def render_evidence(item: Evidence, index: int) -> str:
    location_parts = []
    if item.sectionName:
        location_parts.append(f"章节={clean_evidence_location(item.sectionName)}")
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
        f"来源={evidence_source_label(item.sourcePath, item.source)}；分数={item.score:.4f}；片段={item.snippet}"
    )


def deterministic_grounded_answer(question: str, evidences: list[Evidence]) -> str:
    if not evidences:
        return "当前知识库没有检索到足够相关的证据，请先上传或索引学习资料。"
    top = evidences[:3]
    evidence_text = "；".join(
        f"{item.title} / {clean_evidence_location(item.sectionName)} [{item.evidenceId}]"
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
        raw_location = item.sectionName or item.sectionTitle or "全文"
        location = clean_evidence_location(raw_location)
        if item.startTime:
            location = f"{location}，时间={item.startTime}-{item.endTime}" if item.endTime else f"{location}，时间={item.startTime}"
        source = evidence_source_label(item.sourcePath, item.source)
        location_link = build_evidence_location_link(raw_location, source)
        if location_link:
            location = f"[{location}]({location_link})"
        lines.append(f"- [{item.evidenceId}] {item.title}；位置：{location}；来源：{source}；分数：{item.score:.4f}")
    return answer.rstrip() + "\n" + "\n".join(lines)


def build_evidence_location_link(location: str | None, source: str | None) -> str:
    """把 evidence 章节位置映射到浏览器可打开的原始资料 URL。"""
    source_url = normalize_http_source_url(source)
    if not source_url:
        return ""
    anchor = extract_markdown_anchor(location)
    return attach_fragment(source_url, anchor)


def clean_evidence_location(value: str | None) -> str:
    """清洗 evidence 章节位置，避免把原 Markdown 的目录锚点误渲染成本应用链接。"""
    text = (value or "").strip()
    if not text:
        return "全文"
    text = re.sub(r"!\[([^\]]*)]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)]\((?:#[^)]+|https?://[^)]+)\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or "全文"


def extract_markdown_anchor(value: str | None) -> str:
    """提取 Markdown 目录链接里的 hash，用于拼接到真实来源文件 URL。"""
    text = (value or "").strip()
    match = re.search(r"\[[^\]]+]\(([^)]+)\)", text)
    if not match:
        return ""
    href = match.group(1).strip().strip("<>")
    if href.startswith("#"):
        return href[1:]
    fragment_match = re.search(r"#([^#\s]+)$", href)
    return fragment_match.group(1) if fragment_match else ""


def normalize_http_source_url(source: str | None) -> str:
    """只允许浏览器可直接打开的 http(s) 来源作为跳转目标。"""
    value = (source or "").strip()
    if not re.match(r"^https?://", value, flags=re.IGNORECASE):
        return ""
    return value


def attach_fragment(source_url: str, fragment: str) -> str:
    """为来源 URL 附加章节 fragment，保留原有查询参数。"""
    if not fragment:
        return source_url
    base = source_url.split("#", 1)[0]
    return f"{base}#{fragment}"


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
