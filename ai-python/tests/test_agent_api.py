"""统一 Agent 图与本地 gateway 的非 HTTP 契约测试。"""

from __future__ import annotations

from app.agent_runtime.repository import InMemoryAgentRepository
from app.agent_runtime.service import AgentRuntimeService
from app.schemas.agent import AgentTaskEvent
from agents.gateway.local_gateway import LocalAgentGateway
from agents.orchestration.pae_react_graph import (
    build_planning_plan,
    task_router_node,
    tool_adapter_node,
    web_search_enabled,
)


def test_local_gateway_persists_events_and_never_needs_external_callback() -> None:
    """图事件应直接写入 Python 任务投影和消息流。"""
    service = AgentRuntimeService(InMemoryAgentRepository())
    task = service.create_task(
        "7",
        {"taskType": "pure_read_query", "input": {"goal": "查询 Redis 学习 evidence"}},
    )
    gateway = LocalAgentGateway(task["id"], service)

    gateway.publish_event(
        AgentTaskEvent(
            eventType="TASK_STARTED",
            status="RUNNING",
            pythonThreadId=task["id"],
            draft={"message": "统一 Agent 图已启动"},
        )
    )
    retrieved = gateway.execute_read_tool(
        {"taskId": task["id"], "toolCallId": "tool-memory-1", "toolName": "agent_memory_retriever", "arguments": {"query": "Redis"}}
    )
    assert retrieved["status"] == "SUCCEEDED"
    assert retrieved["ownershipVerified"] is True
    assert retrieved["scope"] == "current_user"

    gateway.publish_event(
        AgentTaskEvent(
            eventType="TASK_COMPLETED",
            status="COMPLETED",
            pythonThreadId=task["id"],
            final={"answer": "当前资料暂无 evidence。", "evidenceIds": []},
        )
    )
    detail = service.get_task(task["id"], "7")
    assert detail["status"] == "COMPLETED"
    assert detail["final"]["answer"] == "当前资料暂无 evidence。"
    assert [item["sourceEventType"] for item in detail["messages"]][-2:] == ["TASK_STARTED", "TASK_COMPLETED"]


def test_local_gateway_requires_approved_review_before_mutation_and_supports_undo() -> None:
    """变更工具只接受当前任务的已批准审批，操作保留幂等撤销记录。"""
    service = AgentRuntimeService(InMemoryAgentRepository())
    task = service.create_task("7", {"taskType": "planning_task", "input": {"goal": "保存学习计划"}})
    gateway = LocalAgentGateway(task["id"], service)
    gateway.publish_event(
        AgentTaskEvent(
            eventType="REVIEW_REQUESTED",
            status="WAITING_CRUD_REVIEW",
            pythonThreadId=task["id"],
            reviewRequest={"id": "review-crud-1", "reviewType": "CRUD", "proposal": {"toolName": "jd_learning_plan_save"}},
        )
    )
    missing = gateway.execute_mutation_tool(
        {"taskId": task["id"], "toolCallId": "tool-mutation-1", "toolName": "jd_learning_plan_save", "approvalId": "review-crud-1", "idempotencyKey": "same-key"}
    )
    assert missing["status"] == "FAILED"
    assert missing["errorCode"] == "AGENT_MUTATION_REQUIRES_APPROVAL"

    service.decide_review(task["id"], "review-crud-1", "7", {"decision": "APPROVED", "comment": "可保存"})
    applied = gateway.execute_mutation_tool(
        {
            "taskId": task["id"],
            "toolCallId": "tool-mutation-2",
            "toolName": "jd_learning_plan_save",
            "approvalId": "review-crud-1",
            "operationId": "operation-1",
            "idempotencyKey": "same-key",
        }
    )
    assert applied["status"] == "SUCCEEDED"
    operation_id = applied["data"]["operationId"]
    undone = service.undo_operation(operation_id, "7", {"idempotencyKey": "undo-1"})
    assert undone["status"] == "UNDONE"


