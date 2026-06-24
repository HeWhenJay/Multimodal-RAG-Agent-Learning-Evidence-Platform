from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from rag.bailian_llm import DEFAULT_CHAT_BASE_URL, DEFAULT_CHAT_MODEL, extract_message_content
from rag.model_logging import log_model_call
from rag.process_logger import logged_rag_method, process_event


DEFAULT_QUERY_EXPANSION_MODEL = DEFAULT_CHAT_MODEL
MAX_QUERY_LENGTH = 120


@dataclass(frozen=True)
class QueryExpansionResult:
    """保存 Multi-Query 生成结果和可观测诊断信息。"""

    queries: list[str]
    provider: str
    model: str
    requested_count: int
    fallback_reason: str | None = None

    def diagnostics(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "queryExpansionProvider": self.provider,
            "queryExpansionModel": self.model,
            "queryExpansionFallbackUsed": bool(self.fallback_reason),
            "queryExpansionRequestedCount": self.requested_count,
            "queryExpansionReturnedCount": len(self.queries),
        }
        if self.fallback_reason:
            result["queryExpansionFallbackReason"] = self.fallback_reason
        return result


class BailianQueryExpansionClient:
    """百炼 OpenAI 兼容 Chat Completions 查询扩展客户端。"""

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
        self.base_url = (
            base_url
            or os.getenv("RAG_QUERY_EXPANSION_BASE_URL")
            or os.getenv("RAG_LLM_BASE_URL")
            or DEFAULT_CHAT_BASE_URL
        ).rstrip("/")
        self.model = model or os.getenv("RAG_QUERY_EXPANSION_MODEL") or os.getenv("RAG_LLM_MODEL") or DEFAULT_QUERY_EXPANSION_MODEL
        self.provider = (provider or os.getenv("RAG_QUERY_EXPANSION_PROVIDER") or "auto").strip().lower()
        self.timeout_seconds = timeout_seconds or float(os.getenv("RAG_QUERY_EXPANSION_TIMEOUT_SECONDS", "30"))

    @property
    def selected_provider(self) -> str:
        """根据配置和 Key 可用性选择查询扩展提供方。"""
        if self.provider == "local":
            return "local"
        if self.provider == "dashscope":
            return "dashscope"
        if self.provider == "auto":
            return "dashscope" if self.api_key else "local"
        return "local"

    @logged_rag_method("query.expand", "multi_query_expand", "生成 Multi-Query 查询变体")
    def expand(self, question: str, *, count: int = 5) -> QueryExpansionResult:
        """生成多路召回查询；生产优先走百炼，失败时保留本地降级。"""
        requested_count = normalize_query_count(count)
        selected_provider = self.selected_provider
        process_event(
            stage="query.expand",
            action="query_expansion_select_provider",
            message="已选择 Multi-Query 生成提供方",
            context={
                "provider": selected_provider,
                "configuredProvider": self.provider,
                "model": self.model if selected_provider == "dashscope" else "deterministic-query-expansion",
                "requestedCount": requested_count,
            },
        )
        if self.provider not in {"auto", "local", "dashscope"}:
            return self._local_result(question, requested_count, fallback_reason=f"未知 RAG_QUERY_EXPANSION_PROVIDER: {self.provider}")
        if selected_provider == "local":
            fallback_reason = "DASHSCOPE_API_KEY 未配置，使用本地查询改写" if self.provider == "auto" and not self.api_key else None
            return self._local_result(question, requested_count, fallback_reason=fallback_reason)
        if not self.api_key:
            return self._local_result(question, requested_count, fallback_reason="DASHSCOPE_API_KEY 未配置")
        try:
            queries = self._call_chat(question, requested_count)
            return QueryExpansionResult(
                queries=queries,
                provider="dashscope",
                model=self.model,
                requested_count=requested_count,
            )
        except Exception as exc:
            return self._local_result(question, requested_count, fallback_reason=f"百炼 Multi-Query 生成失败: {exc}")

    def _local_result(self, question: str, requested_count: int, *, fallback_reason: str | None = None) -> QueryExpansionResult:
        return QueryExpansionResult(
            queries=local_expand_queries(question, requested_count),
            provider="local",
            model="deterministic-query-expansion",
            requested_count=requested_count,
            fallback_reason=fallback_reason,
        )

    @logged_rag_method("query.expand", "bailian_multi_query_call", "调用百炼生成 Multi-Query")
    def _call_chat(self, question: str, count: int) -> list[str]:
        try:
            import httpx
        except ImportError as exc:
            raise RuntimeError("使用百炼 Multi-Query 需要安装 httpx 依赖") from exc

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是学迹智配的 RAG 查询改写器。你的任务是根据用户原问题生成多路检索查询，"
                        "帮助 BM25 和向量检索覆盖不同表达、子问题和学习意图。只输出 JSON 字符串数组，"
                        "不要输出解释、Markdown 或对象。"
                    ),
                },
                {
                    "role": "user",
                    "content": build_query_expansion_prompt(question, count),
                },
            ],
            "temperature": float(os.getenv("RAG_QUERY_EXPANSION_TEMPERATURE", "0.3")),
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with log_model_call(
            stage="query.expand",
            action="bailian_multi_query",
            model_name=self.model,
            event="生成 Multi-Query 查询变体",
            extra_context={"questionLength": len(question), "requestedCount": count},
            recoverable=True,
            fallback_message=f"使用 {self.model} 模型完成 Multi-Query 查询变体生成失败，已降级到本地查询改写继续处理",
        ):
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
        if response.status_code >= 400:
            raise RuntimeError(f"HTTP {response.status_code} {response.text[:500]}")
        content = extract_message_content(response.json()).strip()
        queries = parse_query_expansion_content(content, question, count)
        if len(queries) < 2:
            raise RuntimeError("百炼 Multi-Query 响应未包含有效查询变体")
        return queries


