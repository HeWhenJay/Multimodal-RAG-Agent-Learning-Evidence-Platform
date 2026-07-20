"""纯 Python Agent 的任务、会话、审批、操作与记忆业务服务。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Any

from app.agent_runtime.models import DEFAULT_TOOLS, MEMORY_SCOPE_RANK, TASK_TYPES, new_id, utc_now
from app.agent_runtime.repository import AgentRepositoryProtocol, PostgresAgentRepository
from app.core.result import BusinessError


class AgentBusinessError(BusinessError):
    """表示可返回给 Agent 工作台的受控业务失败。"""


class AgentRuntimeService:
    """管理所有由 Python 成为权威后的 Agent 业务事实记录。"""

    def __init__(self, repository: AgentRepositoryProtocol | None = None) -> None:
        self._repository = repository or PostgresAgentRepository()

    def create_task(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """从登录用户创建任务并持久化首条用户消息。"""
        task_type = text(payload.get("taskType"))
        if task_type not in TASK_TYPES:
            raise AgentBusinessError("AGENT_VALIDATION_FAILED: 任务类型不合法")
        task_input = payload.get("input") if isinstance(payload.get("input"), dict) else {}
        goal = text(task_input.get("goal")) or text(task_input.get("question"))
        if not goal:
            raise AgentBusinessError("AGENT_VALIDATION_FAILED: 任务目标不能为空")
        folder_id = nullable_text(payload.get("folderId"))
        if folder_id and self._repository.get_folder(folder_id, user_id) is None:
            raise AgentBusinessError("AGENT_FOLDER_NOT_FOUND: 会话文件夹不存在")
        now = utc_now()
        task_id = new_id("agent-task")
        title = text(payload.get("title")) or fallback_title(goal)
        record = {
            "id": task_id,
            "user_id": user_id,
            "task_type": task_type,
            "status": "CREATED",
            "title": title,
            "folder_id": folder_id,
            "input_json": task_input,
            "plan_json": {},
            "draft_json": {},
            "final_json": {},
            "python_thread_id": task_id,
            "error_code": None,
            "error_message": None,
            "created_at": now,
            "updated_at": now,
        }
        created = self._repository.create_task(record)
        self._append_message(
            task_id,
            user_id,
            role="USER",
            message_type="USER_GOAL",
            content=goal,
            payload={"input": task_input},
            source_event_type="TASK_CREATED",
            source_id=task_id,
            dedupe_key=f"task-created:{task_id}",
        )
        return self.task_summary(created)

    def task_record(self, task_id: str) -> dict[str, Any]:
        """供进程内 Worker 使用，读取数据库中的权威任务而不接受浏览器 userId。"""
        task = self._repository.get_task(task_id)
        if task is None:
            raise AgentBusinessError("AGENT_TASK_NOT_FOUND: Agent 任务不存在")
        return task

    def get_task(self, task_id: str, user_id: str) -> dict[str, Any]:
        """返回当前用户拥有的任务完整快照。"""
        task = self._require_task(task_id, user_id)
        messages = [self.message_view(item) for item in self._repository.list_messages(task_id, 30)]
        summary_window_limit = 8
        summaries = [self.summary_view(item) for item in self._repository.list_conversation_summaries(task_id, summary_window_limit)]
        summary_count = self._repository.count_conversation_summaries(task_id)
        oldest = min((int(item["sequenceNo"]) for item in messages if item.get("sequenceNo") is not None), default=None)
        result = self.task_summary(task)
        result.update(
            {
                "toolCalls": [self.tool_call_view(item) for item in self._repository.list_tool_calls(task_id)],
                "reviews": [self.review_view(item) for item in self._repository.list_reviews(task_id)],
                "operations": [self.operation_view(item) for item in self._repository.list_operations(task_id)],
                "messages": messages,
                "summaries": summaries,
                "messageWindowLimit": 30,
                "hasMoreMessagesBefore": bool(oldest and self._repository.has_message_before(task_id, oldest)),
                "summaryWindowLimit": summary_window_limit,
                "hasMoreSummaries": summary_count > len(summaries),
                "summaryCount": summary_count,
            }
        )
        return result

    def list_tasks(self, user_id: str, limit: int | None) -> list[dict[str, Any]]:
        """列出当前用户最近任务，限制范围避免全量会话扫描。"""
        safe_limit = clamp(limit, default=20, lower=1, upper=50)
        return [self.task_summary(item) for item in self._repository.list_tasks(user_id, safe_limit)]

    def list_runnable_task_records(self, limit: int | None = None) -> list[dict[str, Any]]:
        """供耐久 worker 读取待启动或崩溃恢复的权威任务记录。"""
        safe_limit = clamp(limit, default=8, lower=1, upper=32)
        return self._repository.list_runnable_tasks(safe_limit)

    def task_execution_lock(self, task_id: str):
        """返回跨 worker 任务锁，调用方必须在执行完整图期间持有它。"""
        return self._repository.task_execution_lock(task_id)

    def latest_resumable_review(self, task_id: str) -> dict[str, Any] | None:
        """读取最近一条已决定审批，供崩溃恢复时选择统一图恢复入口。"""
        reviews = self._repository.list_reviews(task_id)
        candidates = [item for item in reviews if str(item.get("status") or "") in {"APPROVED", "CHANGES_REQUESTED"}]
        return candidates[-1] if candidates else None

    def list_messages(
        self,
        task_id: str,
        user_id: str,
        before_sequence_no: int | None,
        after_sequence_no: int | None,
        limit: int | None,
    ) -> dict[str, Any]:
        """按稳定 sequence_no 分页读取持久化会话消息。"""
        self._require_task(task_id, user_id)
        safe_limit = clamp(limit, default=30, lower=1, upper=100)
        rows = self._repository.list_messages(task_id, safe_limit, before_sequence_no, after_sequence_no)
        messages = [self.message_view(item) for item in rows]
        oldest = min((int(item["sequenceNo"]) for item in messages if item.get("sequenceNo") is not None), default=None)
        newest = max((int(item["sequenceNo"]) for item in messages if item.get("sequenceNo") is not None), default=None)
        return {
            "taskId": task_id,
            "messages": messages,
            "oldestSequenceNo": oldest,
            "newestSequenceNo": newest,
            "hasMoreBefore": bool(oldest and self._repository.has_message_before(task_id, oldest)),
            "hasMoreAfter": bool(newest and self._repository.has_message_after(task_id, newest)),
            "limit": safe_limit,
        }

    def save_context_summary(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """保存统一图生成的可恢复压缩摘要，并从任务事实记录推导所有者。"""
        task = self.task_record(task_id)
        summary_body = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        now = utc_now()
        summary_text = text(payload.get("summaryText")) or text(summary_body.get("rollingSummary")) or "Agent 会话上下文摘要"
        record = {
            "id": text(payload.get("summaryId")) or new_id("agent-summary"),
            "task_id": task_id,
            "user_id": str(task["user_id"]),
            "summary_type": text(payload.get("summaryType")) or "CONTEXT_COMPRESSION",
            "covered_message_start_id": nullable_text(payload.get("coveredMessageStartId")),
            "covered_message_end_id": nullable_text(payload.get("coveredMessageEndId")),
            "covered_message_count": max(0, int_value(payload.get("coveredMessageCount"), 0)),
            "raw_token_estimate": max(0, int_value(payload.get("rawTokenEstimate"), 0)),
            "compressed_token_estimate": max(0, int_value(payload.get("compressedTokenEstimate"), 0)),
            "summary_json": summary_body,
            "summary_text": summary_text,
            "key_facts_json": payload.get("keyFacts") if isinstance(payload.get("keyFacts"), list) else [],
            "evidence_refs_json": payload.get("evidenceRefs") if isinstance(payload.get("evidenceRefs"), list) else [],
            "compression_model": nullable_text(payload.get("compressionModel")),
            "compression_prompt_version": text(payload.get("compressionPromptVersion")) or "agent-context-compression-v1",
            "compression_version": max(1, int_value(payload.get("compressionVersion"), 1)),
            "status": text(payload.get("status")) or "ACTIVE",
            "diagnostics_json": payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {},
            "created_at": now,
            "updated_at": now,
        }
        return self.summary_view(self._repository.save_conversation_summary(record))

    def list_context_summaries(self, task_id: str, user_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        """读取当前用户可恢复的摘要段，禁止通过任务 ID 越权访问。"""
        self._require_task(task_id, user_id)
        safe_limit = clamp(limit, default=6, lower=1, upper=20)
        return [self.summary_view(item) for item in self._repository.list_conversation_summaries(task_id, safe_limit)]

    def conversation_tree(self, user_id: str, limit_per_folder: int | None) -> dict[str, Any]:
        """按文件夹构造侧边栏会话树，未分类会话作为固定根节点。"""
        safe_limit = clamp(limit_per_folder, default=8, lower=1, upper=50)
        folders = self._repository.list_folders(user_id)
        grouped = []
        for folder in folders:
            conversations = [self.task_summary(item) for item in self._repository.list_tasks(user_id, safe_limit, folder["id"])]
            grouped.append(self.folder_view(folder, conversations, self._folder_task_count(user_id, folder["id"])))
        unfiled_rows = [item for item in self._repository.list_tasks(user_id, 1000) if item.get("folder_id") is None]
        unfiled = {
            "id": None,
            "name": "未分类",
            "sortOrder": None,
            "conversationCount": len(unfiled_rows),
            "conversations": [self.task_summary(item) for item in unfiled_rows[:safe_limit]],
            "createdAt": None,
            "updatedAt": None,
        }
        return {"unfiled": unfiled, "folders": grouped}

    def create_folder(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """创建当前用户私有的会话文件夹。"""
        name = text(payload.get("name"))
        if not name:
            raise AgentBusinessError("AGENT_VALIDATION_FAILED: 文件夹名称不能为空")
        if len(name) > 80:
            raise AgentBusinessError("AGENT_VALIDATION_FAILED: 文件夹名称不能超过 80 个字符")
        now = utc_now()
        folder = self._repository.create_folder(
            {
                "id": new_id("agent-folder"),
                "user_id": user_id,
                "name": name,
                "sort_order": int_value(payload.get("sortOrder"), 0),
                "created_at": now,
                "updated_at": now,
            }
        )
        return self.folder_view(folder, [], 0)

    def update_folder(self, folder_id: str, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """更新当前用户文件夹，并保留资源隔离边界。"""
        folder = self._repository.get_folder(folder_id, user_id)
        if folder is None:
            raise AgentBusinessError("AGENT_FOLDER_NOT_FOUND: 会话文件夹不存在")
        name = text(payload.get("name"))
        if not name:
            raise AgentBusinessError("AGENT_VALIDATION_FAILED: 文件夹名称不能为空")
        updated = self._repository.update_folder(
            folder_id,
            name=name,
            sort_order=int_value(payload.get("sortOrder"), int_value(folder.get("sort_order"), 0)),
            updated_at=utc_now(),
        )
        if updated is None:
            raise AgentBusinessError("AGENT_FOLDER_NOT_FOUND: 会话文件夹不存在")
        conversations = [self.task_summary(item) for item in self._repository.list_tasks(user_id, 8, folder_id)]
        return self.folder_view(updated, conversations, self._folder_task_count(user_id, folder_id))

    def delete_folder(self, folder_id: str, user_id: str) -> None:
        """删除文件夹并让其会话恢复为未分类。"""
        if not self._repository.delete_folder(folder_id, user_id):
            raise AgentBusinessError("AGENT_FOLDER_NOT_FOUND: 会话文件夹不存在")

    def move_conversation(self, task_id: str, user_id: str, folder_id: str | None) -> dict[str, Any]:
        """将当前用户任务移动到其私有文件夹或未分类根节点。"""
        task = self._require_task(task_id, user_id)
        if folder_id and self._repository.get_folder(folder_id, user_id) is None:
            raise AgentBusinessError("AGENT_FOLDER_NOT_FOUND: 会话文件夹不存在")
        updated = self._repository.update_task(task_id, folder_id=folder_id, updated_at=utc_now())
        if updated is None:
            raise AgentBusinessError("AGENT_TASK_NOT_FOUND: Agent 任务不存在")
        return self.task_summary(updated)

    def decide_review(self, task_id: str, review_id: str, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """持久化当前所有者的审批决策，Worker 会据此恢复任务。"""
        task = self._require_task(task_id, user_id)
        review = self._repository.get_review(review_id, task_id)
        if review is None:
            raise AgentBusinessError("AGENT_REVIEW_NOT_FOUND: Agent 审批不存在")
        if review.get("status") != "PENDING":
            raise AgentBusinessError("AGENT_REVIEW_RESOLVED: Agent 审批已处理")
        decision = text(payload.get("decision")).upper()
        if decision not in {"APPROVED", "REJECTED", "CHANGES_REQUESTED"}:
            raise AgentBusinessError("AGENT_VALIDATION_FAILED: 审批决策不合法")
        now = utc_now()
        decision_data = {
            "decision": decision,
            "comment": text(payload.get("comment")),
            "changes": payload.get("changes") if isinstance(payload.get("changes"), dict) else {},
        }
        updated_review = self._repository.update_review(
            review_id,
            status=decision,
            decision_json=decision_data,
            reviewed_by=user_id,
            reviewed_at=now,
            updated_at=now,
        )
        if updated_review is None:
            raise AgentBusinessError("AGENT_REVIEW_NOT_FOUND: Agent 审批不存在")
        next_status = "FAILED" if decision == "REJECTED" else "RUNNING"
        self._repository.update_task(task_id, status=next_status, error_code=None, error_message=None, updated_at=now)
        self._append_message(
            task_id,
            user_id,
            role="USER",
            message_type="REVIEW_DECISION",
            content=review_decision_message(decision, decision_data["comment"]),
            payload={"reviewId": review_id, **decision_data},
            source_event_type="REVIEW_DECIDED",
            source_id=review_id,
            dedupe_key=f"review-decision:{review_id}",
        )
        if decision == "REJECTED":
            self._append_message(
                task_id,
                user_id,
                role="SYSTEM",
                message_type="ERROR",
                content="用户未批准 Agent 继续执行。",
                payload={"errorCode": "AGENT_REVIEW_REJECTED"},
                source_event_type="TASK_FAILED",
                source_id=review_id,
                dedupe_key=f"review-rejected:{review_id}",
            )
        return self.get_task(task_id, user_id)

    def undo_operation(self, operation_id: str, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """撤销当前用户仍在窗口内的已应用操作。"""
        operation = self._repository.get_operation(operation_id, user_id)
        if operation is None:
            raise AgentBusinessError("AGENT_OPERATION_NOT_FOUND: Agent 操作不存在")
        if operation.get("status") == "UNDONE":
            return self.operation_view(operation)
        if operation.get("status") != "APPLIED_UNDOABLE":
            raise AgentBusinessError("AGENT_OPERATION_NOT_UNDOABLE: 当前操作不可撤销")
        deadline = operation.get("undo_deadline")
        if isinstance(deadline, str):
            deadline = parse_datetime(deadline)
        if deadline is not None and deadline < utc_now():
            updated = self._repository.update_operation(operation_id, status="UNDO_EXPIRED", updated_at=utc_now())
            raise AgentBusinessError("AGENT_OPERATION_UNDO_EXPIRED: 操作撤销窗口已过期")
        idempotency_key = text(payload.get("idempotencyKey"))
        if not idempotency_key:
            raise AgentBusinessError("AGENT_VALIDATION_FAILED: 幂等键不能为空")
        updated = self._repository.update_operation(operation_id, status="UNDONE", updated_at=utc_now())
        if updated is None:
            raise AgentBusinessError("AGENT_OPERATION_NOT_FOUND: Agent 操作不存在")
        task_id = str(operation["task_id"])
        self._append_message(
            task_id,
            user_id,
            role="SYSTEM",
            message_type="OPERATION_UNDO",
            content="已撤销 Agent 变更操作。",
            payload={"operationId": operation_id, "idempotencyKey": idempotency_key, "reason": text(payload.get("reason"))},
            source_event_type="OPERATION_UNDONE",
            source_id=operation_id,
            dedupe_key=f"operation-undo:{operation_id}:{idempotency_key}",
        )
        return self.operation_view(updated)

    def list_tools(self) -> list[dict[str, Any]]:
        """返回固定白名单，避免模型或浏览器自行扩展工具能力。"""
        return [dict(item) for item in DEFAULT_TOOLS]

    def create_memory(self, user_id: str, payload: dict[str, Any], *, pending_review: bool = False, source_task_id: str | None = None) -> dict[str, Any]:
        """写入显式或 Agent 候选记忆，并保持用户所有权由服务端决定。"""
        memory_type = required_text(payload.get("memoryType"), "记忆类型不能为空")
        namespace = required_text(payload.get("namespace"), "记忆命名空间不能为空")
        scope_type = required_text(payload.get("scopeType"), "记忆作用域不能为空").upper()
        if scope_type not in MEMORY_SCOPE_RANK:
            raise AgentBusinessError("AGENT_MEMORY_VALIDATION_FAILED: 记忆作用域不合法")
        if scope_type == "SYSTEM":
            raise AgentBusinessError("AGENT_MEMORY_VALIDATION_FAILED: 普通用户不能创建 SYSTEM 记忆")
        subject_key = required_text(payload.get("subjectKey"), "记忆主题键不能为空")
        content = required_text(payload.get("content"), "记忆内容不能为空")
        summary = text(payload.get("summary")) or truncate(content, 160)
        reject_sensitive_memory(content, summary)
        now = utc_now()
        memory_id = new_id("agent-memory")
        source_hash = memory_hash(user_id, source_task_id, namespace, subject_key, content)
        status = "PENDING_REVIEW" if pending_review else "PENDING_INDEX"
        record = {
            "id": memory_id,
            "user_id": user_id,
            "memory_type": memory_type.upper(),
            "namespace": namespace,
            "scope_type": scope_type,
            "scope_id": nullable_text(payload.get("scopeId")),
            "subject_key": subject_key,
            "content": content,
            "summary": summary,
            "evidence_refs_json": payload.get("evidenceRefs") if isinstance(payload.get("evidenceRefs"), list) else [],
            "source_task_id": source_task_id,
            "source_tool_call_id": nullable_text(payload.get("sourceToolCallId")),
            "source_review_id": nullable_text(payload.get("sourceReviewId")),
            "source_hash": source_hash,
            "status": status,
            "confidence": clamp_decimal(payload.get("confidence"), 0.6),
            "importance": clamp_decimal(payload.get("importance"), 0.5),
            "sensitivity_level": text(payload.get("sensitivityLevel")).upper() or "LOW",
            "consent_source": "AGENT_INFERRED" if pending_review else "USER_EXPLICIT",
            "access_count": 0,
            "last_accessed_at": None,
            "valid_from": now,
            "valid_until": None,
            "deleted_at": None,
            "created_at": now,
            "updated_at": now,
        }
        created = self._repository.create_memory(record)
        self._audit_memory(created, "CREATE", "AGENT" if pending_review else "USER", "已创建 Agent 记忆。")
        if not pending_review:
            created = self._activate_memory(created, "用户显式创建记忆后完成 Python 索引降级激活。")
        return self.memory_view(created)

    def list_memories(self, user_id: str, filters: dict[str, str]) -> list[dict[str, Any]]:
        """读取当前用户记忆，查询参数仅作为状态过滤而非权限范围。"""
        normalized = {
            "status": text(filters.get("status")).upper(),
            "memory_type": text(filters.get("memoryType")).upper(),
            "namespace": text(filters.get("namespace")),
            "scope_type": text(filters.get("scopeType")).upper(),
        }
        return [self.memory_view(item) for item in self._repository.list_memories(user_id, normalized)]

    def get_memory(self, memory_id: str, user_id: str) -> dict[str, Any]:
        """返回用户自己的单条记忆，不泄露跨用户资源存在性。"""
        return self.memory_view(self._require_memory(memory_id, user_id))

    def confirm_memory(self, memory_id: str, user_id: str) -> dict[str, Any]:
        """确认待审记忆并使其进入可检索状态。"""
        memory = self._require_memory(memory_id, user_id)
        if memory.get("status") not in {"PENDING_REVIEW", "INDEX_FAILED"}:
            raise AgentBusinessError("AGENT_MEMORY_REVIEW_REQUIRED: 只有待确认或索引失败记忆可确认")
        pending = self._repository.update_memory(
            memory_id,
            status="PENDING_INDEX",
            consent_source="USER_REVIEW",
            updated_at=utc_now(),
        )
        if pending is None:
            raise AgentBusinessError("AGENT_MEMORY_NOT_FOUND: Agent 记忆不存在")
        self._audit_memory(pending, "CONFIRM", "USER", "用户确认待审 Agent 记忆。")
        return self.memory_view(self._activate_memory(pending, "用户确认后完成 Python 索引降级激活。"))

    def reject_memory(self, memory_id: str, user_id: str) -> dict[str, Any]:
        """拒绝候选记忆，后续默认检索不会注入。"""
        memory = self._require_memory(memory_id, user_id)
        updated = self._repository.update_memory(memory_id, status="REJECTED", updated_at=utc_now())
        if updated is None:
            raise AgentBusinessError("AGENT_MEMORY_NOT_FOUND: Agent 记忆不存在")
        self._audit_memory(updated, "REJECT", "USER", "用户拒绝待审 Agent 记忆。")
        return self.memory_view(updated)

    def patch_memory(self, memory_id: str, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """通过新版本实现正文变更，并禁止作用域扩大。"""
        old = self._require_memory(memory_id, user_id)
        if old.get("status") == "DELETED":
            raise AgentBusinessError("AGENT_MEMORY_DELETED: 已删除记忆不可修改")
        new_scope = text(payload.get("scopeType")).upper() or str(old["scope_type"])
        if new_scope not in MEMORY_SCOPE_RANK or MEMORY_SCOPE_RANK[new_scope] < MEMORY_SCOPE_RANK.get(str(old["scope_type"]), 99):
            raise AgentBusinessError("AGENT_MEMORY_SCOPE_ESCALATION: PATCH 不能扩大记忆作用域")
        new_scope_id = nullable_text(payload.get("scopeId")) if "scopeId" in payload else old.get("scope_id")
        content = text(payload.get("content")) or str(old["content"])
        summary = text(payload.get("summary")) or str(old["summary"])
        reject_sensitive_memory(content, summary)
        now = utc_now()
        copied = {
            **old,
            "id": new_id("agent-memory"),
            "namespace": text(payload.get("namespace")) or old["namespace"],
            "subject_key": text(payload.get("subjectKey")) or old["subject_key"],
            "scope_type": new_scope,
            "scope_id": new_scope_id,
            "content": content,
            "summary": summary,
            "source_hash": memory_hash(user_id, old.get("source_task_id"), text(payload.get("namespace")) or str(old["namespace"]), text(payload.get("subjectKey")) or str(old["subject_key"]), content),
            "status": "PENDING_INDEX",
            "consent_source": "USER_REVIEW",
            "created_at": now,
            "updated_at": now,
            "deleted_at": None,
        }
        self._repository.update_memory(memory_id, status="SUPERSEDED", updated_at=now)
        created = self._repository.create_memory(copied)
        self._repository.insert_memory_version(
            {
                "id": new_id("agent-memory-version"),
                "memory_id": created["id"],
                "previous_memory_id": memory_id,
                "relation_type": "REFINES",
                "decision": "APPROVED",
                "reason": "用户修改 Agent 记忆内容或作用域。",
                "decided_by": "USER",
                "user_id": user_id,
                "created_at": now,
            }
        )
        self._audit_memory(old, "SUPERSEDE", "USER", "用户修改后旧 Agent 记忆被替代。")
        return self.memory_view(self._activate_memory(created, "新版本完成 Python 索引降级激活。"))

    def archive_memory(self, memory_id: str, user_id: str) -> dict[str, Any]:
        """归档记忆并从默认检索范围移除。"""
        memory = self._require_memory(memory_id, user_id)
        if memory.get("status") == "DELETED":
            raise AgentBusinessError("AGENT_MEMORY_DELETED: 已删除记忆不可归档")
        updated = self._repository.update_memory(memory_id, status="ARCHIVED", updated_at=utc_now())
        if updated is None:
            raise AgentBusinessError("AGENT_MEMORY_NOT_FOUND: Agent 记忆不存在")
        self._audit_memory(updated, "ARCHIVE", "USER", "用户归档 Agent 记忆。")
        return self.memory_view(updated)

    def delete_memory(self, memory_id: str, user_id: str) -> dict[str, Any]:
        """软删除元数据并擦除正文，避免已删除内容在响应中复现。"""
        memory = self._require_memory(memory_id, user_id)
        now = utc_now()
        updated = self._repository.update_memory(
            memory_id,
            status="DELETED",
            content="[已删除]",
            summary="[已删除]",
            deleted_at=now,
            updated_at=now,
        )
        if updated is None:
            raise AgentBusinessError("AGENT_MEMORY_NOT_FOUND: Agent 记忆不存在")
        self._audit_memory(updated, "DELETE", "USER", "用户删除 Agent 记忆，正文已擦除。", before_hash=memory.get("source_hash"))
        return self.memory_view(updated)

    def memory_context(self, user_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """为进程内统一图生成经过用户和状态过滤的短记忆上下文。"""
        rows = self._repository.search_active_memories(user_id, query, limit)
        result = []
        for item in rows:
            self._repository.update_memory(
                str(item["id"]),
                access_count=int_value(item.get("access_count"), 0) + 1,
                last_accessed_at=utc_now(),
                updated_at=utc_now(),
            )
            result.append(
                {
                    "memoryId": item["id"],
                    "memoryType": item["memory_type"],
                    "namespace": item["namespace"],
                    "scope": item["scope_type"],
                    "subjectKey": item["subject_key"],
                    "summary": item["summary"],
                    "score": float(item.get("importance") or 0.5),
                }
            )
        return result

    def apply_agent_event(self, task_id: str, event: dict[str, Any]) -> dict[str, Any]:
        """将统一图事件投影到 PostgreSQL 任务、消息、工具和审批记录。"""
        task = self.task_record(task_id)
        user_id = str(task["user_id"])
        event_type = text(event.get("eventType")) or "AGENT_EVENT"
        status = text(event.get("status")) or str(task.get("status") or "RUNNING")
        draft = event.get("draft") if isinstance(event.get("draft"), dict) else {}
        final = event.get("final") if isinstance(event.get("final"), dict) else {}
        now = utc_now()
        changes: dict[str, Any] = {"status": status, "updated_at": now}
        if draft:
            changes["draft_json"] = draft
            conversation_title = text(draft.get("conversationTitle"))
            if conversation_title:
                changes["title"] = conversation_title
        if final:
            changes["final_json"] = final
        if event.get("errorCode"):
            changes["error_code"] = text(event.get("errorCode"))
            changes["error_message"] = text(event.get("errorMessage"))
        if event.get("pythonThreadId"):
            changes["python_thread_id"] = text(event.get("pythonThreadId"))
        updated = self._repository.update_task(task_id, **changes)
        if updated is None:
            raise AgentBusinessError("AGENT_TASK_NOT_FOUND: Agent 任务不存在")
        tool_call = event.get("toolCall") if isinstance(event.get("toolCall"), dict) else None
        if tool_call:
            self._repository.insert_tool_call(
                {
                    "id": text(tool_call.get("id")) or new_id("agent-tool-call"),
                    "task_id": task_id,
                    "tool_name": text(tool_call.get("toolName")) or "unknown_tool",
                    "tool_type": text(tool_call.get("toolType")) or "READ",
                    "status": text(tool_call.get("status")) or "FAILED",
                    "request_json": {},
                    "response_json": tool_call.get("response") if isinstance(tool_call.get("response"), dict) else {},
                    "ownership_verified": bool(tool_call.get("ownershipVerified")),
                    "scope": nullable_text(tool_call.get("scope")) or "current_user_or_authorized",
                    "error_code": nullable_text(tool_call.get("errorCode")),
                    "error_message": nullable_text(tool_call.get("errorMessage")),
                    "created_at": now,
                    "updated_at": now,
                }
            )
        review_request = event.get("reviewRequest") if isinstance(event.get("reviewRequest"), dict) else None
        if review_request:
            proposal = review_request.get("proposal") if isinstance(review_request.get("proposal"), dict) else None
            if proposal and text(review_request.get("reviewType")).upper() == "PLAN":
                updated = self._repository.update_task(task_id, plan_json=proposal, updated_at=now) or updated
            self._create_review_from_event(task_id, review_request, now)
        content, message_type, role = event_message(event_type, draft, final, event)
        self._append_message(
            task_id,
            user_id,
            role=role,
            message_type=message_type,
            content=content,
            payload={"eventType": event_type, "draft": draft, "final": final, "toolCall": tool_call, "reviewRequest": review_request},
            source_event_type=event_type,
            source_id=text(tool_call.get("id")) if tool_call else None,
            dedupe_key=f"event:{event_type}:{text(tool_call.get('id')) if tool_call else new_id('message')}",
        )
        return self.task_summary(updated)

    def apply_approved_mutation(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """在已批准审批基础上写入可撤销操作记录，不直接越权修改业务资源。"""
        task = self.task_record(task_id)
        user_id = str(task["user_id"])
        approval_id = text(payload.get("approvalId"))
        review = self._repository.get_review(approval_id, task_id) if approval_id else None
        if review is None or review.get("status") != "APPROVED":
            return {
                "taskId": task_id,
                "toolCallId": payload.get("toolCallId"),
                "toolName": payload.get("toolName"),
                "status": "FAILED",
                "errorCode": "AGENT_MUTATION_REQUIRES_APPROVAL",
                "errorMessage": "变更工具缺少当前用户已批准的审批记录。",
                "retryable": False,
            }
        tool_name = text(payload.get("toolName"))
        idempotency_key = text(payload.get("idempotencyKey")) or f"{tool_name}:{task_id}:{approval_id}"
        now = utc_now()
        operation = self._repository.insert_operation(
            {
                "id": text(payload.get("operationId")) or new_id("agent-operation"),
                "task_id": task_id,
                "review_id": approval_id,
                "user_id": user_id,
                "operation_type": tool_name.upper() or "AGENT_MUTATION",
                "resource_type": "agent_task_draft",
                "resource_id": task_id,
                "status": "APPLIED_UNDOABLE",
                "before_snapshot_ref": None,
                "after_snapshot_ref": None,
                "idempotency_key": idempotency_key,
                "undo_deadline": now + timedelta(minutes=30),
                "audit_event_id": None,
                "error_code": None,
                "error_message": None,
                "created_at": now,
                "updated_at": now,
            }
        )
        return {
            "taskId": task_id,
            "toolCallId": payload.get("toolCallId"),
            "toolName": tool_name,
            "status": "SUCCEEDED",
            "ownershipVerified": True,
            "scope": "current_user",
            "data": {"operationId": operation["id"], "operationStatus": operation["status"]},
        }

    def mark_worker_failure(self, task_id: str, error_code: str, error_message: str) -> None:
        """保证后台图异常不会让任务永久停在运行中。"""
        task = self.task_record(task_id)
        self.apply_agent_event(
            task_id,
            {
                "eventType": "TASK_FAILED",
                "status": "FAILED",
                "pythonThreadId": task.get("python_thread_id") or task_id,
                "errorCode": error_code,
                "errorMessage": error_message,
            },
        )

    def task_summary(self, task: dict[str, Any]) -> dict[str, Any]:
        """转换为 React 兼容的 camelCase 任务对象。"""
        return {
            "id": task["id"],
            "folderId": task.get("folder_id"),
            "taskType": task["task_type"],
            "status": task["status"],
            "title": task.get("title") or "未命名会话",
            "input": json_value(task.get("input_json"), {}),
            "plan": json_value(task.get("plan_json"), {}),
            "draft": json_value(task.get("draft_json"), {}),
            "final": json_value(task.get("final_json"), {}),
            "pythonThreadId": task.get("python_thread_id"),
            "errorCode": task.get("error_code"),
            "errorMessage": task.get("error_message"),
            "createdAt": task.get("created_at"),
            "updatedAt": task.get("updated_at"),
        }

    def message_view(self, item: dict[str, Any]) -> dict[str, Any]:
        """转换持久化消息，保留稳定 sequenceNo 供前端分页。"""
        return {
            "id": item["id"],
            "taskId": item.get("task_id"),
            "sequenceNo": item.get("sequence_no"),
            "role": item.get("role"),
            "messageType": item.get("message_type"),
            "content": item.get("content"),
            "payload": json_value(item.get("payload_json"), {}),
            "sourceEventType": item.get("source_event_type"),
            "sourceId": item.get("source_id"),
            "dedupeKey": item.get("dedupe_key"),
            "createdAt": item.get("created_at"),
            "updatedAt": item.get("updated_at"),
        }

    def summary_view(self, item: dict[str, Any]) -> dict[str, Any]:
        """转换摘要记录，供任务详情和统一图恢复上下文直接复用。"""
        return {
            "id": item["id"],
            "summaryId": item["id"],
            "taskId": item.get("task_id"),
            "summaryType": item.get("summary_type"),
            "coveredMessageStartId": item.get("covered_message_start_id"),
            "coveredMessageEndId": item.get("covered_message_end_id"),
            "coveredMessageCount": item.get("covered_message_count"),
            "rawTokenEstimate": item.get("raw_token_estimate"),
            "compressedTokenEstimate": item.get("compressed_token_estimate"),
            "summary": json_value(item.get("summary_json"), {}),
            "summaryText": item.get("summary_text"),
            "keyFacts": json_value(item.get("key_facts_json"), []),
            "evidenceRefs": json_value(item.get("evidence_refs_json"), []),
            "compressionModel": item.get("compression_model"),
            "compressionPromptVersion": item.get("compression_prompt_version"),
            "compressionVersion": item.get("compression_version"),
            "status": item.get("status"),
            "diagnostics": json_value(item.get("diagnostics_json"), {}),
            "createdAt": item.get("created_at"),
            "updatedAt": item.get("updated_at"),
        }

    def tool_call_view(self, item: dict[str, Any]) -> dict[str, Any]:
        """转换工具观测，避免返回未脱敏的内部请求字段。"""
        return {
            "id": item["id"],
            "taskId": item.get("task_id"),
            "toolName": item.get("tool_name"),
            "toolType": item.get("tool_type"),
            "status": item.get("status"),
            "request": json_value(item.get("request_json"), {}),
            "response": json_value(item.get("response_json"), {}),
            "ownershipVerified": item.get("ownership_verified"),
            "scope": item.get("scope"),
            "errorCode": item.get("error_code"),
            "errorMessage": item.get("error_message"),
            "createdAt": item.get("created_at"),
            "updatedAt": item.get("updated_at"),
        }

    def review_view(self, item: dict[str, Any]) -> dict[str, Any]:
        """转换审批记录，提案与决策均由 PostgreSQL 事实记录读取。"""
        return {
            "id": item["id"],
            "taskId": item.get("task_id"),
            "reviewType": item.get("review_type"),
            "status": item.get("status"),
            "proposal": json_value(item.get("proposal_json"), {}),
            "decision": json_value(item.get("decision_json"), {}),
            "reviewedBy": item.get("reviewed_by"),
            "reviewedAt": item.get("reviewed_at"),
            "createdAt": item.get("created_at"),
            "updatedAt": item.get("updated_at"),
            "expiresAt": item.get("expires_at"),
        }

    def operation_view(self, item: dict[str, Any]) -> dict[str, Any]:
        """转换可撤销操作记录。"""
        return {
            "id": item["id"],
            "taskId": item.get("task_id"),
            "reviewId": item.get("review_id"),
            "operationType": item.get("operation_type"),
            "resourceType": item.get("resource_type"),
            "resourceId": item.get("resource_id"),
            "status": item.get("status"),
            "beforeSnapshotRef": item.get("before_snapshot_ref"),
            "afterSnapshotRef": item.get("after_snapshot_ref"),
            "idempotencyKey": item.get("idempotency_key"),
            "undoDeadline": item.get("undo_deadline"),
            "auditEventId": item.get("audit_event_id"),
            "errorCode": item.get("error_code"),
            "errorMessage": item.get("error_message"),
            "createdAt": item.get("created_at"),
            "updatedAt": item.get("updated_at"),
        }

    def folder_view(self, folder: dict[str, Any], conversations: list[dict[str, Any]], count: int) -> dict[str, Any]:
        """转换文件夹及限定窗口内的会话。"""
        return {
            "id": folder.get("id"),
            "name": folder.get("name"),
            "sortOrder": folder.get("sort_order"),
            "conversationCount": count,
            "conversations": conversations,
            "createdAt": folder.get("created_at"),
            "updatedAt": folder.get("updated_at"),
        }

    def memory_view(self, item: dict[str, Any]) -> dict[str, Any]:
        """转换当前用户可见的记忆记录。"""
        return {
            "id": item["id"],
            "userId": item.get("user_id"),
            "memoryType": item.get("memory_type"),
            "namespace": item.get("namespace"),
            "scopeType": item.get("scope_type"),
            "scopeId": item.get("scope_id"),
            "subjectKey": item.get("subject_key"),
            "content": item.get("content"),
            "summary": item.get("summary"),
            "evidenceRefs": json_value(item.get("evidence_refs_json"), []),
            "sourceTaskId": item.get("source_task_id"),
            "sourceToolCallId": item.get("source_tool_call_id"),
            "sourceReviewId": item.get("source_review_id"),
            "status": item.get("status"),
            "confidence": float(item.get("confidence") or 0),
            "importance": float(item.get("importance") or 0),
            "sensitivityLevel": item.get("sensitivity_level"),
            "consentSource": item.get("consent_source"),
            "accessCount": item.get("access_count"),
            "lastAccessedAt": item.get("last_accessed_at"),
            "validFrom": item.get("valid_from"),
            "validUntil": item.get("valid_until"),
            "deletedAt": item.get("deleted_at"),
            "createdAt": item.get("created_at"),
            "updatedAt": item.get("updated_at"),
        }

    def _require_task(self, task_id: str, user_id: str) -> dict[str, Any]:
        task = self._repository.get_task(task_id, user_id)
        if task is None:
            raise AgentBusinessError("AGENT_TASK_NOT_FOUND: Agent 任务不存在")
        return task

    def _require_memory(self, memory_id: str, user_id: str) -> dict[str, Any]:
        item = self._repository.get_memory(memory_id, user_id)
        if item is None:
            raise AgentBusinessError("AGENT_MEMORY_NOT_FOUND: Agent 记忆不存在")
        return item

    def _append_message(
        self,
        task_id: str,
        user_id: str,
        *,
        role: str,
        message_type: str,
        content: str,
        payload: dict[str, Any],
        source_event_type: str | None,
        source_id: str | None,
        dedupe_key: str,
    ) -> None:
        """追加消息投影；重复投递通过 task/dedupe_key 约束保持幂等。"""
        now = utc_now()
        self._repository.append_message(
            {
                "id": new_id("agent-message"),
                "task_id": task_id,
                "user_id": user_id,
                "role": role,
                "message_type": message_type,
                "content": content or "Agent 状态已更新。",
                "payload_json": payload,
                "source_event_type": source_event_type,
                "source_id": source_id,
                "dedupe_key": dedupe_key[:220],
                "created_at": now,
                "updated_at": now,
            }
        )

    def _create_review_from_event(self, task_id: str, request: dict[str, Any], now: datetime) -> None:
        """将统一图暂停点投影为当前任务的可审批记录。"""
        review_id = text(request.get("reviewId")) or text(request.get("id")) or new_id("agent-review")
        if self._repository.get_review(review_id, task_id) is not None:
            return
        review_type = text(request.get("reviewType")).upper() or "PLAN"
        self._repository.insert_review(
            {
                "id": review_id,
                "task_id": task_id,
                "review_type": review_type,
                "status": "PENDING",
                "proposal_json": request.get("proposal") if isinstance(request.get("proposal"), dict) else request,
                "decision_json": {},
                "reviewed_by": None,
                "reviewed_at": None,
                "created_at": now,
                "updated_at": now,
                "expires_at": None,
            }
        )

    def _activate_memory(self, memory: dict[str, Any], summary: str) -> dict[str, Any]:
        """当前阶段使用确定性索引降级激活记忆，保留后续接入向量索引的状态边界。"""
        updated = self._repository.update_memory(memory["id"], status="ACTIVE", updated_at=utc_now())
        if updated is None:
            raise AgentBusinessError("AGENT_MEMORY_NOT_FOUND: Agent 记忆不存在")
        self._audit_memory(updated, "INDEX_UPSERT", "SYSTEM", summary)
        return updated

    def _audit_memory(
        self,
        memory: dict[str, Any],
        action: str,
        actor_type: str,
        summary: str,
        *,
        before_hash: str | None = None,
    ) -> None:
        """写入不含正文的记忆审计事件。"""
        self._repository.insert_memory_audit(
            {
                "id": new_id("agent-memory-audit"),
                "memory_id": memory.get("id"),
                "user_id": memory["user_id"],
                "task_id": memory.get("source_task_id"),
                "action": action,
                "actor_type": actor_type,
                "before_hash": before_hash,
                "after_hash": memory.get("source_hash"),
                "summary": summary,
                "created_at": utc_now(),
            }
        )

    def _folder_task_count(self, user_id: str, folder_id: str) -> int:
        """文件夹的数量以数据库任务事实记录为准。"""
        return len(self._repository.list_tasks(user_id, 1000, folder_id))


def text(value: Any) -> str:
    """标准化 API 文本字段。"""
    return value.strip() if isinstance(value, str) else ""


def nullable_text(value: Any) -> str | None:
    """将空字符串转为数据库空值。"""
    result = text(value)
    return result or None


def required_text(value: Any, message: str) -> str:
    """校验必填文本并保留中文业务错误。"""
    result = text(value)
    if not result:
        raise AgentBusinessError(f"AGENT_MEMORY_VALIDATION_FAILED: {message}")
    return result


def int_value(value: Any, default: int) -> int:
    """安全转换正整数类输入。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def clamp(value: int | None, *, default: int, lower: int, upper: int) -> int:
    """将分页及列表限制控制在协议允许范围。"""
    return max(lower, min(upper, int_value(value, default)))


