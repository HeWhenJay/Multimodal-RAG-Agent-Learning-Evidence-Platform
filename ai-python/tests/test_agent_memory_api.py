"""纯 Python Agent 记忆状态机测试。"""

from __future__ import annotations

import pytest

from app.agent_runtime.repository import InMemoryAgentRepository
from app.agent_runtime.service import AgentBusinessError, AgentRuntimeService


def test_explicit_memory_is_activated_and_only_owner_can_retrieve() -> None:
    """显式记忆激活后可作为当前用户上下文，其他用户不可读取。"""
    service = AgentRuntimeService(InMemoryAgentRepository())
    created = service.create_memory(
        "7",
        {
            "memoryType": "PREFERENCE",
            "namespace": "answer_style",
            "scopeType": "USER",
            "subjectKey": "evidence_first",
            "content": "回答先给结论，再列出可追溯 evidence。",
            "importance": 0.9,
        },
    )

    assert created["status"] == "ACTIVE"
    assert service.memory_context("7", "回答 evidence", 3)[0]["memoryId"] == created["id"]
    with pytest.raises(AgentBusinessError, match="AGENT_MEMORY_NOT_FOUND"):
        service.get_memory(created["id"], "8")


def test_pending_candidate_requires_confirmation_before_default_retrieval() -> None:
    """Agent 候选保持待审状态，不会在确认前自动注入下一次任务。"""
    service = AgentRuntimeService(InMemoryAgentRepository())
    candidate = service.create_memory(
        "7",
        {
            "memoryType": "EPISODIC",
            "namespace": "agent_task",
            "scopeType": "USER",
            "subjectKey": "recent_goal",
            "content": "用户正在准备 RAG 相关实习。",
        },
        pending_review=True,
    )

    assert candidate["status"] == "PENDING_REVIEW"
    assert service.memory_context("7", "RAG 实习", 3) == []
    confirmed = service.confirm_memory(candidate["id"], "7")
    assert confirmed["status"] == "ACTIVE"
    assert service.memory_context("7", "RAG 实习", 3)[0]["memoryId"] == candidate["id"]


def test_memory_patch_creates_new_version_and_blocks_scope_expansion() -> None:
    """修改会生成新版本，且不能从 TASK 作用域扩展到 USER。"""
    repository = InMemoryAgentRepository()
    service = AgentRuntimeService(repository)
    original = service.create_memory(
        "7",
        {
            "memoryType": "PROJECT",
            "namespace": "project_context",
            "scopeType": "TASK",
            "scopeId": "task-1",
            "subjectKey": "rag_design",
            "content": "使用 RRF 融合多路检索。",
        },
    )
    with pytest.raises(AgentBusinessError, match="AGENT_MEMORY_SCOPE_ESCALATION"):
        service.patch_memory(original["id"], "7", {"scopeType": "USER"})

    refined = service.patch_memory(original["id"], "7", {"content": "使用 Multi-Query、BM25、向量检索和 RRF 融合。"})
    assert refined["id"] != original["id"]
    assert refined["status"] == "ACTIVE"
    assert repository.memories[original["id"]]["status"] == "SUPERSEDED"
    assert repository.memory_versions[-1]["previous_memory_id"] == original["id"]


def test_memory_delete_erases_text_and_prevents_retrieval() -> None:
    """删除正文后不会再被默认记忆检索返回。"""
    service = AgentRuntimeService(InMemoryAgentRepository())
    memory = service.create_memory(
        "7",
        {
            "memoryType": "PREFERENCE",
            "namespace": "privacy",
            "scopeType": "USER",
            "subjectKey": "delete_me",
            "content": "这段内容随后应被擦除。",
        },
    )
    deleted = service.delete_memory(memory["id"], "7")

    assert deleted["status"] == "DELETED"
    assert deleted["content"] == "[已删除]"
    assert service.memory_context("7", "擦除", 3) == []