def expand_queries_with_diagnostics(question: str, *, count: int = 5) -> QueryExpansionResult:
    """生成带诊断信息的查询变体，供检索链路记录 provider/model/fallback。"""
    return BailianQueryExpansionClient().expand(question, count=count)


def expand_queries(question: str) -> list[str]:
    """兼容旧调用方的 Multi-Query 入口。"""
    return expand_queries_with_diagnostics(question).queries


def build_query_expansion_prompt(question: str, count: int) -> str:
    """构造查询扩展提示词，避免固定拼接 evidence 后缀。"""
    base = normalize_query_text(question)
    return (
        f"用户原问题：{base}\n\n"
        f"请生成 {count} 条用于 RAG 召回的中文查询，必须满足：\n"
        "1. 第一条必须保留用户原问题，不要改写用户的问题边界。\n"
        "2. 其余查询从不同角度补充同义表达、关键概念、步骤方法、例子、对比点或子问题。\n"
        "3. 如果用户是想复习忘记的知识点，优先生成“概念原理、关键步骤/公式、例子应用、易混点对比”类查询。\n"
        "4. 如果用户在问 JD、岗位、招聘或能力缺口，补充岗位要求、技能栈、能力差距和项目匹配类查询。\n"
        "5. 如果用户在问简历、resume 或项目经历，补充简历证据、项目亮点、技术细节和量化成果类查询。\n"
        "6. 不要机械追加“关键证据”或“学习资料 笔记”；每条查询应能独立用于检索。\n"
        f"7. 每条不超过 {MAX_QUERY_LENGTH} 个字符，只输出 JSON 字符串数组。"
    )


def parse_query_expansion_content(content: str, original_question: str, count: int) -> list[str]:
    """解析 LLM 返回的 JSON 数组或编号列表，并做去重清洗。"""
    raw_items = parse_json_query_items(content)
    if not raw_items:
        raw_items = parse_numbered_query_items(content)
    if not raw_items:
        raw_items = [original_question]
    normalized = normalize_query_list([original_question, *raw_items], count)
    if len(normalized) < min(count, 3):
        normalized = normalize_query_list([*normalized, *local_expand_queries(original_question, count)], count)
    return normalized


def parse_json_query_items(content: str) -> list[str]:
    """从 JSON 数组或包含 queries 字段的对象中读取查询列表。"""
    candidates = [content.strip()]
    bracket_match = re.search(r"\[[\s\S]*]", content)
    if bracket_match:
        candidates.append(bracket_match.group(0))
    fenced_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", content, flags=re.IGNORECASE)
    if fenced_match:
        candidates.append(fenced_match.group(1).strip())
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return [str(item) for item in data if isinstance(item, (str, int, float))]
        if isinstance(data, dict):
            for key in ("queries", "expandedQueries", "queryVariants"):
                value = data.get(key)
                if isinstance(value, list):
                    return [str(item) for item in value if isinstance(item, (str, int, float))]
    return []