def clamp_decimal(value: Any, default: float) -> float:
    """限制记忆权重到 0 到 1 的闭区间。"""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def json_value(value: Any, fallback: Any) -> Any:
    """兼容 PostgreSQL TEXT/JSONB 和内存仓储中的 JSON 值。"""
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value.strip():
        return fallback
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, type(fallback)) else fallback
    except json.JSONDecodeError:
        return fallback


def fallback_title(goal: str) -> str:
    """在模型标题节点不可用时生成稳定的会话标题。"""
    return truncate(goal.replace("\n", " ").strip(), 20) or "新建 Agent 会话"


def truncate(value: str, size: int) -> str:
    """截断展示文本，避免长输入撑破标题和摘要字段。"""
    return value if len(value) <= size else f"{value[:size]}..."


def memory_hash(user_id: str, source_task_id: str | None, namespace: str, subject_key: str, content: str) -> str:
    """生成记忆来源哈希，不将正文直接放进审计表。"""
    source = "|".join([user_id, source_task_id or "", namespace, subject_key, content])
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def reject_sensitive_memory(content: str, summary: str) -> None:
    """拒绝明显凭据和证件模式，避免其进入长期 Agent 记忆。"""
    value = f"{content}\n{summary}"
    patterns = [r"(?i)(api[_-]?key|secret|password|token)\s*[:=]", r"\b\d{17}[\dXx]\b", r"\b1[3-9]\d{9}\b"]
    if any(re.search(pattern, value) for pattern in patterns):
        raise AgentBusinessError("AGENT_MEMORY_SENSITIVE_REJECTED: 记忆内容包含敏感信息，已拒绝保存")