def test_local_gateway_persists_context_summary_for_worker_recovery() -> None:
    """压缩摘要必须写入任务事实表，并在新 gateway 实例中恢复。"""
    service = AgentRuntimeService(InMemoryAgentRepository())
    task = service.create_task("7", {"taskType": "pure_read_query", "input": {"goal": "继续讨论 RAG 融合"}})
    gateway = LocalAgentGateway(task["id"], service)

    saved = gateway.save_context_summary(
        task["id"],
        {
            "summaryId": "summary-1",
            "summaryType": "CONTEXT_COMPRESSION",
            "coveredMessageStartId": "message-1",
            "coveredMessageEndId": "message-2",
            "coveredMessageCount": 2,
            "rawTokenEstimate": 320,
            "compressedTokenEstimate": 80,
            "summary": {"rollingSummary": "已讨论 BM25、向量检索和 RRF 融合。", "keyFacts": [{"text": "RRF 用于融合"}]},
            "summaryText": "已讨论 BM25、向量检索和 RRF 融合。",
            "keyFacts": [{"text": "RRF 用于融合"}],
            "evidenceRefs": [{"type": "rag_evidence", "id": "evidence-1"}],
            "compressionModel": "deterministic",
            "status": "ACTIVE",
            "diagnostics": {"triggerNode": "planner"},
        },
    )
    restored = LocalAgentGateway(task["id"], service).restore_context(task["id"], summary_limit=6)
    detail = service.get_task(task["id"], "7")

    assert saved["id"] == "summary-1"
    assert restored["activeSummaries"][0]["summaryText"] == "已讨论 BM25、向量检索和 RRF 融合。"
    assert detail["summaryCount"] == 1
    assert detail["summaries"][0]["evidenceRefs"][0]["id"] == "evidence-1"


def test_in_memory_task_execution_lock_prevents_duplicate_worker_claim() -> None:
    """测试替身也要表达真实 PostgreSQL advisory lock 的单任务执行语义。"""
    repository = InMemoryAgentRepository()

    with repository.task_execution_lock("task-1") as first:
        with repository.task_execution_lock("task-1") as second:
            assert first is True
            assert second is False
    with repository.task_execution_lock("task-1") as released:
        assert released is True


def test_unified_graph_routes_and_free_explore_plan_are_python_local() -> None:
    """路由器保留只读/规划子图，联网失败时计划仍包含本地 RAG。"""
    read_state = task_router_node({"task_type": "pure_read_query", "task_input": {"workspaceMode": "read"}})
    planning_state = task_router_node({"task_type": "planning_task", "task_input": {"workspaceMode": "free_explore"}})
    task_input = {"goal": "获取外部岗位趋势并补充本地证据", "workspaceMode": "free_explore"}
    plan = build_planning_plan(task_input)

    assert read_state["subgraph"] == "read_only"
    assert planning_state["subgraph"] == "planning"
    assert web_search_enabled(task_input) is True
    assert [step["toolName"] for step in plan["steps"][:2]] == ["web_search_probe", "rag_query_probe_non_persistent"]
    assert "Python 本地 Gateway" in plan["guardrails"][0]


def test_tool_adapter_rejects_unapproved_or_unknown_tool_before_gateway_call() -> None:
    """图节点在 gateway 前拒绝未审批变更和非白名单工具。"""

    class GuardedGateway:
        def __init__(self) -> None:
            self.events: list[dict] = []
            self.called = False

        def execute_read_tool(self, _payload: dict) -> dict:
            self.called = True
            raise AssertionError("非法工具不应进入 gateway")

        def execute_mutation_tool(self, _payload: dict) -> dict:
            self.called = True
            raise AssertionError("未审批变更不应进入 gateway")

        def publish_event(self, event: AgentTaskEvent) -> None:
            self.events.append(event.model_dump(by_alias=True, exclude_none=True))

    gateway = GuardedGateway()
    mutation = tool_adapter_node(
        {"task_id": "task-1", "thread_id": "task-1", "current_action": {"toolName": "jd_learning_plan_save", "toolType": "MUTATION"}, "tool_calls": [], "observations": [], "tool_results": [], "react_trace": []},
        gateway,
    )
    forbidden = tool_adapter_node(
        {"task_id": "task-2", "thread_id": "task-2", "current_action": {"toolName": "direct_database_reader", "toolType": "READ"}, "tool_calls": [], "observations": [], "tool_results": [], "react_trace": []},
        gateway,
    )

    assert gateway.called is False
    assert mutation["error_code"] == "AGENT_MUTATION_REQUIRES_APPROVAL"
    assert forbidden["error_code"] == "AGENT_TOOL_FORBIDDEN"