def parse_numbered_query_items(content: str) -> list[str]:
    """兼容模型返回的编号列表。"""
    lines = []
    for line in content.splitlines():
        text = re.sub(r"^\s*(?:[-*]|\d+[.)、]|[（(]\d+[）)])\s*", "", line).strip()
        if text:
            lines.append(text)
    return lines


def local_expand_queries(question: str, count: int = 5) -> list[str]:
    """本地确定性降级：按问题意图生成较通用的查询变体。"""
    base = normalize_query_text(question)
    if not base:
        return []
    lower_base = base.lower()
    variants = [base]
    if contains_any(lower_base, ["jd", "岗位", "招聘", "能力", "要求", "缺口"]):
        variants.extend([
            f"{base} 岗位要求 技能栈",
            f"{base} 能力缺口 项目匹配",
            f"{base} 学习路径 面试准备",
        ])
    elif contains_any(lower_base, ["简历", "resume", "项目", "经历"]):
        variants.extend([
            f"{base} 简历项目 经历证据",
            f"{base} 技术亮点 量化成果",
            f"{base} 项目难点 解决方案",
        ])
    elif contains_any(lower_base, ["复习", "忘", "忘记", "回顾", "知识点", "概念", "原理", "是什么"]):
        variants.extend([
            f"{base} 核心概念 原理",
            f"{base} 关键步骤 公式 示例",
            f"{base} 易混点 对比 总结",
        ])
    elif contains_any(lower_base, ["区别", "对比", "比较", "不同", "差异"]):
        variants.extend([
            f"{base} 差异对比",
            f"{base} 适用场景 优缺点",
            f"{base} 典型例子",
        ])
    elif contains_any(lower_base, ["怎么", "如何", "步骤", "实现", "做法"]):
        variants.extend([
            f"{base} 步骤 方法",
            f"{base} 示例 场景",
            f"{base} 注意事项 常见问题",
        ])
    elif contains_any(lower_base, ["报错", "错误", "异常", "失败", "bug"]):
        variants.extend([
            f"{base} 原因分析",
            f"{base} 排查步骤",
            f"{base} 修复方案",
        ])
    else:
        variants.extend([
            f"{base} 核心概念 原理",
            f"{base} 方法步骤 示例",
            f"{base} 总结 对比 应用场景",
        ])
    return normalize_query_list(variants, count)


def normalize_query_list(items: list[str], count: int) -> list[str]:
    """清洗、去重并限制查询数量。"""
    normalized: list[str] = []
    for item in items:
        text = normalize_query_text(item)
        if not text or text in normalized:
            continue
        normalized.append(text[:MAX_QUERY_LENGTH])
        if len(normalized) >= normalize_query_count(count):
            break
    return normalized


def normalize_query_text(value: str) -> str:
    """清理查询文本中的编号、引号和多余空白。"""
    text = str(value or "").strip().strip("\"'“”‘’")
    text = re.sub(r"^\s*(?:[-*]|\d+[.)、]|[（(]\d+[）)])\s*", "", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_query_count(count: int) -> int:
    """限制 Multi-Query 数量，避免召回链路成本失控。"""
    return max(3, min(int(count or 5), 5))


def contains_any(text: str, terms: list[str]) -> bool:
    """判断问题是否包含某类意图词。"""
    return any(term in text for term in terms)


def format_query_variants(queries: list[str]) -> str:
    """格式化 Multi-Query 改写结果，供前端展示每个查询变体。"""
    return "；".join(f"{index}. {query}" for index, query in enumerate(queries, start=1))


def format_query_expansion_detail(result: QueryExpansionResult) -> str:
    """格式化查询扩展详情，展示提供方、模型和查询列表。"""
    fallback = f"；降级原因：{result.fallback_reason}" if result.fallback_reason else ""
    return (
        f"生成方式：{result.provider}；模型：{result.model}{fallback}；"
        f"查询变体：{format_query_variants(result.queries)}"
    )