def parse_datetime(value: str) -> datetime | None:
    """兼容 JSON 化时间的撤销截止校验。"""
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.tzinfo else result.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def review_decision_message(decision: str, comment: str) -> str:
    """生成无内部推理的用户审批消息。"""
    labels = {"APPROVED": "用户已批准 Agent 继续执行。", "REJECTED": "用户已拒绝 Agent 审批。", "CHANGES_REQUESTED": "用户要求修改 Agent 方案。"}
    return f"{labels[decision]} {comment}".strip()


def event_message(event_type: str, draft: dict[str, Any], final: dict[str, Any], event: dict[str, Any]) -> tuple[str, str, str]:
    """把图事件转换为可审计且不含隐藏推理链的消息投影。"""
    if event_type == "TASK_COMPLETED":
        return text(final.get("answer")) or text(final.get("message")) or "Agent 任务已完成。", "FINAL_ANSWER", "ASSISTANT"
    if event_type == "TASK_FAILED":
        return text(event.get("errorMessage")) or "Agent 任务执行失败。", "ERROR", "SYSTEM"
    if "TOOL_CALL" in event_type:
        return text(draft.get("message")) or "Agent 已完成一次工具调用。", "TOOL_OBSERVATION", "TOOL"
    if "REVIEW" in event_type or "PROPOSED" in event_type:
        return text(draft.get("message")) or "Agent 正在等待用户审批。", "PLAN_REVIEW", "SYSTEM"
    return text(draft.get("message")) or f"Agent 状态更新：{event_type}。", "STATUS", "SYSTEM"
