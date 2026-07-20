"""Agent 公开运行时共用的常量和轻量记录工具。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


TASK_TYPES = {"pure_read_query", "planning_task", "mutation_task"}
TERMINAL_TASK_STATUSES = {"COMPLETED", "CANCELED", "FAILED"}
MEMORY_SCOPE_RANK = {
    "USER": 0,
    "PROJECT": 1,
    "MATERIAL": 2,
    "TASK": 3,
    "SESSION": 3,
}
DEFAULT_TOOLS: list[dict[str, Any]] = [
    {
        "toolName": "rag_query_probe_non_persistent",
        "toolType": "READ",
        "requiresReview": False,
        "approvalType": None,
        "stage": 1,
        "description": "在当前用户可见资料中执行只读 RAG 检索并返回 evidence。",
    },
    {
        "toolName": "agent_memory_retriever",
        "toolType": "READ",
        "requiresReview": False,
        "approvalType": None,
        "stage": 1,
        "description": "检索当前用户已激活的 Agent 记忆。",
    },
    {
        "toolName": "retrieval_coverage_probe",
        "toolType": "READ",
        "requiresReview": False,
        "approvalType": None,
        "stage": 2,
        "description": "检查资料检索覆盖情况，不修改业务数据。",
    },
    {
        "toolName": "jd_learning_plan_save",
        "toolType": "MUTATION",
        "requiresReview": True,
        "approvalType": "CRUD",
        "stage": 4,
        "description": "保存学习计划草稿，必须经过当前用户审批。",
    },
    {
        "toolName": "resume_revision_save",
        "toolType": "MUTATION",
        "requiresReview": True,
        "approvalType": "CRUD",
        "stage": 4,
        "description": "保存简历改写草稿，必须经过当前用户审批。",
    },
    {
        "toolName": "agent_memory_candidate_save",
        "toolType": "MUTATION",
        "requiresReview": True,
        "approvalType": "CRUD",
        "stage": 7,
        "description": "保存 Agent 记忆候选，必须经过当前用户审批。",
    },
]


def new_id(prefix: str) -> str:
    """生成与既有 `VARCHAR(120)` 表字段兼容的稳定前缀 ID。"""
    return f"{prefix}-{uuid4().hex}"


def utc_now() -> datetime:
    """统一生成带时区时间，避免本地时区混入 PostgreSQL 事实记录。"""
    return datetime.now(timezone.utc)
