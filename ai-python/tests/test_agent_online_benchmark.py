"""线上 Agent A/B 基准的可重复单元契约测试。"""

from __future__ import annotations

from datetime import datetime, timezone

from app.agent_runtime.repository import InMemoryAgentRepository
from app.agent_runtime.runtime_state_cache import AgentRuntimeStateCache
from app.agent_runtime.service import AgentRuntimeService
from app.services import agent_online_benchmark as benchmark_module
from app.services.agent_online_benchmark import AgentOnlineBenchmarkRegistry, BenchmarkRun, CONTEXT_COUNT, TURN_COUNT, build_control_cases, build_fixture
from agents.gateway.local_gateway import LocalAgentGateway
from agents.gateway.local_gateway import task_benchmark_config
from agents.orchestration.pae_react_graph import initial_state, prepare_llm_payload
from agents.llm.qwen_client import extract_usage


class FakeRedis:
    """最小 Redis 替身，用于验证缓存键、TTL 和一次性暂停标记。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and name in self.values:
            return False
        self.values[name] = value
        if ex is not None:
            self.ttls[name] = ex
        return True

    def delete(self, *names: str) -> int:
        for name in names:
            self.values.pop(name, None)
        return len(names)

    def ping(self) -> bool:
        return True


def test_continue_task_preserves_thread_and_requeues_after_terminal() -> None:
    """同一会话追加用户轮次只能重置状态，不能生成新的 pythonThreadId。"""
    repository = InMemoryAgentRepository()
    service = AgentRuntimeService(repository)
    created = service.create_task("7", {"taskType": "pure_read_query", "input": {"goal": "第一轮"}})
    repository.update_task(created["id"], status="COMPLETED")

    continued = service.continue_task(created["id"], "7", {"content": "第二轮", "clientTurnId": "web-turn-001"})
    messages = service.list_messages(created["id"], "7", None, None, 10)["messages"]

    assert continued["status"] == "CREATED"
    assert continued["pythonThreadId"] == created["pythonThreadId"]
    assert continued["input"]["goal"] == "第二轮"
    assert messages[-1]["messageType"] == "USER_TURN"
    assert messages[-1]["content"] == "第二轮"


def test_benchmark_turn_uses_same_thread_without_starting_worker() -> None:
    """Token 基准的冻结轮次应持久化到同一任务，但不依赖 Worker 终态。"""
    repository = InMemoryAgentRepository()
    service = AgentRuntimeService(repository)
    created = service.create_task("7", {"taskType": "pure_read_query", "input": {"goal": "基准初始化"}})

    continued = service.append_benchmark_turn(created["id"], "7", content="第一条冻结追问", client_turn_id="benchmark-token-a-01")
    messages = service.list_messages(created["id"], "7", None, None, 10)["messages"]

    assert continued["status"] == "CREATED"
    assert continued["pythonThreadId"] == created["pythonThreadId"]
    assert messages[-1]["messageType"] == "BENCHMARK_TURN"
    assert messages[-1]["content"] == "第一条冻结追问"


def test_token_variants_use_threshold_not_fixed_message_limit(monkeypatch) -> None:
    """A 组保留完整未摘要原文，B 组按 token 阈值拆出压缩候选而非固定 12 条。"""
    monkeypatch.setenv("AGENT_BENCHMARK_ENABLED", "true")
    messages = [
        {
            "id": f"m-{index}",
            "role": "USER",
            "messageType": "BENCHMARK_CONTEXT",
            "content": f"上下文 {index} " + ("工具审批边界、线程恢复、摘要覆盖范围。 " * 160),
            "createdAt": "2026-07-25T00:00:00Z",
        }
        for index in range(1, 25)
    ]
    payload = {"node": "planner"}
    baseline_state = initial_state(
        "task-a",
        "pure_read_query",
        "thread-a",
        {
            "goal": "A 组",
            "agentBenchmark": {
                "scenarioSetId": "agent-control-long-context-v1",
                "compressionEnabled": False,
                "bestWindowTokens": 4000,
                "summaryTargetTokens": 1000,
                "rawMessageFetchLimit": 200,
            },
        },
        plan_approved=False,
    )
    baseline_state["context_messages"] = messages
    baseline_state["compression_candidate_messages"] = []
    optimized_state = initial_state(
        "task-b",
        "pure_read_query",
        "thread-b",
        {
            "goal": "B 组",
            "agentBenchmark": {
                "scenarioSetId": "agent-control-long-context-v1",
                "compressionEnabled": True,
                "bestWindowTokens": 4000,
                "summaryTargetTokens": 400,
                "summaryHardLimitTokens": 600,
                "rawMessageFetchLimit": 200,
            },
        },
        plan_approved=False,
    )
    optimized_state["context_messages"] = messages
    optimized_state["compression_candidate_messages"] = []

    baseline = prepare_llm_payload(baseline_state, "planner", payload)
    optimized = prepare_llm_payload(optimized_state, "planner", payload)

    assert len(baseline["restoredContext"]["recentMessages"]) == 24
    assert 1 <= len(optimized["restoredContext"]["recentMessages"]) < 24
    assert len(optimized["restoredContext"]["recentMessages"]) != 12
    assert optimized["restoredContext"]["budget"]["windowPolicy"] == "token_threshold_v2"
    assert optimized["restoredContext"]["budget"]["summaryTargetTokens"] == 400


def test_local_gateway_keeps_all_until_token_threshold() -> None:
    """恢复层不再最多取 100/最近 12 条，而是按 token 阈值动态拆分。"""
    repository = InMemoryAgentRepository()
    service = AgentRuntimeService(repository)
    task = service.create_task(
        "7",
        {
            "taskType": "pure_read_query",
            "input": {
                "goal": "动态上下文窗口",
                "agentBenchmark": {
                    "scenarioSetId": "agent-control-long-context-v1",
                    "compressionEnabled": True,
                    "redisEnabled": False,
                    "bestWindowTokens": 4000,
                    "summaryTargetTokens": 400,
                    "summaryHardLimitTokens": 600,
                    "rawMessageFetchLimit": 200,
                },
            },
        },
    )
    for index in range(1, 25):
        service.append_benchmark_context_message(
            task["id"],
            "7",
            content=f"上下文 {index} " + ("工具审批边界、线程恢复、摘要覆盖范围。 " * 160),
            case_id=f"context-{index:02d}",
        )

    context = LocalAgentGateway(task["id"], service).restore_context(task["id"], best_window_tokens=4000, summary_target_tokens=400)
    total_unsummarized = len(context["messageWindow"]) + len(context["compressionCandidateMessages"])

    assert total_unsummarized == 25
    assert 1 <= len(context["messageWindow"]) < total_unsummarized
    assert len(context["messageWindow"]) != 12
    assert context["budgetMetadata"]["windowPolicy"] == "token_threshold_v2"
    assert context["budgetMetadata"]["summaryTargetTokens"] == 400
    assert context["budgetMetadata"]["compressionCandidateTokenEstimate"] > 0


def test_recovery_latency_stops_at_second_context_restore(monkeypatch, tmp_path) -> None:
    """恢复时延必须在第二个持久化恢复事件出现前后冻结，而非等待任务完成。"""
    events: list[str] = []

    class FakeProcess:
        def __init__(self, pid: int, label: str) -> None:
            self.pid = pid
            self.label = label
            self.returncode: int | None = None

        def terminate(self) -> None:
            events.append(f"{self.label}:terminate")
            self.returncode = -15

        def wait(self, timeout: float) -> int:
            events.append(f"{self.label}:wait")
            if self.returncode is None:
                self.returncode = 0
            return self.returncode

        def kill(self) -> None:
            events.append(f"{self.label}:kill")
            self.returncode = -9

    processes: list[FakeProcess] = []

    def fake_popen(*_args, **_kwargs):
        process = FakeProcess(100 + len(processes), "first" if not processes else "second")
        processes.append(process)
        return process

    restore_messages = [
        {"sequenceNo": 11, "payload": {"draft": {"node": "context_restore", "restoreSource": "postgresql"}}},
        {"sequenceNo": 22, "payload": {"draft": {"node": "context_restore", "restoreSource": "redis_l2"}}},
    ]

    def fake_wait_for_restore(*_args, **_kwargs):
        events.append(f"restore-{len([item for item in events if item.startswith('restore-')]) + 1}")
        return restore_messages.pop(0)

    class FakeService:
        def get_task(self, *_args):
            return {"pythonThreadId": "thread-1", "status": "COMPLETED", "summaryCount": 2}

    ticks = iter([10.0, 10.05, 10.20])
    monkeypatch.setattr(benchmark_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(benchmark_module, "latest_sequence", lambda *_args: 10)
    monkeypatch.setattr(benchmark_module, "wait_for_context_restore", fake_wait_for_restore)
    monkeypatch.setattr(benchmark_module.time, "perf_counter", lambda: next(ticks))

    registry = AgentOnlineBenchmarkRegistry()
    result = registry._kill_and_recover_worker(FakeService(), BenchmarkRun("run", "7", tmp_path), "task-1", "thread-1", 1)

    assert result["restoreLatencyMs"] == 50.0
    assert result["completionLatencyMs"] == 200.0
    assert events.index("restore-2") < events.index("second:wait")


def test_runtime_state_cache_validates_identity_and_pause_once() -> None:
    """Redis L2 必须绑定 user/task/thread，且恢复暂停标记只能被首个 Worker 获得。"""
    redis = FakeRedis()
    cache = AgentRuntimeStateCache(client=redis)
    context = {"messageWindow": [{"id": "m-1"}], "summarySegments": []}

    assert cache.save_context("7", "task-1", "thread-1", context)
    assert cache.load_context("7", "task-1", "thread-1") == context
    assert cache.load_context("7", "task-1", "other-thread") is None
    assert cache.claim_benchmark_pause("7", "task-1", "turn-1", 60)
    assert not cache.claim_benchmark_pause("7", "task-1", "turn-1", 60)

    cache.invalidate_context("7", "task-1")
    assert cache.load_context("7", "task-1", "thread-1") is None


def test_runtime_state_cache_serializes_postgres_datetime() -> None:
    """真实 PostgreSQL 行中的时间字段必须能写入 Redis 并按 ISO 文本恢复。"""
    redis = FakeRedis()
    cache = AgentRuntimeStateCache(client=redis)
    context = {"messageWindow": [{"id": "m-1", "createdAt": datetime(2026, 7, 31, 8, 30, tzinfo=timezone.utc)}]}

    assert cache.save_context("7", "task-1", "thread-1", context)

    restored = cache.load_context("7", "task-1", "thread-1")
    assert restored == {"messageWindow": [{"id": "m-1", "createdAt": "2026-07-31T08:30:00+00:00"}]}


def test_task_benchmark_config_accepts_postgres_text_json() -> None:
    """真实 PostgreSQL 的 TEXT input_json 也必须应用任务级基准预算。"""
    task = {
        "input_json": (
            '{"agentBenchmark":{"scenarioSetId":"agent-control-long-context-v1",'
            '"redisEnabled":true,"rawMessageFetchLimit":200}}'
        )
    }

    config = task_benchmark_config(task)

    assert config["redisEnabled"] is True
    assert config["rawMessageFetchLimit"] == 200


def test_benchmark_fixture_and_control_cases_are_fixed_size() -> None:
    """报告的 24 条上下文、30 轮和 100 条工具输入必须由固定夹具生成。"""
    fixture = build_fixture()
    cases = build_control_cases()

    assert len(fixture["contexts"]) == CONTEXT_COUNT == 24
    assert len(fixture["turns"]) == TURN_COUNT == 30
    assert len(cases) == 100
    assert sum(item["category"] == "unapproved_mutation" for item in cases) == 50
    assert sum(item["category"] == "unknown_tool" for item in cases) == 50


def test_extract_usage_reads_only_non_negative_openai_compatible_counts() -> None:
    """Token 汇总只接受模型响应的 usage，缺失字段不能伪造为字符估算。"""
    assert extract_usage({"usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}}) == {
        "promptTokens": 123,
        "completionTokens": 45,
        "totalTokens": 168,
    }
    assert extract_usage({"usage": {"prompt_tokens": -1, "completion_tokens": "45"}}) == {}
