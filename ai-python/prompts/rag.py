"""RAG 查询改写与答案生成 Prompt。"""

from __future__ import annotations

from typing import Any

from prompts.common import json_prompt


QUERY_EXPANSION_PROMPT_VERSION = "rag-query-expansion-v1"
RAG_ANSWER_PROMPT_VERSION = "rag-answer-v1"


def query_expansion_system_prompt() -> str:
    """返回 Multi-Query 查询改写系统 Prompt。"""
    return (
        "你是学迹智配的 RAG 查询改写器。你的任务是根据用户原问题生成多路检索查询，"
        "帮助 BM25 和向量检索覆盖不同表达、子问题和学习意图。只输出 JSON 字符串数组，"
        "不要输出解释、Markdown 或对象。"
    )


def query_expansion_user_prompt(question: str, count: int, max_length: int) -> str:
    """返回 Multi-Query 查询改写 user Prompt。"""
    return (
        f"用户原问题：{question}\n\n"
        f"请生成 {count} 条用于 RAG 召回的中文查询，必须满足：\n"
        "1. 第一条必须保留用户原问题，不要改写用户的问题边界。\n"
        "2. 其余查询从不同角度补充同义表达、关键概念、步骤方法、例子、对比点或子问题。\n"
        "3. 如果用户是想复习忘记的知识点，优先生成“概念原理、关键步骤/公式、例子应用、易混点对比”类查询。\n"
        "4. 如果用户在问 JD、岗位、招聘或能力缺口，补充岗位要求、技能栈、能力差距和项目匹配类查询。\n"
        "5. 如果用户在问简历、resume 或项目经历，补充简历证据、项目亮点、技术细节和量化成果类查询。\n"
        "6. 不要机械追加“关键证据”或“学习资料 笔记”；每条查询应能独立用于检索。\n"
        f"7. 每条不超过 {max_length} 个字符，只输出 JSON 字符串数组。"
    )


def rag_answer_system_prompt() -> str:
    """返回 RAG grounded answer 系统 Prompt。"""
    return (
        "你是学迹智配的 RAG 回答生成器。只能根据用户提供的 evidence 回答，"
        "不得编造 evidence 中不存在的事实。回答必须使用中文，并保留引用标记，"
        "引用格式为 [evidenceId]。如果 evidence 不足，明确说明缺口和需要补充的资料。"
    )


def rag_answer_user_prompt(question: str, evidence_text: str) -> str:
    """返回 RAG grounded answer user Prompt。"""
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


def prompt_payload_text(payload: dict[str, Any]) -> str:
    """为需要结构化 JSON 上下文的 Prompt 提供统一格式。"""
    return json_prompt(payload)
