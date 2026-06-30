import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

AI_PYTHON_DIR = Path(__file__).resolve().parents[1]
if str(AI_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(AI_PYTHON_DIR))

os.environ["RAG_STORE_BACKEND"] = "memory"

from app.main import app


class FakeResponse:
    def __init__(self, payload: dict | None = None, status_code: int = 200) -> None:
        self.payload = payload or {}
        self.status_code = status_code
        self.content = b"{}"

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict:
        return self.payload


class FakeJavaClient:
    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        self.headers = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def post(self, url: str, json: dict, headers: dict) -> FakeResponse:
        assert "/internal/rag/" not in url
        assert headers["X-Agent-Internal-Token"] == "agent-secret"
        FakeJavaClient.calls.append({"url": url, "json": json})
        if url.endswith("/summaries"):
            return FakeResponse({"id": json.get("summaryId"), **json})
        if url.endswith("/api/internal/agent/tools/read"):
            if json["toolName"] == "agent_memory_retriever":
                return FakeResponse(
                    {
                        "taskId": json["taskId"],
                        "toolCallId": json["toolCallId"],
                        "toolName": json["toolName"],
                        "status": "SUCCEEDED",
                        "ownershipVerified": True,
                        "scope": "current_user_or_authorized",
                        "data": {
                            "memoryContext": [
                                {
                                    "memoryId": "agent-memory-1",
                                    "memoryType": "PREFERENCE",
                                    "namespace": "user_preference",
                                    "scope": "USER",
                                    "subjectKey": "answer_style",
                                    "summary": "用户偏好回答先给结论再列证据。",
                                    "score": 0.82,
                                }
                            ],
                            "memories": [],
                            "memoryCount": 1,
                        },
                        "diagnostics": {},
                        "retryable": False,
                    }
                )
            if json["toolName"] == "agent_memory_candidate_proposer":
                return FakeResponse(
                    {
                        "taskId": json["taskId"],
                        "toolCallId": json["toolCallId"],
                        "toolName": json["toolName"],
                        "status": "SUCCEEDED",
                        "ownershipVerified": True,
                        "scope": "current_user_or_authorized",
                        "data": {
                            "candidates": [
                                {
                                    "memoryType": "EPISODIC",
                                    "namespace": "agent_task",
                                    "scopeType": "USER",
                                    "subjectKey": "recent_task_insight",
                                    "content": "用户最近关注后端实习 JD 适配。",
                                    "summary": "后端实习 JD 适配关注 Java 和 RAG。",
                                    "confidence": 0.62,
                                    "importance": 0.56,
                                }
                            ],
                            "conflicts": [],
                            "provider": "fake",
                        },
                        "diagnostics": {},
                        "retryable": False,
                    }
                )
            if json["toolName"] == "web_search_probe":
                if json["arguments"]["query"] == "__fail_tavily__":
                    return FakeResponse(
                        {
                            "taskId": json["taskId"],
                            "toolCallId": json["toolCallId"],
                            "toolName": json["toolName"],
                            "status": "FAILED",
                            "ownershipVerified": True,
                            "scope": "current_user_or_authorized",
                            "data": {},
                            "diagnostics": {},
                            "retryable": False,
                            "errorCode": "AGENT_TAVILY_NOT_CONFIGURED",
                            "errorMessage": "未配置 Tavily API Key",
                        }
                    )
                return FakeResponse(
                    {
                        "taskId": json["taskId"],
                        "toolCallId": json["toolCallId"],
                        "toolName": json["toolName"],
                        "status": "SUCCEEDED",
                        "ownershipVerified": True,
                        "scope": "current_user_or_authorized",
                        "data": {
                            "query": json["arguments"]["query"],
                            "retrievedAt": "2026-06-21T16:40:00+08:00",
                            "results": [
                                {
                                    "title": "公司技术趋势",
                                    "sourceUrl": "https://example.com/trend",
                                    "summary": "外部参考摘要",
                                    "score": 0.88,
                                    "confidence": "HIGH",
                                    "retrievedAt": "2026-06-21T16:40:00+08:00",
                                }
                            ],
                            "resultCount": 1,
                        },
                        "diagnostics": {},
                        "retryable": False,
                    }
                )
            return FakeResponse(
                {
                    "taskId": json["taskId"],
                    "toolCallId": json["toolCallId"],
                    "toolName": json["toolName"],
                    "status": "SUCCEEDED",
                    "ownershipVerified": True,
                    "scope": "current_user_or_authorized",
                    "data": {
                        "answer": "Redis 证据集中在缓存淘汰和持久化。",
                        "expandedQueries": ["Redis 缓存淘汰", "Redis 持久化"],
                        "evidences": [
                            {
                                "evidenceId": "material-12-1",
                                "title": "Redis 笔记",
                                "snippet": "正文片段不应进入 Observation 摘要",
                            }
                        ],
                        "diagnostics": {"candidateCount": 4},
                    },
                    "diagnostics": {},
                    "retryable": False,
                }
            )
        if url.endswith("/api/internal/agent/tools/mutation/execute"):
            return FakeResponse(
                {
                    "taskId": json["taskId"],
                    "toolCallId": json["toolCallId"],
                    "toolName": json["toolName"],
                    "status": "SUCCEEDED",
                    "ownershipVerified": True,
                    "scope": "current_user_or_authorized",
                    "data": {
                        "operationId": json["operationId"],
                        "status": "APPLIED_UNDOABLE",
                        "beforeSnapshotRef": "agent-operation-snapshot:snapshot-before-1",
                        "afterSnapshotRef": "agent-operation-snapshot:snapshot-after-1",
                        "undoDeadline": "2026-06-21T16:20:00+08:00",
                    },
                    "diagnostics": {},
                    "retryable": False,
                }
            )
        return FakeResponse({"accepted": True})

    def get(self, url: str, params: dict, headers: dict) -> FakeResponse:
        assert "/internal/rag/" not in url
        assert headers["X-Agent-Internal-Token"] == "agent-secret"
        FakeJavaClient.calls.append({"url": url, "params": params})
        if url.endswith("/context"):
            long_content = "用户早期提到 Redis 缓存淘汰、持久化和 RAG evidence 对齐。" * 20
            return FakeResponse(
                {
                    "taskId": "agent-task",
                    "messageWindow": [
                        {
                            "id": f"agent-msg-{index}",
                            "role": "USER" if index % 2 == 0 else "ASSISTANT",
                            "messageType": "USER_GOAL" if index == 0 else "STATUS",
                            "content": long_content,
                            "createdAt": "2026-06-29T10:00:00+08:00",
                        }
                        for index in range(6)
                    ],
                    "activeSummaries": [],
                    "summarySegments": [],
                    "budgetMetadata": {
                        "promptTargetTokens": int(params.get("bestWindowTokens", 18000)),
                        "restoreSource": "postgresql",
                    },
                }
            )
        if url.endswith("/context/messages"):
            return FakeResponse([])
        return FakeResponse({})


