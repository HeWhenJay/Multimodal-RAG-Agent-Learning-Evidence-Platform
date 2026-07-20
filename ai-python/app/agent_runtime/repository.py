"""Agent 任务和记忆的 PostgreSQL 仓储，以及供测试使用的内存替身。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
import os
from threading import RLock
from typing import Any, ContextManager, Protocol

from app.auth.repository import DEFAULT_SCHEMA, resolve_database_url, validate_schema


JSON_FIELDS = {
    "input_json",
    "plan_json",
    "draft_json",
    "final_json",
    "request_json",
    "response_json",
    "proposal_json",
    "decision_json",
    "payload_json",
    "summary_json",
    "key_facts_json",
    "evidence_refs_json",
    "diagnostics_json",
}


class AgentRepositoryProtocol(Protocol):
    """公开 Agent 服务所需的最小持久化操作，支持测试替换。"""

    def create_task(self, record: dict[str, Any]) -> dict[str, Any]: ...

    def get_task(self, task_id: str, user_id: str | None = None) -> dict[str, Any] | None: ...

    def list_tasks(self, user_id: str, limit: int, folder_id: str | None = None) -> list[dict[str, Any]]: ...

    def list_runnable_tasks(self, limit: int) -> list[dict[str, Any]]: ...

    def task_execution_lock(self, task_id: str) -> ContextManager[bool]: ...

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any] | None: ...

    def append_message(self, record: dict[str, Any]) -> dict[str, Any]: ...

    def list_messages(
        self,
        task_id: str,
        limit: int,
        before_sequence_no: int | None = None,
        after_sequence_no: int | None = None,
    ) -> list[dict[str, Any]]: ...

    def has_message_before(self, task_id: str, sequence_no: int) -> bool: ...

    def has_message_after(self, task_id: str, sequence_no: int) -> bool: ...

    def save_conversation_summary(self, record: dict[str, Any]) -> dict[str, Any]: ...

    def list_conversation_summaries(self, task_id: str, limit: int) -> list[dict[str, Any]]: ...

    def count_conversation_summaries(self, task_id: str) -> int: ...

    def insert_tool_call(self, record: dict[str, Any]) -> dict[str, Any]: ...

    def list_tool_calls(self, task_id: str) -> list[dict[str, Any]]: ...

    def insert_review(self, record: dict[str, Any]) -> dict[str, Any]: ...

    def get_review(self, review_id: str, task_id: str) -> dict[str, Any] | None: ...

    def update_review(self, review_id: str, **changes: Any) -> dict[str, Any] | None: ...

    def list_reviews(self, task_id: str) -> list[dict[str, Any]]: ...

    def insert_operation(self, record: dict[str, Any]) -> dict[str, Any]: ...

    def get_operation(self, operation_id: str, user_id: str | None = None) -> dict[str, Any] | None: ...

    def update_operation(self, operation_id: str, **changes: Any) -> dict[str, Any] | None: ...

    def list_operations(self, task_id: str) -> list[dict[str, Any]]: ...

    def create_folder(self, record: dict[str, Any]) -> dict[str, Any]: ...

    def get_folder(self, folder_id: str, user_id: str | None = None) -> dict[str, Any] | None: ...

    def list_folders(self, user_id: str) -> list[dict[str, Any]]: ...

    def update_folder(self, folder_id: str, **changes: Any) -> dict[str, Any] | None: ...

    def delete_folder(self, folder_id: str, user_id: str) -> bool: ...

    def create_memory(self, record: dict[str, Any]) -> dict[str, Any]: ...

    def get_memory(self, memory_id: str, user_id: str | None = None) -> dict[str, Any] | None: ...

    def list_memories(self, user_id: str, filters: dict[str, str]) -> list[dict[str, Any]]: ...

    def update_memory(self, memory_id: str, **changes: Any) -> dict[str, Any] | None: ...

    def search_active_memories(self, user_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]: ...

    def insert_memory_version(self, record: dict[str, Any]) -> None: ...

    def insert_memory_audit(self, record: dict[str, Any]) -> None: ...


class InMemoryAgentRepository:
    """供 API 测试使用的进程内仓储，接口与 PostgreSQL 实现保持一致。"""

    def __init__(self) -> None:
        self._lock = RLock()
        self.tasks: dict[str, dict[str, Any]] = {}
        self._claimed_task_ids: set[str] = set()
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.conversation_summaries: dict[str, list[dict[str, Any]]] = {}
        self.tool_calls: dict[str, dict[str, Any]] = {}
        self.reviews: dict[str, dict[str, Any]] = {}
        self.operations: dict[str, dict[str, Any]] = {}
        self.folders: dict[str, dict[str, Any]] = {}
        self.memories: dict[str, dict[str, Any]] = {}
        self.memory_versions: list[dict[str, Any]] = []
        self.memory_audits: list[dict[str, Any]] = []

    def create_task(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.tasks[record["id"]] = deepcopy(record)
            return deepcopy(record)

    def get_task(self, task_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None or (user_id is not None and task.get("user_id") != user_id):
                return None
            return deepcopy(task)

    def list_tasks(self, user_id: str, limit: int, folder_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            rows = [
                item
                for item in self.tasks.values()
                if item.get("user_id") == user_id and (folder_id is None or item.get("folder_id") == folder_id)
            ]
            rows.sort(key=lambda item: (item.get("updated_at"), item.get("id")), reverse=True)
            return deepcopy(rows[:limit])

    def list_runnable_tasks(self, limit: int) -> list[dict[str, Any]]:
        """返回等待启动或因进程中断而需要恢复的任务。"""
        with self._lock:
            rows = [item for item in self.tasks.values() if item.get("status") in {"CREATED", "RUNNING"}]
            rows.sort(key=lambda item: (item.get("updated_at"), item.get("id")))
            return deepcopy(rows[:limit])

    @contextmanager
    def task_execution_lock(self, task_id: str) -> Iterator[bool]:
        """用内存锁模拟跨 worker 任务领取，避免测试掩盖并发重复执行。"""
        with self._lock:
            acquired = task_id not in self._claimed_task_ids
            if acquired:
                self._claimed_task_ids.add(task_id)
        if not acquired:
            yield False
            return
        try:
            yield True
        finally:
            with self._lock:
                self._claimed_task_ids.discard(task_id)

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            task = self.tasks.get(task_id)
            if task is None:
                return None
            task.update(deepcopy(changes))
            return deepcopy(task)

    def append_message(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            task_messages = self.messages.setdefault(record["task_id"], [])
            duplicate = next((item for item in task_messages if item.get("dedupe_key") == record.get("dedupe_key")), None)
            if duplicate is not None:
                return deepcopy(duplicate)
            item = deepcopy(record)
            item["sequence_no"] = len(task_messages) + 1
            task_messages.append(item)
            return deepcopy(item)

    def list_messages(
        self,
        task_id: str,
        limit: int,
        before_sequence_no: int | None = None,
        after_sequence_no: int | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            rows = list(self.messages.get(task_id, []))
            if before_sequence_no is not None:
                rows = [item for item in rows if int(item["sequence_no"]) < before_sequence_no]
                rows = rows[-limit:]
            elif after_sequence_no is not None:
                rows = [item for item in rows if int(item["sequence_no"]) > after_sequence_no][:limit]
            else:
                rows = rows[-limit:]
            return deepcopy(rows)

    def has_message_before(self, task_id: str, sequence_no: int) -> bool:
        with self._lock:
            return any(int(item["sequence_no"]) < sequence_no for item in self.messages.get(task_id, []))

    def has_message_after(self, task_id: str, sequence_no: int) -> bool:
        with self._lock:
            return any(int(item["sequence_no"]) > sequence_no for item in self.messages.get(task_id, []))

    def save_conversation_summary(self, record: dict[str, Any]) -> dict[str, Any]:
        """保存或更新同一摘要 ID，方便 worker 在重启后恢复上下文。"""
        with self._lock:
            summaries = self.conversation_summaries.setdefault(record["task_id"], [])
            existing = next((item for item in summaries if item.get("id") == record["id"]), None)
            if existing is None:
                summaries.append(deepcopy(record))
                return deepcopy(record)
            existing.update(deepcopy(record))
            return deepcopy(existing)

    def list_conversation_summaries(self, task_id: str, limit: int) -> list[dict[str, Any]]:
        """按最近更新时间读取摘要段，供任务详情和上下文恢复使用。"""
        with self._lock:
            rows = list(self.conversation_summaries.get(task_id, []))
            rows.sort(key=lambda item: (item.get("updated_at"), item.get("id")), reverse=True)
            return deepcopy(rows[:limit])

    def count_conversation_summaries(self, task_id: str) -> int:
        """统计任务摘要总数，帮助前端判断是否需加载更多。"""
        with self._lock:
            return len(self.conversation_summaries.get(task_id, []))

    def insert_tool_call(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            existing = self.tool_calls.get(record["id"])
            if existing is not None:
                existing.update(deepcopy(record))
                return deepcopy(existing)
            self.tool_calls[record["id"]] = deepcopy(record)
            return deepcopy(record)

    def list_tool_calls(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(sorted((item for item in self.tool_calls.values() if item.get("task_id") == task_id), key=lambda item: item.get("created_at")))

    def insert_review(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.reviews[record["id"]] = deepcopy(record)
            return deepcopy(record)

    def get_review(self, review_id: str, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self.reviews.get(review_id)
            return deepcopy(item) if item and item.get("task_id") == task_id else None

    def update_review(self, review_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            item = self.reviews.get(review_id)
            if item is None:
                return None
            item.update(deepcopy(changes))
            return deepcopy(item)

    def list_reviews(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(sorted((item for item in self.reviews.values() if item.get("task_id") == task_id), key=lambda item: item.get("created_at")))

    def insert_operation(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.operations[record["id"]] = deepcopy(record)
            return deepcopy(record)

    def get_operation(self, operation_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            item = self.operations.get(operation_id)
            if item is None or (user_id is not None and item.get("user_id") != user_id):
                return None
            return deepcopy(item)

    def update_operation(self, operation_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            item = self.operations.get(operation_id)
            if item is None:
                return None
            item.update(deepcopy(changes))
            return deepcopy(item)

    def list_operations(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(sorted((item for item in self.operations.values() if item.get("task_id") == task_id), key=lambda item: item.get("created_at")))

    def create_folder(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.folders[record["id"]] = deepcopy(record)
            return deepcopy(record)

    def get_folder(self, folder_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            item = self.folders.get(folder_id)
            if item is None or (user_id is not None and item.get("user_id") != user_id):
                return None
            return deepcopy(item)

    def list_folders(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = [item for item in self.folders.values() if item.get("user_id") == user_id]
            rows.sort(key=lambda item: (item.get("sort_order", 0), item.get("updated_at"), item.get("id")))
            return deepcopy(rows)

    def update_folder(self, folder_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            item = self.folders.get(folder_id)
            if item is None:
                return None
            item.update(deepcopy(changes))
            return deepcopy(item)

    def delete_folder(self, folder_id: str, user_id: str) -> bool:
        with self._lock:
            item = self.get_folder(folder_id, user_id)
            if item is None:
                return False
            for task in self.tasks.values():
                if task.get("user_id") == user_id and task.get("folder_id") == folder_id:
                    task["folder_id"] = None
            del self.folders[folder_id]
            return True

    def create_memory(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.memories[record["id"]] = deepcopy(record)
            return deepcopy(record)

    def get_memory(self, memory_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        with self._lock:
            item = self.memories.get(memory_id)
            if item is None or (user_id is not None and item.get("user_id") != user_id):
                return None
            return deepcopy(item)

    def list_memories(self, user_id: str, filters: dict[str, str]) -> list[dict[str, Any]]:
        with self._lock:
            rows = [item for item in self.memories.values() if item.get("user_id") == user_id]
            for key, value in filters.items():
                if value:
                    rows = [item for item in rows if str(item.get(key, "")) == value]
            if not filters.get("status"):
                rows = [item for item in rows if item.get("status") != "DELETED"]
            rows.sort(key=lambda item: (item.get("updated_at"), item.get("id")), reverse=True)
            return deepcopy(rows)

    def update_memory(self, memory_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock:
            item = self.memories.get(memory_id)
            if item is None:
                return None
            item.update(deepcopy(changes))
            return deepcopy(item)

    def search_active_memories(self, user_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        tokens = [part.lower() for part in query.split() if part.strip()]
        with self._lock:
            rows = [
                item
                for item in self.memories.values()
                if item.get("user_id") == user_id
                and item.get("status") == "ACTIVE"
                and item.get("deleted_at") is None
                and item.get("sensitivity_level") != "HIGH"
            ]
            rows.sort(
                key=lambda item: (
                    sum(token in f"{item.get('summary', '')} {item.get('content', '')}".lower() for token in tokens),
                    float(item.get("importance") or 0),
                    item.get("updated_at"),
                ),
                reverse=True,
            )
            return deepcopy(rows[:limit])

    def insert_memory_version(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.memory_versions.append(deepcopy(record))

    def insert_memory_audit(self, record: dict[str, Any]) -> None:
        with self._lock:
            self.memory_audits.append(deepcopy(record))


class PostgresAgentRepository:
    """使用既有 `learning_evidence.agent_*` 表的 psycopg 仓储。"""

    def __init__(self, database_url: str | None = None, schema: str | None = None) -> None:
        self._database_url = database_url or resolve_database_url()
        self._schema = validate_schema(schema or os.getenv("RAG_DATABASE_SCHEMA", DEFAULT_SCHEMA))

    def create_task(self, record: dict[str, Any]) -> dict[str, Any]:
        self._write(
            """
            INSERT INTO {schema}.agent_task
                (id, user_id, task_type, status, title, folder_id, input_json, plan_json, draft_json,
                 final_json, python_thread_id, error_code, error_message, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record["id"], record["user_id"], record["task_type"], record["status"], record.get("title"),
                record.get("folder_id"), json_text(record.get("input_json")), json_text(record.get("plan_json")),
                json_text(record.get("draft_json")), json_text(record.get("final_json")), record.get("python_thread_id"),
                record.get("error_code"), record.get("error_message"), record["created_at"], record["updated_at"],
            ),
        )
        created = self.get_task(str(record["id"]))
        if created is None:
            raise RuntimeError("Agent 任务写入后无法读取")
        return created

    def get_task(self, task_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        condition = "AND user_id = %s" if user_id is not None else ""
        params: tuple[Any, ...] = (task_id, user_id) if user_id is not None else (task_id,)
        return self._row(
            f"SELECT * FROM {{schema}}.agent_task WHERE id = %s {condition}",
            params,
        )

    def list_tasks(self, user_id: str, limit: int, folder_id: str | None = None) -> list[dict[str, Any]]:
        if folder_id is None:
            return self._rows(
                "SELECT * FROM {schema}.agent_task WHERE user_id = %s ORDER BY updated_at DESC, id DESC LIMIT %s",
                (user_id, limit),
            )
        return self._rows(
            "SELECT * FROM {schema}.agent_task WHERE user_id = %s AND folder_id = %s ORDER BY updated_at DESC, id DESC LIMIT %s",
            (user_id, folder_id, limit),
        )

    def list_runnable_tasks(self, limit: int) -> list[dict[str, Any]]:
        """读取待启动或可恢复的任务，单 worker 顺序完成每次领取。"""
        return self._rows(
            """
            SELECT * FROM {schema}.agent_task
            WHERE status IN ('CREATED', 'RUNNING')
            ORDER BY updated_at ASC, id ASC
            LIMIT %s
            """,
            (limit,),
        )

    @contextmanager
    def task_execution_lock(self, task_id: str) -> Iterator[bool]:
        """通过 PostgreSQL session advisory lock 防止多个 worker 并发运行同一任务。"""
        connection = self._connect()
        acquired = False
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_try_advisory_lock(hashtext(%s), hashtext(%s)) AS acquired",
                    (self._schema, task_id),
                )
                row = cursor.fetchone() or {}
                acquired = bool(row.get("acquired"))
            yield acquired
        finally:
            if acquired:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT pg_advisory_unlock(hashtext(%s), hashtext(%s))",
                            (self._schema, task_id),
                        )
                finally:
                    connection.close()
            else:
                connection.close()

    def update_task(self, task_id: str, **changes: Any) -> dict[str, Any] | None:
        return self._update("agent_task", task_id, changes)

    def append_message(self, record: dict[str, Any]) -> dict[str, Any]:
        with self._transaction() as cursor:
            cursor.execute(self._statement("SELECT id FROM {schema}.agent_task WHERE id = %s FOR UPDATE"), (record["task_id"],))
            if cursor.fetchone() is None:
                raise RuntimeError("Agent 任务不存在，无法追加消息")
            cursor.execute(
                self._statement("SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_no FROM {schema}.agent_chat_message WHERE task_id = %s"),
                (record["task_id"],),
            )
            next_no = int(cursor.fetchone()["next_no"])
            cursor.execute(
                self._statement(
                    """
                    INSERT INTO {schema}.agent_chat_message
                        (id, task_id, user_id, sequence_no, role, message_type, content, payload_json,
                         source_event_type, source_id, dedupe_key, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (task_id, dedupe_key) DO UPDATE SET updated_at = EXCLUDED.updated_at
                    RETURNING *
                    """
                ),
                (
                    record["id"], record["task_id"], record["user_id"], next_no, record["role"], record["message_type"],
                    record["content"], json_text(record.get("payload_json")), record.get("source_event_type"), record.get("source_id"),
                    record["dedupe_key"], record["created_at"], record["updated_at"],
                ),
            )
            return normalize_row(cursor.fetchone())

    def list_messages(self, task_id: str, limit: int, before_sequence_no: int | None = None, after_sequence_no: int | None = None) -> list[dict[str, Any]]:
        if before_sequence_no is not None:
            rows = self._rows(
                "SELECT * FROM {schema}.agent_chat_message WHERE task_id = %s AND sequence_no < %s ORDER BY sequence_no DESC LIMIT %s",
                (task_id, before_sequence_no, limit),
            )
            return list(reversed(rows))
        if after_sequence_no is not None:
            return self._rows(
                "SELECT * FROM {schema}.agent_chat_message WHERE task_id = %s AND sequence_no > %s ORDER BY sequence_no ASC LIMIT %s",
                (task_id, after_sequence_no, limit),
            )
        rows = self._rows(
            "SELECT * FROM {schema}.agent_chat_message WHERE task_id = %s ORDER BY sequence_no DESC LIMIT %s",
            (task_id, limit),
        )
        return list(reversed(rows))

    def has_message_before(self, task_id: str, sequence_no: int) -> bool:
        return self._row(
            "SELECT 1 AS present FROM {schema}.agent_chat_message WHERE task_id = %s AND sequence_no < %s LIMIT 1",
            (task_id, sequence_no),
        ) is not None

    def has_message_after(self, task_id: str, sequence_no: int) -> bool:
        return self._row(
            "SELECT 1 AS present FROM {schema}.agent_chat_message WHERE task_id = %s AND sequence_no > %s LIMIT 1",
            (task_id, sequence_no),
        ) is not None

    def save_conversation_summary(self, record: dict[str, Any]) -> dict[str, Any]:
        """持久化压缩摘要，正文消息仍保留在消息表中以支持审计回溯。"""
        self._write(
            """
            INSERT INTO {schema}.agent_conversation_summary
                (id, task_id, user_id, summary_type, covered_message_start_id, covered_message_end_id,
                 covered_message_count, raw_token_estimate, compressed_token_estimate, summary_json, summary_text,
                 key_facts_json, evidence_refs_json, compression_model, compression_prompt_version,
                 compression_version, status, diagnostics_json, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                summary_json = EXCLUDED.summary_json,
                summary_text = EXCLUDED.summary_text,
                key_facts_json = EXCLUDED.key_facts_json,
                evidence_refs_json = EXCLUDED.evidence_refs_json,
                diagnostics_json = EXCLUDED.diagnostics_json,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
            """,
            (
                record["id"], record["task_id"], record["user_id"], record["summary_type"],
                record.get("covered_message_start_id"), record.get("covered_message_end_id"),
                record["covered_message_count"], record["raw_token_estimate"], record["compressed_token_estimate"],
                json_text(record.get("summary_json")), record["summary_text"], json_text(record.get("key_facts_json")),
                json_text(record.get("evidence_refs_json")), record.get("compression_model"),
                record.get("compression_prompt_version"), record["compression_version"], record["status"],
                json_text(record.get("diagnostics_json")), record["created_at"], record["updated_at"],
            ),
        )
        item = self._row("SELECT * FROM {schema}.agent_conversation_summary WHERE id = %s", (record["id"],))
        if item is None:
            raise RuntimeError("Agent 上下文摘要写入失败")
        return item

    def list_conversation_summaries(self, task_id: str, limit: int) -> list[dict[str, Any]]:
        """按最近更新时间读取可恢复摘要段。"""
        return self._rows(
            """
            SELECT * FROM {schema}.agent_conversation_summary
            WHERE task_id = %s
            ORDER BY updated_at DESC, id DESC
            LIMIT %s
            """,
            (task_id, limit),
        )

    def count_conversation_summaries(self, task_id: str) -> int:
        """统计摘要总数，避免详情窗口错误声称已加载全部记录。"""
        row = self._row("SELECT COUNT(1) AS count FROM {schema}.agent_conversation_summary WHERE task_id = %s", (task_id,))
        return int(row.get("count") or 0) if row else 0

    def insert_tool_call(self, record: dict[str, Any]) -> dict[str, Any]:
        self._write(
            """
            INSERT INTO {schema}.agent_tool_call
                (id, task_id, tool_name, tool_type, status, request_json, response_json, ownership_verified,
                 scope, error_code, error_message, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET status = EXCLUDED.status, response_json = EXCLUDED.response_json,
                ownership_verified = EXCLUDED.ownership_verified, scope = EXCLUDED.scope,
                error_code = EXCLUDED.error_code, error_message = EXCLUDED.error_message, updated_at = EXCLUDED.updated_at
            """,
            (
                record["id"], record["task_id"], record["tool_name"], record["tool_type"], record["status"],
                json_text(record.get("request_json")), json_text(record.get("response_json")), bool(record.get("ownership_verified")),
                record.get("scope") or "current_user_or_authorized", record.get("error_code"), record.get("error_message"),
                record["created_at"], record["updated_at"],
            ),
        )
        item = self._row("SELECT * FROM {schema}.agent_tool_call WHERE id = %s", (record["id"],))
        if item is None:
            raise RuntimeError("Agent 工具调用写入失败")
        return item

    def list_tool_calls(self, task_id: str) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM {schema}.agent_tool_call WHERE task_id = %s ORDER BY created_at ASC, id ASC", (task_id,))

    def insert_review(self, record: dict[str, Any]) -> dict[str, Any]:
        self._write(
            """
            INSERT INTO {schema}.agent_human_review
                (id, task_id, review_type, status, proposal_json, decision_json, reviewed_by, reviewed_at,
                 created_at, updated_at, expires_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record["id"], record["task_id"], record["review_type"], record["status"],
                json_text(record.get("proposal_json")), json_text(record.get("decision_json")), record.get("reviewed_by"),
                record.get("reviewed_at"), record["created_at"], record["updated_at"], record.get("expires_at"),
            ),
        )
        item = self.get_review(str(record["id"]), str(record["task_id"]))
        if item is None:
            raise RuntimeError("Agent 审批写入失败")
        return item

    def get_review(self, review_id: str, task_id: str) -> dict[str, Any] | None:
        return self._row("SELECT * FROM {schema}.agent_human_review WHERE id = %s AND task_id = %s", (review_id, task_id))

    def update_review(self, review_id: str, **changes: Any) -> dict[str, Any] | None:
        return self._update("agent_human_review", review_id, changes)

    def list_reviews(self, task_id: str) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM {schema}.agent_human_review WHERE task_id = %s ORDER BY created_at ASC, id ASC", (task_id,))

    def insert_operation(self, record: dict[str, Any]) -> dict[str, Any]:
        self._write(
            """
            INSERT INTO {schema}.agent_operation
                (id, task_id, review_id, user_id, operation_type, resource_type, resource_id, status,
                 before_snapshot_ref, after_snapshot_ref, idempotency_key, undo_deadline, audit_event_id,
                 error_code, error_message, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, operation_type, resource_type, resource_id, idempotency_key) DO NOTHING
            """,
            (
                record["id"], record["task_id"], record.get("review_id"), record["user_id"], record["operation_type"],
                record["resource_type"], record["resource_id"], record["status"], record.get("before_snapshot_ref"),
                record.get("after_snapshot_ref"), record["idempotency_key"], record.get("undo_deadline"), record.get("audit_event_id"),
                record.get("error_code"), record.get("error_message"), record["created_at"], record["updated_at"],
            ),
        )
        item = self.get_operation(str(record["id"]))
        if item is None:
            item = self._row(
                """
                SELECT * FROM {schema}.agent_operation
                WHERE user_id = %s AND operation_type = %s AND resource_type = %s AND resource_id = %s AND idempotency_key = %s
                """,
                (record["user_id"], record["operation_type"], record["resource_type"], record["resource_id"], record["idempotency_key"]),
            )
        if item is None:
            raise RuntimeError("Agent 操作写入失败")
        return item

    def get_operation(self, operation_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        if user_id is None:
            return self._row("SELECT * FROM {schema}.agent_operation WHERE id = %s", (operation_id,))
        return self._row("SELECT * FROM {schema}.agent_operation WHERE id = %s AND user_id = %s", (operation_id, user_id))

    def update_operation(self, operation_id: str, **changes: Any) -> dict[str, Any] | None:
        return self._update("agent_operation", operation_id, changes)

    def list_operations(self, task_id: str) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM {schema}.agent_operation WHERE task_id = %s ORDER BY created_at ASC, id ASC", (task_id,))

    def create_folder(self, record: dict[str, Any]) -> dict[str, Any]:
        self._write(
            """
            INSERT INTO {schema}.agent_conversation_folder (id, user_id, name, sort_order, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (record["id"], record["user_id"], record["name"], record["sort_order"], record["created_at"], record["updated_at"]),
        )
        item = self.get_folder(str(record["id"]))
        if item is None:
            raise RuntimeError("Agent 文件夹写入失败")
        return item

    def get_folder(self, folder_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        if user_id is None:
            return self._row("SELECT * FROM {schema}.agent_conversation_folder WHERE id = %s", (folder_id,))
        return self._row("SELECT * FROM {schema}.agent_conversation_folder WHERE id = %s AND user_id = %s", (folder_id, user_id))

    def list_folders(self, user_id: str) -> list[dict[str, Any]]:
        return self._rows(
            "SELECT * FROM {schema}.agent_conversation_folder WHERE user_id = %s ORDER BY sort_order ASC, updated_at DESC, id ASC",
            (user_id,),
        )

    def update_folder(self, folder_id: str, **changes: Any) -> dict[str, Any] | None:
        return self._update("agent_conversation_folder", folder_id, changes)

    def delete_folder(self, folder_id: str, user_id: str) -> bool:
        with self._transaction() as cursor:
            cursor.execute(self._statement("SELECT id FROM {schema}.agent_conversation_folder WHERE id = %s AND user_id = %s FOR UPDATE"), (folder_id, user_id))
            if cursor.fetchone() is None:
                return False
            cursor.execute(self._statement("UPDATE {schema}.agent_task SET folder_id = NULL, updated_at = CURRENT_TIMESTAMP WHERE user_id = %s AND folder_id = %s"), (user_id, folder_id))
            cursor.execute(self._statement("DELETE FROM {schema}.agent_conversation_folder WHERE id = %s"), (folder_id,))
            return True

    def create_memory(self, record: dict[str, Any]) -> dict[str, Any]:
        self._write(
            """
            INSERT INTO {schema}.agent_memory_item
                (id, user_id, memory_type, namespace, scope_type, scope_id, subject_key, content, summary,
                 evidence_refs_json, source_task_id, source_tool_call_id, source_review_id, source_hash, status,
                 confidence, importance, sensitivity_level, consent_source, access_count, last_accessed_at,
                 valid_from, valid_until, deleted_at, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record["id"], record["user_id"], record["memory_type"], record["namespace"], record["scope_type"],
                record.get("scope_id"), record["subject_key"], record["content"], record["summary"],
                json_text(record.get("evidence_refs_json", [])), record.get("source_task_id"), record.get("source_tool_call_id"),
                record.get("source_review_id"), record["source_hash"], record["status"], record["confidence"],
                record["importance"], record["sensitivity_level"], record["consent_source"], record.get("access_count", 0),
                record.get("last_accessed_at"), record.get("valid_from"), record.get("valid_until"), record.get("deleted_at"),
                record["created_at"], record["updated_at"],
            ),
        )
        item = self.get_memory(str(record["id"]))
        if item is None:
            raise RuntimeError("Agent 记忆写入失败")
        return item

    def get_memory(self, memory_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        if user_id is None:
            return self._row("SELECT * FROM {schema}.agent_memory_item WHERE id = %s", (memory_id,))
        return self._row("SELECT * FROM {schema}.agent_memory_item WHERE id = %s AND user_id = %s", (memory_id, user_id))

    def list_memories(self, user_id: str, filters: dict[str, str]) -> list[dict[str, Any]]:
        clauses = ["user_id = %s"]
        params: list[Any] = [user_id]
        field_map = {"status": "status", "memory_type": "memory_type", "namespace": "namespace", "scope_type": "scope_type"}
        for key, column in field_map.items():
            value = filters.get(key)
            if value:
                clauses.append(f"{column} = %s")
                params.append(value)
        if not filters.get("status"):
            clauses.append("status <> 'DELETED'")
        return self._rows(
            f"SELECT * FROM {{schema}}.agent_memory_item WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC, id DESC",
            tuple(params),
        )

    def update_memory(self, memory_id: str, **changes: Any) -> dict[str, Any] | None:
        return self._update("agent_memory_item", memory_id, changes)

    def search_active_memories(self, user_id: str, query: str, limit: int = 5) -> list[dict[str, Any]]:
        pattern = f"%{query.strip()}%"
        return self._rows(
            """
            SELECT * FROM {schema}.agent_memory_item
            WHERE user_id = %s AND status = 'ACTIVE' AND deleted_at IS NULL
              AND COALESCE(sensitivity_level, 'LOW') <> 'HIGH'
              AND (summary ILIKE %s OR content ILIKE %s OR subject_key ILIKE %s)
            ORDER BY importance DESC, confidence DESC, updated_at DESC
            LIMIT %s
            """,
            (user_id, pattern, pattern, pattern, limit),
        )

    def insert_memory_version(self, record: dict[str, Any]) -> None:
        self._write(
            """
            INSERT INTO {schema}.agent_memory_version
                (id, memory_id, previous_memory_id, relation_type, decision, reason, decided_by, user_id, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record["id"], record["memory_id"], record.get("previous_memory_id"), record["relation_type"],
                record["decision"], record.get("reason"), record["decided_by"], record["user_id"], record["created_at"],
            ),
        )

    def insert_memory_audit(self, record: dict[str, Any]) -> None:
        self._write(
            """
            INSERT INTO {schema}.agent_memory_audit
                (id, memory_id, user_id, task_id, action, actor_type, before_hash, after_hash, summary, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record["id"], record.get("memory_id"), record["user_id"], record.get("task_id"), record["action"],
                record["actor_type"], record.get("before_hash"), record.get("after_hash"), record["summary"], record["created_at"],
            ),
        )

    def _update(self, table: str, item_id: str, changes: dict[str, Any]) -> dict[str, Any] | None:
        if not changes:
            return self._row(f"SELECT * FROM {{schema}}.{table} WHERE id = %s", (item_id,))
        allowed = {
            "agent_task": {"status", "title", "folder_id", "input_json", "plan_json", "draft_json", "final_json", "python_thread_id", "error_code", "error_message", "updated_at"},
            "agent_human_review": {"status", "decision_json", "reviewed_by", "reviewed_at", "updated_at"},
            "agent_operation": {"status", "before_snapshot_ref", "after_snapshot_ref", "undo_deadline", "audit_event_id", "error_code", "error_message", "updated_at"},
            "agent_conversation_folder": {"name", "sort_order", "updated_at"},
            "agent_memory_item": {"memory_type", "namespace", "scope_type", "scope_id", "subject_key", "content", "summary", "evidence_refs_json", "source_hash", "status", "confidence", "importance", "sensitivity_level", "consent_source", "access_count", "last_accessed_at", "valid_from", "valid_until", "deleted_at", "updated_at"},
        }
        selected = {key: value for key, value in changes.items() if key in allowed[table]}
        if not selected:
            return self._row(f"SELECT * FROM {{schema}}.{table} WHERE id = %s", (item_id,))
        fields = list(selected)
        assignments = ", ".join(f"{field} = %s" for field in fields)
        values = [json_text(value) if field in JSON_FIELDS else value for field, value in selected.items()]
        self._write(f"UPDATE {{schema}}.{table} SET {assignments} WHERE id = %s", tuple(values + [item_id]))
        return self._row(f"SELECT * FROM {{schema}}.{table} WHERE id = %s", (item_id,))

    @contextmanager
    def _transaction(self) -> Iterator[Any]:
        connection = self._connect()
        try:
            with connection:
                with connection.cursor() as cursor:
                    yield cursor
        finally:
            connection.close()

    def _rows(self, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        with self._transaction() as cursor:
            cursor.execute(self._statement(query), params)
            return [normalize_row(item) for item in cursor.fetchall()]

    def _row(self, query: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._transaction() as cursor:
            cursor.execute(self._statement(query), params)
            row = cursor.fetchone()
            return normalize_row(row) if row else None

    def _write(self, query: str, params: tuple[Any, ...]) -> None:
        with self._transaction() as cursor:
            cursor.execute(self._statement(query), params)

    def _statement(self, query: str) -> Any:
        from psycopg import sql

        return sql.SQL(query).format(schema=sql.Identifier(self._schema))

    def _connect(self) -> Any:
        if not self._database_url:
            raise RuntimeError("未配置 AUTH_DATABASE_URL、RAG_DATABASE_URL 或 DATABASE_URL")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("Agent PostgreSQL 仓储需要安装 psycopg[binary]") from exc
        return psycopg.connect(self._database_url, row_factory=dict_row)


def json_text(value: Any) -> str:
    """将 JSON 字段稳定序列化为既有 TEXT/JSONB 列可接受的 UTF-8 文本。"""
    if isinstance(value, str):
        return value
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=json_default)


def normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    """将 psycopg 行拷贝出来，保留时间对象给 FastAPI 编码器处理。"""
    return dict(row)


def json_default(value: Any) -> str:
    """为时间等 JSON 元数据提供可读的 ISO 序列化。"""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)