class FakeQwenResult:
    """测试用千问结构化返回。"""

    def __init__(self, data: dict, model: str) -> None:
        self.data = data
        self.provider = "dashscope"
        self.model = model


class FakeQwenClient:
    """按节点返回预设 JSON 的测试客户端。"""

    def __init__(self, outputs: dict[str, dict]) -> None:
        self.outputs = outputs
        self.calls: list[dict] = []

    def complete_json(self, *, node: str, model: str, system_prompt: str, user_prompt: str) -> FakeQwenResult:
        self.calls.append({"node": node, "model": model, "system_prompt": system_prompt, "user_prompt": user_prompt})
        if node not in self.outputs:
            raise RuntimeError(f"未配置 fake qwen 节点：{node}")
        return FakeQwenResult(self.outputs[node], model)


class FakeSummaryClient:
    """记录上下文摘要保存请求的测试客户端。"""

    def __init__(
        self,
        restored_context: dict | None = None,
        recalled_messages: list[dict] | None = None,
        recall_error: Exception | None = None,
        save_error: Exception | None = None,
    ) -> None:
        self.saved_summaries: list[dict] = []
        self.recalled_context_calls: list[dict] = []
        self.events: list[dict] = []
        self.restored_context = restored_context or {}
        self.recalled_messages = recalled_messages
        self.recall_error = recall_error
        self.save_error = save_error

    def save_context_summary(self, task_id: str, payload: dict) -> dict:
        if self.save_error is not None:
            raise self.save_error
        self.saved_summaries.append({"taskId": task_id, "payload": payload})
        return {"id": payload.get("summaryId"), **payload}

    def restore_context(self, task_id: str, *, query: str = "", recent_limit: int = 12, summary_limit: int = 6, best_window_tokens: int = 18000) -> dict:
        return self.restored_context

    def recall_context_messages(self, task_id: str, params: dict) -> list[dict]:
        self.recalled_context_calls.append({"taskId": task_id, "params": params})
        if self.recall_error is not None:
            raise self.recall_error
        if self.recalled_messages is not None:
            return self.recalled_messages
        return [
            {"id": "recall-msg-1", "role": "USER", "messageType": "TEXT", "content": "早期用户追问 Redis 持久化细节"},
            {"id": "recall-msg-2", "role": "ASSISTANT", "messageType": "TEXT", "content": "早期回答引用了 RAG evidence"},
        ]

    def publish_event(self, event) -> None:
        self.events.append(event.model_dump(by_alias=True, exclude_none=True))


def event_calls() -> list[dict]:
    """读取 Java events 回写调用。"""
    return [call for call in FakeJavaClient.calls if call["url"].endswith("/events")]


def non_progress_events(calls: list[dict]) -> list[dict]:
    """过滤节点级流式进度，只保留业务完成、工具和审批事件。"""
    return [call for call in calls if not is_progress_event(call)]


def is_progress_event(call: dict) -> bool:
    payload = call["json"]
    draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else {}
    return str(payload.get("eventType") or "").startswith("AGENT_NODE_") and "node" in draft


def events_by_type(calls: list[dict], event_type: str) -> list[dict]:
    return [call for call in calls if call["json"].get("eventType") == event_type]


def tool_observation_events(calls: list[dict]) -> list[dict]:
    return events_by_type(calls, "TOOL_CALL_COMPLETED")


def final_draft_events(calls: list[dict]) -> list[dict]:
    return [call for call in events_by_type(calls, "DRAFT_UPDATED") if not is_progress_event(call)]


def test_agent_task_requires_internal_token(monkeypatch):
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)

    response = client.post(
        "/internal/agent/tasks",
        json={
            "taskId": "agent-task-1",
            "taskType": "pure_read_query",
            "input": {"goal": "Redis 学到了什么"},
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-1/events",
            "javaToolGatewayBaseUrl": "http://java",
            "threadId": "agent-task-1",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "AGENT_INTERNAL_TOKEN_INVALID"


def test_agent_task_accepts_local_shared_token_without_env(monkeypatch, tmp_path):
    """未配置环境变量时，Python 内部接口可使用本地共享文件令牌。"""
    import httpx

    FakeJavaClient.calls = []
    monkeypatch.delenv("EVIDENCE_AGENT_INTERNAL_TOKEN", raising=False)
    token_file = tmp_path / "agent-internal-token"
    token_file.write_text("agent-secret\n", encoding="utf-8")
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN_FILE", str(token_file))
    client = TestClient(app)
    monkeypatch.setattr(httpx, "Client", FakeJavaClient)

    response = client.post(
        "/internal/agent/tasks",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "agent-task-local-token",
            "taskType": "pure_read_query",
            "input": {"goal": "Redis 学到了什么"},
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-local-token/events",
            "javaToolGatewayBaseUrl": "http://java",
            "threadId": "agent-task-local-token",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"


def test_unified_graph_task_router_selects_subgraphs_without_old_entrypoints():
    from agents.orchestration.pae_react_graph import task_router_node

    read_state = task_router_node({"task_type": "pure_read_query", "task_input": {"workspaceMode": "general"}})
    planning_state = task_router_node({"task_type": "planning_task", "task_input": {"workspaceMode": "planning"}})

    assert read_state["subgraph"] == "read_only"
    assert planning_state["subgraph"] == "planning"


def test_free_explore_defaults_to_web_search_before_rag():
    """自由探索默认优先联网，再用 RAG 做本地证据补充或降级。"""
    from agents.orchestration.pae_react_graph import build_planning_plan, web_search_enabled

    task_input = {
        "goal": "数据库中没有这些内容，帮我获取外部资料",
        "workspaceMode": "free_explore",
    }

    plan = build_planning_plan(task_input)

    assert web_search_enabled(task_input) is True
    assert [step["toolName"] for step in plan["steps"][:2]] == [
        "web_search_probe",
        "rag_query_probe_non_persistent",
    ]
    assert plan["riskLevel"] == "MEDIUM"


def test_planner_uses_qwen_json_for_resume_rewrite_intent(monkeypatch):
    """Planner 可接收千问结构化计划并进入简历修改子图。"""
    import agents.orchestration.pae_react_graph as graph

    monkeypatch.setattr(
        graph,
        "get_agent_qwen_client",
        lambda: FakeQwenClient(
            {
                "planner": {
                    "title": "后端实习简历优化计划",
                    "steps": [
                        {
                            "description": "检索当前用户 evidence 支撑简历优化",
                            "toolName": "rag_query_probe_non_persistent",
                            "toolType": "READ",
                            "expectedOutput": "带 evidence 的适配观察",
                        }
                    ],
                    "tools": ["rag_query_probe_non_persistent"],
                    "internalSubgraphs": ["resume_rewrite_subgraph"],
                    "resumeRewriteIntent": True,
                    "requiresPlanReview": True,
                    "requiresOutputReview": True,
                    "riskLevel": "MEDIUM",
                    "guardrails": ["计划审批不授权写操作"],
                }
            }
        ),
    )
    state = graph.planner_node(
        {
            "task_type": "planning_task",
            "subgraph": "planning",
            "task_input": {"goal": "帮我优化简历匹配后端实习", "jobDescription": "Java 后端"},
            "status": "RUNNING",
        }
    )

    assert state["plan"]["resumeRewriteIntent"] is True
    assert state["plan"]["internalSubgraphs"] == ["resume_rewrite_subgraph"]
    assert state["llm_diagnostics"][0]["status"] == "used"


def test_planner_keeps_free_explore_web_search_when_llm_only_returns_rag(monkeypatch):
    """LLM Planner 漏掉联网步骤时，sanitizer 仍按自由探索策略补回 web_search_probe。"""
    import agents.orchestration.pae_react_graph as graph

    monkeypatch.setattr(
        graph,
        "get_agent_qwen_client",
        lambda: FakeQwenClient(
            {
                "planner": {
                    "title": "自由探索计划",
                    "steps": [
                        {
                            "description": "检索当前用户 evidence",
                            "toolName": "rag_query_probe_non_persistent",
                            "toolType": "READ",
                            "expectedOutput": "本地 evidence",
                        }
                    ],
                    "tools": ["rag_query_probe_non_persistent"],
                    "internalSubgraphs": [],
                    "resumeRewriteIntent": False,
                    "requiresPlanReview": True,
                    "requiresOutputReview": True,
                    "riskLevel": "LOW",
                    "guardrails": ["计划审批不授权写操作"],
                }
            }
        ),
    )
    state = graph.planner_node(
        {
            "task_type": "planning_task",
            "subgraph": "planning",
            "task_input": {
                "goal": "自由探索一个新学习主题",
                "workspaceMode": "free_explore",
            },
            "status": "RUNNING",
        }
    )

    assert [step["toolName"] for step in state["plan"]["steps"][:2]] == [
        "web_search_probe",
        "rag_query_probe_non_persistent",
    ]
    assert "web_search_probe" in state["plan"]["tools"]
    assert state["llm_diagnostics"][0]["status"] == "used"


def test_planner_rejects_illegal_llm_tool_and_falls_back(monkeypatch):
    """Planner 输出非法工具名时走确定性 fallback。"""
    import agents.orchestration.pae_react_graph as graph

    monkeypatch.setattr(
        graph,
        "get_agent_qwen_client",
        lambda: FakeQwenClient(
            {
                "planner": {
                    "title": "非法计划",
                    "steps": [{"description": "绕过网关", "toolName": "direct_database_writer", "toolType": "READ"}],
                }
            }
        ),
    )
    state = graph.planner_node(
        {
            "task_type": "pure_read_query",
            "subgraph": "read_only",
            "task_input": {"question": "Redis 学到了什么"},
            "status": "RUNNING",
        }
    )

    assert state["plan"]["steps"][0]["toolName"] == "rag_query_probe_non_persistent"
    assert state["llm_diagnostics"][0]["status"].startswith("fallback:")


def test_executor_rejects_llm_mutation_tool(monkeypatch):
    """Executor 不能让千问选择 mutation 工具。"""
    import agents.orchestration.pae_react_graph as graph

    monkeypatch.setattr(
        graph,
        "get_agent_qwen_client",
        lambda: FakeQwenClient(
            {
                "executor": {
                    "toolName": "jd_learning_plan_save",
                    "toolType": "MUTATION",
                    "arguments": {},
                    "reason": "尝试保存",
                }
            }
        ),
    )
    state = graph.executor_node(
        {
            "task_id": "agent-task-llm-executor",
            "thread_id": "agent-task-llm-executor",
            "task_type": "planning_task",
            "subgraph": "planning",
            "task_input": {"goal": "生成学习计划"},
            "user_goal": "生成学习计划",
            "status": "RUNNING",
            "plan": {
                "steps": [
                    {
                        "description": "检索 evidence",
                        "toolName": "rag_query_probe_non_persistent",
                        "toolType": "READ",
                    }
                ]
            },
            "current_step_index": 0,
            "observations": [],
            "react_trace": [],
        }
    )

    assert state["current_action"]["toolName"] == "rag_query_probe_non_persistent"
    assert state["current_action"]["toolType"] == "READ"
    assert state["llm_diagnostics"][0]["status"].startswith("fallback:")


def test_executor_over_budget_persists_context_summary(monkeypatch):
    """非 planner 节点超窗时压缩 Java 恢复的候选窗口，而不是最近原文窗口。"""
    import agents.orchestration.pae_react_graph as graph

    monkeypatch.setattr(
        graph,
        "get_agent_qwen_client",
        lambda: FakeQwenClient({"executor": {"toolName": "rag_query_probe_non_persistent", "toolType": "READ", "arguments": {}}}),
    )
    monkeypatch.setenv("AGENT_CONTEXT_COMPRESSION_THRESHOLD_RATIO", "0.5")
    long_content = "用户早期反复讨论 Redis 持久化、缓存淘汰、RAG evidence 对齐和简历改写约束。" * 180
    fake_client = FakeSummaryClient(
        {
            "messageWindow": [
                {"id": "recent-msg-1", "role": "USER", "messageType": "TEXT", "content": "最近一轮用户问题"},
                {"id": "recent-msg-2", "role": "ASSISTANT", "messageType": "TEXT", "content": "最近一轮回答"},
            ],
            "compressionCandidateMessages": [
                {
                    "id": f"candidate-msg-{index}",
                    "role": "USER" if index % 2 == 0 else "ASSISTANT",
                    "messageType": "TEXT",
                    "content": long_content,
                }
                for index in range(4)
            ],
            "activeSummaries": [],
            "summarySegments": [],
            "budgetMetadata": {
                "promptTargetTokens": 4000,
                "restoreSource": "postgresql",
                "compressionCandidateCount": 4,
                "uncompressedMessageCount": 18,
            },
        }
    )
    restored = graph.context_restore_node(
        {
            "task_id": "agent-task-executor-compression",
            "thread_id": "agent-task-executor-compression",
            "task_type": "planning_task",
            "subgraph": "planning",
            "task_input": {"goal": "继续分析后端实习 JD"},
            "user_goal": "继续分析后端实习 JD",
            "status": "RUNNING",
            "context_budget": {"bestWindowTokens": 4000, "restoreSource": "postgresql"},
            "plan": {
                "steps": [
                    {
                        "description": "检索本地 evidence",
                        "toolName": "rag_query_probe_non_persistent",
                        "toolType": "READ",
                    }
                ]
            },
            "current_step_index": 0,
            "observations": [],
            "react_trace": [],
            "llm_diagnostics": [],
        },
        fake_client,
    )
    state = graph.executor_node(restored, fake_client)

    assert fake_client.saved_summaries
    assert fake_client.saved_summaries[0]["taskId"] == "agent-task-executor-compression"
    saved = fake_client.saved_summaries[0]["payload"]
    assert saved["status"] in {"ACTIVE", "HIGH_LOSS_RISK"}
    assert saved["coveredMessageStartId"] == "candidate-msg-0"
    assert saved["coveredMessageEndId"] == "candidate-msg-3"
    assert saved["coveredMessageCount"] == 4
    assert saved["coveredMessageStartId"] != "recent-msg-1"
    assert state["active_summary_id"] == saved["summaryId"]
    assert state["compression_candidate_messages"] == []
    assert any(item["node"] == "conversation_compression" and item["status"] == "saved" for item in state["llm_diagnostics"])


def test_context_compression_rewrites_hallucinated_covered_range(monkeypatch):
    """压缩摘要覆盖范围必须来自候选窗口，不能信任模型幻觉 message id。"""
    import agents.orchestration.pae_react_graph as graph

    monkeypatch.setattr(
        graph,
        "get_agent_qwen_client",
        lambda: FakeQwenClient(
            {
                "executor": {"toolName": "rag_query_probe_non_persistent", "toolType": "READ", "arguments": {}},
                "conversation_compression": {
                    "rollingSummary": "模型返回了摘要，但覆盖范围包含幻觉 ID。",
                    "keyFacts": [{"text": "用户讨论 Redis 与 RAG evidence 对齐", "source": "candidate"}],
                    "openQuestions": [],
                    "decisions": [],
                    "userPreferences": [],
                    "taskState": {"node": "executor", "status": "RUNNING"},
                    "toolFindings": [],
                    "evidenceRefs": [],
                    "lastRawMessageIds": ["ghost-msg", "candidate-msg-1"],
                    "coveredMessageRange": {"startId": "ghost-start", "endId": "ghost-end"},
                    "compressionVersion": 1,
                    "confidence": 0.8,
                    "lossRisk": "LOW",
                },
            }
        ),
    )
    monkeypatch.setenv("AGENT_CONTEXT_COMPRESSION_THRESHOLD_RATIO", "0.5")
    long_content = "早期上下文讨论 Redis 持久化、缓存淘汰、RAG evidence 对齐和简历改写约束。" * 220
    fake_client = FakeSummaryClient(
        {
            "messageWindow": [{"id": "recent-msg-1", "role": "USER", "messageType": "TEXT", "content": "最近问题"}],
            "compressionCandidateMessages": [
                {"id": f"candidate-msg-{index}", "role": "USER", "messageType": "TEXT", "content": long_content}
                for index in range(3)
            ],
            "activeSummaries": [],
            "summarySegments": [],
            "budgetMetadata": {
                "promptTargetTokens": 4000,
                "restoreSource": "postgresql",
                "compressionCandidateCount": 3,
                "uncompressedMessageCount": 12,
            },
        }
    )
    restored = graph.context_restore_node(
        {
            "task_id": "agent-task-hallucinated-range",
            "thread_id": "agent-task-hallucinated-range",
            "task_type": "planning_task",
            "subgraph": "planning",
            "task_input": {"goal": "继续分析后端实习 JD"},
            "user_goal": "继续分析后端实习 JD",
            "status": "RUNNING",
            "context_budget": {"bestWindowTokens": 4000, "restoreSource": "postgresql"},
            "plan": {"steps": [{"description": "检索 evidence", "toolName": "rag_query_probe_non_persistent", "toolType": "READ"}]},
            "current_step_index": 0,
            "observations": [],
            "react_trace": [],
            "llm_diagnostics": [],
        },
        fake_client,
    )

    graph.executor_node(restored, fake_client)

    saved = fake_client.saved_summaries[0]["payload"]
    assert saved["coveredMessageStartId"] == "candidate-msg-0"
    assert saved["coveredMessageEndId"] == "candidate-msg-2"
    assert saved["summary"]["coveredMessageRange"] == {"startId": "candidate-msg-0", "endId": "candidate-msg-2"}
    assert saved["summary"]["lastRawMessageIds"] == ["candidate-msg-1"]
    assert "ghost-msg" not in saved["summary"]["lastRawMessageIds"]


def test_prepare_llm_payload_recalls_summary_context_once():
    """长会话恢复时，LLM prompt 会通过 Java 回捞摘要覆盖范围附近少量原文。"""
    import agents.orchestration.pae_react_graph as graph

    fake_client = FakeSummaryClient()
    state = {
        "task_id": "agent-task-recall",
        "thread_id": "agent-task-recall",
        "task_type": "planning_task",
        "task_input": {"goal": "追问 Redis 持久化 evidence 细节"},
        "user_goal": "追问 Redis 持久化 evidence 细节",
        "status": "RUNNING",
        "context_budget": {"bestWindowTokens": 800, "restoreSource": "postgresql"},
        "context_messages": [{"id": "recent-msg-1", "role": "USER", "messageType": "TEXT", "content": "最近问题"}],
        "context_summaries": [
            {
                "id": "summary-redis-1",
                "summaryText": "早期讨论 Redis 持久化和 RAG evidence 对齐。",
                "keyFacts": [{"text": "用户关注 Redis 持久化"}],
                "evidenceRefs": [{"type": "rag_evidence", "id": "material-12-1"}],
                "coveredMessageStartId": "msg-1",
                "coveredMessageEndId": "msg-8",
                "status": "ACTIVE",
            }
        ],
        "compression_candidate_messages": [],
        "llm_diagnostics": [],
    }

    payload = graph.prepare_llm_payload(state, "executor", {"node": "executor"}, fake_client)
    payload_again = graph.prepare_llm_payload(state, "executor", {"node": "executor"}, fake_client)

    assert len(fake_client.recalled_context_calls) == 1
    call = fake_client.recalled_context_calls[0]
    assert call["taskId"] == "agent-task-recall"
    assert call["params"]["summaryId"] == "summary-redis-1"
    assert call["params"]["coveredMessageStartId"] == "msg-1"
    assert call["params"]["coveredMessageEndId"] == "msg-8"
    assert call["params"]["before"] == 1
    assert call["params"]["after"] == 1
    assert call["params"]["limit"] == 6
    assert payload["restoredContext"]["recalledMessages"][0]["id"] == "recall-msg-1"
    assert payload_again["restoredContext"]["recalledMessages"][1]["id"] == "recall-msg-2"
    assert any(item["node"] == "context_recall" and item["status"] == "recalled:executor" for item in state["llm_diagnostics"])


def test_context_compression_save_failure_does_not_publish_unpersisted_summary(monkeypatch):
    """摘要保存失败时，不把未落库摘要注入后续 prompt。"""
    import agents.orchestration.pae_react_graph as graph

    monkeypatch.setattr(
        graph,
        "get_agent_qwen_client",
        lambda: FakeQwenClient(
            {
                "conversation_compression": {
                    "rollingSummary": "未能落库的摘要",
                    "keyFacts": [{"text": "候选窗口事实", "source": "candidate"}],
                    "lastRawMessageIds": ["candidate-msg-1"],
                    "coveredMessageRange": {"startId": "candidate-msg-0", "endId": "candidate-msg-1"},
                    "compressionVersion": 1,
                    "lossRisk": "LOW",
                }
            }
        ),
    )
    monkeypatch.setenv("AGENT_CONTEXT_COMPRESSION_THRESHOLD_RATIO", "0.5")
    long_content = "早期上下文讨论 Redis 持久化、缓存淘汰和 RAG evidence 对齐。" * 240
    candidates = [
        {"id": f"candidate-msg-{index}", "role": "USER", "messageType": "TEXT", "content": long_content}
        for index in range(2)
    ]
    state = {
        "task_id": "agent-task-save-failed",
        "thread_id": "agent-task-save-failed",
        "task_type": "planning_task",
        "task_input": {"goal": "继续分析 Redis evidence"},
        "user_goal": "继续分析 Redis evidence",
        "status": "RUNNING",
        "context_budget": {"bestWindowTokens": 800, "restoreSource": "postgresql"},
        "context_messages": [{"id": "recent-msg-1", "role": "USER", "messageType": "TEXT", "content": "最近问题"}],
        "context_summaries": [{"id": "persisted-summary-1", "summaryText": "已落库旧摘要", "status": "ACTIVE"}],
        "compression_candidate_messages": candidates.copy(),
        "active_summary_id": "persisted-summary-1",
        "llm_diagnostics": [],
    }
    fake_client = FakeSummaryClient(save_error=RuntimeError("Java 保存失败"))

    payload = graph.prepare_llm_payload(state, "executor", {"node": "executor"}, fake_client)

    assert any(
        item["node"] == "conversation_compression" and item["status"].startswith("save_failed:")
        for item in state["llm_diagnostics"]
    )
    assert state["active_summary_id"] == "persisted-summary-1"
    assert state["compression_candidate_messages"] == candidates
    assert [item["id"] for item in state["context_summaries"]] == ["persisted-summary-1"]
    assert [item["id"] for item in payload["restoredContext"]["summarySegments"]] == ["persisted-summary-1"]
    assert not fake_client.saved_summaries


def test_prepare_llm_payload_marks_empty_recall_attempt_once():
    """Java 回捞空返回时也记录 attempt，避免后续 LLM 节点反复重试。"""
    import agents.orchestration.pae_react_graph as graph

    fake_client = FakeSummaryClient(recalled_messages=[])
    state = {
        "task_id": "agent-task-empty-recall",
        "thread_id": "agent-task-empty-recall",
        "task_type": "planning_task",
        "task_input": {"goal": "追问 Redis 持久化 evidence 细节"},
        "user_goal": "追问 Redis 持久化 evidence 细节",
        "status": "RUNNING",
        "context_budget": {"bestWindowTokens": 4000, "restoreSource": "postgresql"},
        "context_messages": [{"id": "recent-msg-1", "role": "USER", "messageType": "TEXT", "content": "最近问题"}],
        "context_summaries": [
            {
                "id": "summary-empty-1",
                "summaryText": "早期讨论 Redis 持久化。",
                "keyFacts": [{"text": "Redis 持久化"}],
                "coveredMessageStartId": "msg-1",
                "coveredMessageEndId": "msg-8",
                "status": "ACTIVE",
            }
        ],
        "compression_candidate_messages": [],
        "llm_diagnostics": [],
    }

    payload = graph.prepare_llm_payload(state, "executor", {"node": "executor"}, fake_client)
    payload_again = graph.prepare_llm_payload(state, "answer_writer", {"node": "answer_writer"}, fake_client)

    assert len(fake_client.recalled_context_calls) == 1
    assert payload["restoredContext"]["recalledMessages"] == []
    assert payload_again["restoredContext"]["recalledMessages"] == []
    assert state["context_recall_keys"] == ["summary:executor"]
    assert any(item["node"] == "context_recall" and item["status"] == "empty:executor" for item in state["llm_diagnostics"])


def test_conversation_title_prompt_truncates_long_goal(monkeypatch):
    """Conversation Title 只能把截断后的短文本交给 Qwen，避免入口节点绕过上下文预算。"""
    import agents.orchestration.pae_react_graph as graph

    fake_qwen = FakeQwenClient({"conversation_title": {"conversationTitle": "后端实习准备"}})
    monkeypatch.setattr(graph, "get_agent_qwen_client", lambda: fake_qwen)
    long_goal = "帮我分析后端实习准备。" + ("超长目标" * 200) + "TAIL_SHOULD_NOT_APPEAR"

    state = graph.conversation_title_node(
        {
            "task_id": "agent-task-title",
            "thread_id": "agent-task-title",
            "task_type": "pure_read_query",
            "task_input": {"goal": long_goal},
            "user_goal": long_goal,
            "status": "RUNNING",
        },
        client=None,
    )

    prompt = fake_qwen.calls[0]["user_prompt"]
    assert "TAIL_SHOULD_NOT_APPEAR" not in prompt
    assert "inputTruncated" in prompt
    assert "帮我分析后端实习准备" in prompt
    assert state["llm_diagnostics"][0]["node"] == "conversation_title"


def test_acceptance_skips_empty_llm_action_without_loop(monkeypatch):
    """Executor 返回空 action 时验收节点推进步骤，避免空转循环。"""
    import agents.orchestration.pae_react_graph as graph

    monkeypatch.setattr(
        graph,
        "get_agent_qwen_client",
        lambda: FakeQwenClient({"executor": {"toolName": "", "toolType": "READ", "arguments": {}, "reason": "无需工具"}}),
    )
    state = {
        "task_id": "agent-task-empty-action",
        "thread_id": "agent-task-empty-action",
        "task_type": "pure_read_query",
        "subgraph": "read_only",
        "task_input": {"question": "只读问答"},
        "user_goal": "只读问答",
        "status": "RUNNING",
        "plan": {
            "steps": [
                {
                    "description": "读取证据",
                    "toolName": "rag_query_probe_non_persistent",
                    "toolType": "READ",
                }
            ]
        },
        "current_step_index": 0,
        "observations": [],
        "tool_results": [],
        "tool_calls": [],
        "react_trace": [],
        "llm_diagnostics": [],
    }

    executed = graph.executor_node(state)
    assert executed["current_action"] == {}

    accepted = graph.acceptance_node(executed, FakeJavaClient)

    assert accepted["current_step_index"] == 1
    assert accepted["observations"][0]["status"] == "SKIPPED"
    assert graph.route_after_acceptance(accepted) == "answer_writer"


def test_acceptance_repair_decision_routes_to_repair(monkeypatch):
    """验收 LLM 要求补救时，路由必须进入 repair。"""
    import agents.orchestration.pae_react_graph as graph

    monkeypatch.setattr(
        graph,
        "get_agent_qwen_client",
        lambda: FakeQwenClient({"acceptance": {"decision": "REPAIR", "complete": False, "reason": "证据不足"}}),
    )
    state = graph.acceptance_node(
        {
            "task_id": "agent-task-acceptance-repair",
            "thread_id": "agent-task-acceptance-repair",
            "task_type": "pure_read_query",
            "subgraph": "read_only",
            "task_input": {"question": "只读问答"},
            "user_goal": "只读问答",
            "status": "RUNNING",
            "plan": {"steps": []},
            "completion_criteria": ["返回回答"],
            "current_step_index": 0,
            "current_action": {},
            "observations": [],
            "tool_calls": [],
            "tool_results": [],
            "llm_diagnostics": [],
        },
        FakeJavaClient,
    )

    assert state["status"] == "TOOL_FAILED"
    assert state["verifier_result"]["requiresRepair"] is True
    assert graph.route_after_acceptance(state) == "repair"


def test_graph_recursion_limit_publishes_failed_event(monkeypatch):
    """统一图超过最大深度时回写失败事件，提示用户重新规划或缩小目标。"""
    import agents.orchestration.pae_react_graph as graph
    from langgraph.errors import GraphRecursionError

    class RecursingGraph:
        def invoke(self, state: dict, config: dict) -> dict:
            assert config["recursion_limit"] == graph.AGENT_GRAPH_RECURSION_LIMIT
            raise GraphRecursionError("recursion limit")

    class EventClient:
        def __init__(self) -> None:
            self.events = []

        def publish_event(self, event) -> None:
            self.events.append(event.model_dump(by_alias=True, exclude_none=True))

    client = EventClient()
    monkeypatch.setattr(graph, "build_unified_graph", lambda _client: RecursingGraph())
    result = graph.invoke_unified_graph_with_limit(
        {
            "task_id": "agent-task-recursion",
            "thread_id": "agent-task-recursion",
            "status": "RUNNING",
            "llm_diagnostics": [],
        },
        client,
    )

    assert result["status"] == "FAILED"
    assert result["error_code"] == "AGENT_GRAPH_RECURSION_LIMIT"
    assert str(graph.AGENT_GRAPH_RECURSION_LIMIT) in result["error_message"]
    assert client.events[0]["eventType"] == "TASK_FAILED"
    assert client.events[0]["errorCode"] == "AGENT_GRAPH_RECURSION_LIMIT"


def test_agent_task_uses_java_gateway_and_callbacks(monkeypatch):
    import httpx

    FakeJavaClient.calls = []
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    monkeypatch.setattr(httpx, "Client", FakeJavaClient)

    response = client.post(
        "/internal/agent/tasks",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "agent-task-1",
            "taskType": "pure_read_query",
            "input": {
                "goal": "Redis 学到了什么",
                "topK": 3,
                "toolHints": ["rag_query_probe_non_persistent"],
                "metadataFilter": {"documentType": "markdown"},
            },
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-1/events",
            "javaToolGatewayBaseUrl": "http://java",
            "threadId": "agent-task-1",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    tool_calls = [call for call in FakeJavaClient.calls if call["url"].endswith("/api/internal/agent/tools/read")]
    calls = event_calls()
    assert [call["json"]["toolName"] for call in tool_calls] == ["agent_memory_retriever", "rag_query_probe_non_persistent"]
    assert tool_calls[1]["json"]["arguments"]["metadataFilter"] == {"documentType": "markdown"}
    assert [event["json"]["eventType"] for event in non_progress_events(calls)] == [
        "TASK_STARTED",
        "TOOL_CALL_COMPLETED",
        "TASK_COMPLETED",
    ]
    assert any(call["json"]["draft"].get("node") == "planner" for call in calls if is_progress_event(call))
    observation = tool_observation_events(calls)[0]["json"]["toolCall"]["response"]
    assert observation["evidenceCount"] == 1
    assert "正文片段" not in str(observation)


def test_planning_task_requests_plan_review_before_tool_calls(monkeypatch):
    import httpx

    FakeJavaClient.calls = []
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    monkeypatch.setattr(httpx, "Client", FakeJavaClient)

    response = client.post(
        "/internal/agent/tasks",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "agent-task-plan",
            "taskType": "planning_task",
            "input": {
                "goal": "分析后端实习 JD 适配度",
                "jobDescription": "要求 Java、Spring Boot、Redis 和 RAG 项目经验",
                "resumeText": "做过多模态 RAG 项目，熟悉 Java",
            },
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-plan/events",
            "javaToolGatewayBaseUrl": "http://java",
            "threadId": "agent-task-plan",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    tool_calls = [call for call in FakeJavaClient.calls if call["url"].endswith("/api/internal/agent/tools/read")]
    calls = event_calls()
    assert tool_calls == []
    business_events = non_progress_events(calls)
    assert [event["json"]["eventType"] for event in business_events] == ["TASK_STARTED", "REVIEW_REQUESTED"]
    assert any(call["json"]["draft"].get("node") == "planner" for call in calls if is_progress_event(call))
    assert business_events[-1]["json"]["reviewRequest"]["reviewType"] == "PLAN"
    assert business_events[-1]["json"]["draft"]["message"] == "规划器已生成执行路线，等待用户批准或要求修改。"


def test_planning_resume_uses_java_gateway_and_requests_output_review(monkeypatch):
    import httpx

    FakeJavaClient.calls = []
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    monkeypatch.setattr(httpx, "Client", FakeJavaClient)

    response = client.post(
        "/internal/agent/tasks/agent-task-plan/resume",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "agent-task-plan",
            "taskType": "planning_task",
            "threadId": "agent-task-plan",
            "reviewType": "PLAN",
            "decision": "APPROVED",
            "decisionPayload": {"comment": "同意继续"},
            "input": {
                "goal": "分析后端实习 JD 适配度",
                "jobDescription": "要求 Java、Spring Boot、Redis 和 RAG 项目经验",
                "resumeText": "做过多模态 RAG 项目，熟悉 Java",
                "topK": 3,
            },
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-plan/events",
            "javaToolGatewayBaseUrl": "http://java",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    tool_calls = [call for call in FakeJavaClient.calls if call["url"].endswith("/api/internal/agent/tools/read")]
    calls = event_calls()
    assert [call["json"]["toolName"] for call in tool_calls] == [
        "agent_memory_retriever",
        "rag_query_probe_non_persistent",
        "agent_memory_candidate_proposer",
    ]
    business_events = non_progress_events(calls)
    assert [event["json"]["eventType"] for event in business_events] == [
        "TOOL_CALL_COMPLETED",
        "DRAFT_UPDATED",
        "REVIEW_REQUESTED",
    ]
    assert business_events[-1]["json"]["reviewRequest"]["reviewType"] == "OUTPUT"
    draft = final_draft_events(calls)[-1]["json"]["draft"]
    assert draft["alignment"][0]["status"] == "supported"
    assert draft["pendingMemoryCandidates"][0]["summary"] == "后端实习 JD 适配关注 Java 和 RAG。"
    assert draft["resumeRewrite"] == {}


def test_planning_resume_enters_resume_rewrite_subgraph_when_planner_detects_intent(monkeypatch):
    import httpx

    FakeJavaClient.calls = []
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    monkeypatch.setattr(httpx, "Client", FakeJavaClient)

    response = client.post(
        "/internal/agent/tasks/agent-task-resume-rewrite/resume",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "agent-task-resume-rewrite",
            "taskType": "planning_task",
            "threadId": "agent-task-resume-rewrite",
            "reviewType": "PLAN",
            "decision": "APPROVED",
            "decisionPayload": {"comment": "同意继续"},
            "input": {
                "goal": "根据这个后端实习 JD 优化简历",
                "jobDescription": "要求 Java、Spring Boot、Redis 和 RAG 项目经验",
                "resumeText": "做过多模态 RAG 项目，熟悉 Java",
                "topK": 3,
            },
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-resume-rewrite/events",
            "javaToolGatewayBaseUrl": "http://java",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    tool_calls = [call for call in FakeJavaClient.calls if call["url"].endswith("/api/internal/agent/tools/read")]
    calls = event_calls()
    assert [call["json"]["toolName"] for call in tool_calls] == [
        "agent_memory_retriever",
        "rag_query_probe_non_persistent",
        "agent_memory_candidate_proposer",
    ]
    draft = final_draft_events(calls)[-1]["json"]["draft"]
    resume_rewrite = draft["resumeRewrite"]
    assert resume_rewrite["status"] == "PENDING_REVIEW"
    assert resume_rewrite["toolName"] == "resume_rewrite_subgraph"
    assert resume_rewrite["requiresApproval"] is True
    assert resume_rewrite["subgraphResult"]["accepted"] is True
    assert resume_rewrite["patches"][0]["status"] == "PENDING_REVIEW"


def test_planning_resume_with_web_search_keeps_local_rag_flow(monkeypatch):
    import httpx

    FakeJavaClient.calls = []
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    monkeypatch.setattr(httpx, "Client", FakeJavaClient)

    response = client.post(
        "/internal/agent/tasks/agent-task-plan/resume",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "agent-task-plan",
            "taskType": "planning_task",
            "threadId": "agent-task-plan",
            "reviewType": "PLAN",
            "decision": "APPROVED",
            "decisionPayload": {"comment": "同意继续"},
            "input": {
                "goal": "分析后端实习 JD 适配度",
                "jobDescription": "要求 Java、Spring Boot、Redis 和 RAG 项目经验",
                "resumeText": "做过多模态 RAG 项目，熟悉 Java",
                "enableWebSearch": True,
                "toolHints": ["web_search_probe"],
            },
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-plan/events",
            "javaToolGatewayBaseUrl": "http://java",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    tool_calls = [call for call in FakeJavaClient.calls if call["url"].endswith("/api/internal/agent/tools/read")]
    calls = event_calls()
    assert [call["json"]["toolName"] for call in tool_calls] == [
        "agent_memory_retriever",
        "web_search_probe",
        "rag_query_probe_non_persistent",
        "agent_memory_candidate_proposer",
    ]
    assert [event["json"]["eventType"] for event in non_progress_events(calls)] == [
        "TOOL_CALL_COMPLETED",
        "TOOL_CALL_COMPLETED",
        "DRAFT_UPDATED",
        "REVIEW_REQUESTED",
    ]
    assert final_draft_events(calls)[-1]["json"]["draft"]["webReferences"][0]["sourceUrl"] == "https://example.com/trend"


def test_planning_resume_degrades_to_local_rag_when_web_search_unavailable(monkeypatch):
    import httpx

    FakeJavaClient.calls = []
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    monkeypatch.setattr(httpx, "Client", FakeJavaClient)

    response = client.post(
        "/internal/agent/tasks/agent-task-plan/resume",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "agent-task-plan",
            "taskType": "planning_task",
            "threadId": "agent-task-plan",
            "reviewType": "PLAN",
            "decision": "APPROVED",
            "decisionPayload": {"comment": "同意继续"},
            "input": {
                "goal": "分析后端实习 JD 适配度",
                "jobDescription": "要求 Java、Spring Boot、Redis 和 RAG 项目经验",
                "resumeText": "做过多模态 RAG 项目，熟悉 Java",
                "enableWebSearch": True,
                "webSearchQuery": "__fail_tavily__",
            },
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-plan/events",
            "javaToolGatewayBaseUrl": "http://java",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    tool_calls = [call for call in FakeJavaClient.calls if call["url"].endswith("/api/internal/agent/tools/read")]
    calls = event_calls()
    assert [call["json"]["toolName"] for call in tool_calls] == [
        "agent_memory_retriever",
        "web_search_probe",
        "rag_query_probe_non_persistent",
        "agent_memory_candidate_proposer",
    ]
    first_tool = tool_observation_events(calls)[0]["json"]["toolCall"]
    assert first_tool["status"] == "FAILED"
    assert first_tool["errorCode"] == "AGENT_TAVILY_NOT_CONFIGURED"
    assert non_progress_events(calls)[-1]["json"]["reviewRequest"]["reviewType"] == "OUTPUT"
    draft = final_draft_events(calls)[-1]["json"]["draft"]
    assert draft["webReferences"] == []
    assert draft["alignment"][0]["status"] == "supported"


def test_planning_resume_builds_template_content_candidate_without_docx_write(monkeypatch):
    import httpx

    FakeJavaClient.calls = []
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    monkeypatch.setattr(httpx, "Client", FakeJavaClient)

    response = client.post(
        "/internal/agent/tasks/agent-task-plan/resume",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "agent-task-plan",
            "taskType": "planning_task",
            "threadId": "agent-task-plan",
            "reviewType": "PLAN",
            "decision": "APPROVED",
            "decisionPayload": {"comment": "同意继续"},
            "input": {
                "goal": "生成后端实习简历草稿",
                "jobDescription": "要求 Java、Redis 和 RAG 项目经验",
                "resumeText": "做过多模态 RAG 项目，熟悉 Java",
                "toolHints": ["resume_template_fill"],
                "resumeTemplateId": "resume-template-1",
            },
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-plan/events",
            "javaToolGatewayBaseUrl": "http://java",
        },
    )

    assert response.status_code == 200
    draft = final_draft_events(event_calls())[-1]["json"]["draft"]
    fill_result = draft["resumeTemplateFill"]
    assert fill_result["status"] == "PENDING_REVIEW"
    assert fill_result["requiresApproval"] is True
    assert fill_result["approvalType"] == "OUTPUT"
    assert "outputPath" not in fill_result
    assert "Java" in fill_result["contentMap"]["skills"]


def test_planning_output_approval_with_save_intent_requests_crud_review(monkeypatch):
    import httpx

    FakeJavaClient.calls = []
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    monkeypatch.setattr(httpx, "Client", FakeJavaClient)

    response = client.post(
        "/internal/agent/tasks/agent-task-plan/resume",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "agent-task-plan",
            "taskType": "planning_task",
            "threadId": "agent-task-plan",
            "reviewType": "OUTPUT",
            "decision": "APPROVED",
            "decisionPayload": {"comment": "输出可保存"},
            "input": {
                "goal": "保存学习计划",
                "saveDraft": True,
                "toolHints": ["jd_learning_plan_save"],
            },
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-plan/events",
            "javaToolGatewayBaseUrl": "http://java",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    business_events = non_progress_events(event_calls())
    assert [event["json"]["eventType"] for event in business_events] == ["MUTATION_PROPOSED"]
    review = business_events[0]["json"]["reviewRequest"]
    assert review["reviewType"] == "CRUD"
    assert review["proposal"]["toolName"] == "jd_learning_plan_save"
    assert review["proposal"]["idempotencyKey"] == "jd_learning_plan_save-agent-task-plan-v1"


def test_planning_output_approval_with_memory_save_requests_crud_review(monkeypatch):
    import httpx

    FakeJavaClient.calls = []
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    monkeypatch.setattr(httpx, "Client", FakeJavaClient)

    response = client.post(
        "/internal/agent/tasks/agent-task-plan/resume",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "agent-task-plan",
            "taskType": "planning_task",
            "threadId": "agent-task-plan",
            "reviewType": "OUTPUT",
            "decision": "APPROVED",
            "decisionPayload": {"comment": "确认保存这条记忆"},
            "input": {
                "goal": "记住我偏好先看结论",
                "toolHints": ["agent_memory_candidate_save"],
            },
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-plan/events",
            "javaToolGatewayBaseUrl": "http://java",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    review = non_progress_events(event_calls())[0]["json"]["reviewRequest"]
    assert review["reviewType"] == "CRUD"
    assert review["proposal"]["toolName"] == "agent_memory_candidate_save"
    assert review["proposal"]["operationType"] == "AGENT_MEMORY_CANDIDATE_SAVE"
    assert review["proposal"]["resourceType"] == "agent_memory"


def test_tool_adapter_rejects_unapproved_mutation_before_java_gateway():
    from agents.orchestration.pae_react_graph import tool_adapter_node

    class GuardedClient:
        def __init__(self) -> None:
            self.events = []
            self.mutation_called = False

        def execute_mutation_tool(self, payload: dict) -> dict:
            self.mutation_called = True
            raise AssertionError("未审批变更不应调用 Java mutation gateway")

        def execute_read_tool(self, payload: dict) -> dict:
            raise AssertionError("该用例不应调用只读工具")

        def publish_event(self, event) -> None:
            self.events.append(event.model_dump(by_alias=True, exclude_none=True))

    client = GuardedClient()

    state = tool_adapter_node(
        {
            "task_id": "agent-task-plan",
            "thread_id": "agent-task-plan",
            "current_action": {
                "toolName": "jd_learning_plan_save",
                "toolType": "MUTATION",
                "arguments": {"source": "test"},
            },
            "tool_calls": [],
            "observations": [],
            "tool_results": [],
            "react_trace": [],
        },
        client,
    )

    assert client.mutation_called is False
    assert state["status"] == "TOOL_FAILED"
    assert state["error_code"] == "AGENT_MUTATION_REQUIRES_APPROVAL"
    observation = [event for event in client.events if event.get("eventType") == "TOOL_CALL_COMPLETED"][0]
    assert observation["toolCall"]["errorCode"] == "AGENT_MUTATION_REQUIRES_APPROVAL"


def test_tool_adapter_rejects_unknown_read_tool_before_java_gateway():
    from agents.orchestration.pae_react_graph import tool_adapter_node

    class GuardedClient:
        def __init__(self) -> None:
            self.events = []
            self.read_called = False

        def execute_mutation_tool(self, payload: dict) -> dict:
            raise AssertionError("该用例不应调用变更工具")

        def execute_read_tool(self, payload: dict) -> dict:
            self.read_called = True
            raise AssertionError("非法只读工具不应调用 Java read gateway")

        def publish_event(self, event) -> None:
            self.events.append(event.model_dump(by_alias=True, exclude_none=True))

    client = GuardedClient()
    state = tool_adapter_node(
        {
            "task_id": "agent-task-tool-forbidden",
            "thread_id": "agent-task-tool-forbidden",
            "current_action": {
                "toolName": "direct_database_reader",
                "toolType": "READ",
                "arguments": {},
            },
            "tool_calls": [],
            "observations": [],
            "tool_results": [],
            "react_trace": [],
        },
        client,
    )

    assert client.read_called is False
    assert state["status"] == "TOOL_FAILED"
    assert state["error_code"] == "AGENT_TOOL_FORBIDDEN"
    observation = [event for event in client.events if event.get("eventType") == "TOOL_CALL_COMPLETED"][0]
    assert observation["toolCall"]["errorCode"] == "AGENT_TOOL_FORBIDDEN"


def test_planning_crud_approval_executes_java_mutation_gateway(monkeypatch):
    import httpx

    FakeJavaClient.calls = []
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN", "agent-secret")
    client = TestClient(app)
    monkeypatch.setattr(httpx, "Client", FakeJavaClient)

    response = client.post(
        "/internal/agent/tasks/agent-task-plan/resume",
        headers={"X-Agent-Internal-Token": "agent-secret"},
        json={
            "taskId": "agent-task-plan",
            "taskType": "planning_task",
            "threadId": "agent-task-plan",
            "reviewType": "CRUD",
            "decision": "APPROVED",
            "decisionPayload": {
                "reviewId": "review-crud-agent-task-plan",
                "toolName": "jd_learning_plan_save",
                "idempotencyKey": "jd_learning_plan_save-agent-task-plan-v1",
                "comment": "同意保存",
            },
            "input": {
                "goal": "保存学习计划",
                "saveDraft": True,
                "toolHints": ["jd_learning_plan_save"],
            },
            "callbackUrl": "http://java/api/internal/agent/tasks/agent-task-plan/events",
            "javaToolGatewayBaseUrl": "http://java",
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "RUNNING"
    mutation_calls = [call for call in FakeJavaClient.calls if call["url"].endswith("/api/internal/agent/tools/mutation/execute")]
    business_events = non_progress_events(event_calls())
    assert len(mutation_calls) == 1
    assert mutation_calls[0]["json"]["approvalId"] == "review-crud-agent-task-plan"
    assert mutation_calls[0]["json"]["toolName"] == "jd_learning_plan_save"
    assert [event["json"]["eventType"] for event in business_events] == ["TOOL_CALL_COMPLETED", "TASK_COMPLETED"]
    assert business_events[0]["json"]["toolCall"]["toolType"] == "MUTATION"
    assert business_events[-1]["json"]["final"]["operationStatus"] == "APPLIED_UNDOABLE"

