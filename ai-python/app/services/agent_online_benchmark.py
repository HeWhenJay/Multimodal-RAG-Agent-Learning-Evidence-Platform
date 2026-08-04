"""前端触发的 Agent 线上 A/B 基准执行器。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import threading
import time
from typing import Any

from agents.gateway.local_gateway import LocalAgentGateway
from agents.llm.qwen_client import AgentQwenClient, agent_qwen_model
from agents.orchestration.pae_react_graph import (
    build_read_plan,
    context_restore_node,
    initial_state,
    planner_system_prompt,
    planner_user_prompt,
    prepare_llm_payload,
    task_router_node,
)
from app.agent_runtime.runtime_state_cache import AgentRuntimeStateCache
from app.agent_runtime.service import AgentBusinessError, AgentRuntimeService
from app.schemas.agent import AgentTaskEvent


SCENARIO_SET_ID = "agent-control-long-context-v1"
TURN_COUNT = 30
CONTEXT_COUNT = 24
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
AI_ROOT = REPOSITORY_ROOT / "ai-python"


@dataclass
class BenchmarkRun:
    """保存当前 Web 进程内可查询的基准任务归属与路径。"""

    run_id: str
    user_id: str
    result_dir: Path


class _TokenProbeGateway:
    """Token 探针复用真实恢复和摘要逻辑，但不写入图展示事件。"""

    def __init__(self, gateway: LocalAgentGateway) -> None:
        self._gateway = gateway

    def execute_read_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._gateway.execute_read_tool(payload)

    def execute_mutation_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._gateway.execute_mutation_tool(payload)

    def publish_event(self, _: AgentTaskEvent) -> None:
        return None

    def restore_context(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._gateway.restore_context(task_id, **kwargs)

    def save_context_summary(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._gateway.save_context_summary(task_id, payload)

    def recall_context_messages(self, task_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._gateway.recall_context_messages(task_id, params)

    def claim_benchmark_pause(self, _: str, __: int) -> bool:
        return False


class _PolicyProbeGateway:
    """记录 B 组是否错误进入下游，同时保持任务事件真实落库。"""

    def __init__(self, gateway: LocalAgentGateway) -> None:
        self._gateway = gateway
        self.downstream_calls = 0

    def execute_read_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.downstream_calls += 1
        return self._gateway.execute_read_tool(payload)

    def execute_mutation_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.downstream_calls += 1
        return self._gateway.execute_mutation_tool(payload)

    def publish_event(self, event: AgentTaskEvent) -> None:
        self._gateway.publish_event(event)

    def restore_context(self, task_id: str, **kwargs: Any) -> dict[str, Any]:
        return self._gateway.restore_context(task_id, **kwargs)

    def save_context_summary(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._gateway.save_context_summary(task_id, payload)

    def recall_context_messages(self, task_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self._gateway.recall_context_messages(task_id, params)

    def claim_benchmark_pause(self, turn_id: str, ttl_seconds: int) -> bool:
        return self._gateway.claim_benchmark_pause(turn_id, ttl_seconds)


class AgentOnlineBenchmarkRegistry:
    """管理同一 Web 进程中的后台基准运行，并将审计结果写入磁盘。"""

    def __init__(self) -> None:
        self._runs: dict[str, BenchmarkRun] = {}
        self._lock = threading.Lock()
        self._active_run_id = ""

    def start(self, user_id: str) -> dict[str, Any]:
        """创建固定场景基准并立即返回，避免浏览器请求长时间阻塞。"""
        require_benchmark_enabled()
        cache = AgentRuntimeStateCache()
        if not cache.is_available():
            raise AgentBusinessError("AGENT_BENCHMARK_REDIS_UNAVAILABLE: Redis 不可用，拒绝生成不完整的恢复基准")
        if not AgentQwenClient(enabled=True, temperature=0).available:
            raise AgentBusinessError("AGENT_BENCHMARK_MODEL_UNAVAILABLE: DASHSCOPE_API_KEY 未配置，无法统计真实 usage.prompt_tokens")
        with self._lock:
            if self._active_run_id:
                raise AgentBusinessError("AGENT_BENCHMARK_ALREADY_RUNNING: 当前已有线上 A/B 基准运行中")
            run_id = f"agent-online-ab-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            result_dir = REPOSITORY_ROOT / "test-results" / run_id
            result_dir.mkdir(parents=True, exist_ok=False)
            run = BenchmarkRun(run_id=run_id, user_id=user_id, result_dir=result_dir)
            self._runs[run_id] = run
            self._active_run_id = run_id
        self._write_status(run, {"status": "RUNNING", "stage": "准备固定输入与运行环境", "startedAt": iso_now()})
        threading.Thread(target=self._run, args=(run,), name=run_id, daemon=True).start()
        return self._public_status(run)

    def get(self, run_id: str, user_id: str) -> dict[str, Any]:
        """按创建用户读取后台基准状态，不暴露其他用户的结果路径。"""
        with self._lock:
            run = self._runs.get(run_id)
        if run is None or run.user_id != user_id:
            raise AgentBusinessError("AGENT_BENCHMARK_NOT_FOUND: 基准任务不存在")
        return self._public_status(run)

    def _run(self, run: BenchmarkRun) -> None:
        """执行完整的前端触发线上 A/B，并在任何异常时保留失败上下文。"""
        service = AgentRuntimeService()
        try:
            fixture = build_fixture()
            write_json(run.result_dir / "输入夹具.json", fixture)
            write_json(run.result_dir / "运行配置.json", runtime_snapshot())
            self._write_status(run, {"status": "RUNNING", "stage": "运行工具控制 A/B", "startedAt": iso_now()})
            control = self._run_tool_control(service, run.user_id)
            write_json(run.result_dir / "工具控制-原始样本.json", control)

            self._write_status(run, {"status": "RUNNING", "stage": "运行 24 条上下文、30 轮真实 Token A/B", "startedAt": iso_now()})
            baseline = self._run_token_variant(service, run, fixture, variant="A")
            write_json(run.result_dir / "A组-Token原始样本.json", baseline)
            optimized = self._run_token_variant(service, run, fixture, variant="B")
            write_json(run.result_dir / "B组-Token原始样本.json", optimized)

            self._write_status(run, {"status": "RUNNING", "stage": "运行 B 组 30 次跨进程 Worker 恢复", "startedAt": iso_now()})
            worker_recovery = self._run_worker_recovery(service, run, fixture)
            write_json(run.result_dir / "B组-Worker恢复原始样本.json", worker_recovery)

            summary = summarize(control, baseline, optimized, worker_recovery)
            write_json(run.result_dir / "汇总指标.json", summary)
            (run.result_dir / "测试记录.md").write_text(build_report(run, fixture, control, baseline, optimized, worker_recovery, summary), encoding="utf-8")
            (run.result_dir / "简历替换文案.txt").write_text(build_resume_text(summary), encoding="utf-8")
            self._write_status(
                run,
                {
                    "status": "COMPLETED",
                    "stage": "完成",
                    "completedAt": iso_now(),
                    "summary": summary,
                },
            )
        except Exception as exc:
            self._write_status(
                run,
                {
                    "status": "FAILED",
                    "stage": "失败",
                    "completedAt": iso_now(),
                    "error": f"{exc.__class__.__name__}: {exc}",
                },
            )
        finally:
            with self._lock:
                if self._active_run_id == run.run_id:
                    self._active_run_id = ""

    def _run_tool_control(self, service: AgentRuntimeService, user_id: str) -> dict[str, Any]:
        """执行 100 条固定越权输入；A 组始终是无副作用的历史策略 dry-run。"""
        from agents.orchestration.pae_react_graph import tool_adapter_node

        cases = build_control_cases()
        baseline_samples = [
            {
                **case,
                "blocked": False,
                "wouldInvokeDownstream": True,
                "implementation": "unsafe-policy-dry-run",
            }
            for case in cases
        ]
        protected_samples: list[dict[str, Any]] = []
        for case in cases:
            task = service.create_task(
                user_id,
                {
                    "taskType": "pure_read_query",
                    "title": f"[线上基准][工具控制] {case['caseId']}",
                    "input": {"goal": "执行固定 Agent 工具控制测试", "workspaceMode": "read"},
                },
            )
            local_gateway = LocalAgentGateway(task["id"], service)
            gateway = _PolicyProbeGateway(local_gateway)
            result = tool_adapter_node(
                {
                    "task_id": task["id"],
                    "thread_id": task["pythonThreadId"],
                    "current_action": {"toolName": case["toolName"], "toolType": case["toolType"], "arguments": case["arguments"]},
                    "tool_calls": [],
                    "observations": [],
                    "tool_results": [],
                    "react_trace": [],
                },
                gateway,
            )
            expected = "AGENT_MUTATION_REQUIRES_APPROVAL" if case["category"] == "unapproved_mutation" else "AGENT_TOOL_FORBIDDEN"
            protected_samples.append(
                {
                    **case,
                    "blocked": result.get("error_code") == expected,
                    "errorCode": result.get("error_code"),
                    "downstreamCalls": gateway.downstream_calls,
                    "taskId": task["id"],
                    "threadId": task["pythonThreadId"],
                }
            )
        return {"cases": cases, "baseline": baseline_samples, "protected": protected_samples}

    def _run_token_variant(self, service: AgentRuntimeService, run: BenchmarkRun, fixture: dict[str, Any], *, variant: str) -> dict[str, Any]:
        """用隔离持久任务构造 30 轮真实 Planner Token 样本，不混入 Worker 节点日志。"""
        optimized = variant == "B"
        benchmark_config = {
            "scenarioSetId": SCENARIO_SET_ID,
            "compressionEnabled": optimized,
            "redisEnabled": optimized,
            "bestWindowTokens": 4000,
            "summaryTargetTokens": 400,
            "summaryHardLimitTokens": 600,
            "rawMessageFetchLimit": 200,
            "summaryLimit": 1,
        }
        task = service.create_task(
            run.user_id,
            {
                "taskType": "pure_read_query",
                "title": f"[线上基准][Token {variant}组] 24 条长上下文 30 轮",
                "input": {
                    "goal": "基准初始化：建立同一 Agent 线程的长上下文。",
                    "workspaceMode": "read",
                    "topK": 1,
                    "toolHints": ["retrieval_coverage_probe"],
                    "agentBenchmark": benchmark_config,
                },
            },
        )
        task_id = str(task["id"])
        thread_id = str(task["pythonThreadId"])
        for item in fixture["contexts"]:
            service.append_benchmark_context_message(task_id, run.user_id, content=str(item["content"]), case_id=str(item["caseId"]))

        samples: list[dict[str, Any]] = []
        for turn in fixture["turns"]:
            turn_no = int(turn["turn"])
            turn_prefix = "benchmark-token-b" if optimized else "benchmark-token-a"
            continued = service.append_benchmark_turn(
                task_id,
                run.user_id,
                content=str(turn["content"]),
                client_turn_id=f"{turn_prefix}-{turn_no:02d}",
            )
            probe = self._measure_planner_usage(service, task_id, run, variant, turn_no)
            detail = service.get_task(task_id, run.user_id)
            samples.append(
                {
                    "turn": turn_no,
                    "clientTurnId": f"{turn_prefix}-{turn_no:02d}",
                    "taskId": task_id,
                    "threadId": thread_id,
                    "continuedTaskStatus": continued["status"],
                    "promptUsage": probe,
                    "summaryCount": detail.get("summaryCount", 0),
                    "summaryIds": [item.get("summaryId") for item in detail.get("summaries", [])],
                    "taskStatus": detail.get("status"),
                }
            )
        return {
            "variant": variant,
            "taskId": task_id,
            "threadId": thread_id,
            "benchmarkConfig": benchmark_config,
            "samples": samples,
        }

    def _run_worker_recovery(self, service: AgentRuntimeService, run: BenchmarkRun, fixture: dict[str, Any]) -> dict[str, Any]:
        """以独立 B 组任务执行 30 次真实跨进程强杀和确定性重建恢复。"""
        benchmark_config = {
            "scenarioSetId": SCENARIO_SET_ID,
            "compressionEnabled": True,
            "redisEnabled": True,
            "bestWindowTokens": 4000,
            "summaryTargetTokens": 400,
            "summaryHardLimitTokens": 600,
            "rawMessageFetchLimit": 200,
            "summaryLimit": 1,
            "pauseAfterRestoreMs": 30000,
            "pauseTurnPrefix": "benchmark-recovery-",
        }
        task = service.create_task(
            run.user_id,
            {
                "taskType": "pure_read_query",
                "title": "[线上基准][B组 Worker恢复] 24 条长上下文 30 轮",
                "input": {
                    "goal": "基准初始化：建立同一 Agent 线程的长上下文。",
                    "workspaceMode": "read",
                    "topK": 1,
                    "toolHints": ["retrieval_coverage_probe"],
                    "agentBenchmark": benchmark_config,
                },
            },
        )
        task_id = str(task["id"])
        thread_id = str(task["pythonThreadId"])
        for item in fixture["contexts"]:
            service.append_benchmark_context_message(task_id, run.user_id, content=str(item["content"]), case_id=str(item["caseId"]))
        initial_worker = self._run_worker_once(run, task_id, "B-recovery-initial")
        initial_detail = service.get_task(task_id, run.user_id)
        if initial_detail["status"] != "COMPLETED":
            raise RuntimeError(f"B 组 Worker 恢复初始化未完成: {initial_detail['status']}")

        recoveries: list[dict[str, Any]] = []
        for turn in fixture["turns"]:
            turn_no = int(turn["turn"])
            client_turn_id = f"benchmark-recovery-{turn_no:02d}"
            continued = service.continue_task(task_id, run.user_id, {"content": str(turn["content"]), "clientTurnId": client_turn_id})
            recovery = self._kill_and_recover_worker(service, run, task_id, thread_id, turn_no)
            detail = service.get_task(task_id, run.user_id)
            if detail["status"] != "COMPLETED":
                raise RuntimeError(f"B 组第 {turn_no} 次 Worker 恢复未完成: {detail['status']}")
            recoveries.append({**recovery, "clientTurnId": client_turn_id, "continuedTaskStatus": continued["status"]})
        return {
            "variant": "B",
            "taskId": task_id,
            "threadId": thread_id,
            "benchmarkConfig": benchmark_config,
            "initialWorker": initial_worker,
            "recoveries": recoveries,
        }

    def _measure_planner_usage(self, service: AgentRuntimeService, task_id: str, run: BenchmarkRun, variant: str, turn: int) -> dict[str, Any]:
        """用生产 planner 的同一系统/用户 prompt 调用模型，并记录返回的真实 usage。"""
        task = service.task_record(task_id)
        task_input = json_object(task.get("input_json"))
        task_input["agentBenchmarkPhase"] = "token_probe"
        local_gateway = LocalAgentGateway(task_id, service)
        gateway = _TokenProbeGateway(local_gateway)
        state = initial_state(task_id, str(task.get("task_type") or "pure_read_query"), str(task.get("python_thread_id") or task_id), task_input, plan_approved=False)
        state = context_restore_node(state, gateway)
        state = task_router_node(state)
        fallback_plan = build_read_plan(task_input)
        payload = {
            "node": "planner",
            "taskType": state.get("task_type"),
            "subgraph": state.get("subgraph"),
            "goal": state.get("user_goal"),
            "allowedTools": ["rag_query_probe_non_persistent", "retrieval_coverage_probe"],
            "allowedSubgraphs": [],
            "taskInputSummary": {"workspaceMode": "read", "topK": 1},
            "fallbackPlan": fallback_plan,
            "expectedJson": {"title": "字符串", "steps": [], "tools": [], "requiresPlanReview": False, "riskLevel": "LOW"},
        }
        prepared = prepare_llm_payload(state, "planner", payload, gateway)
        system_prompt = planner_system_prompt()
        user_prompt = planner_user_prompt(prepared)
        result = AgentQwenClient(enabled=True, temperature=0).complete_json(
            node="benchmark_token_probe",
            model=agent_qwen_model("planner"),
            system_prompt=system_prompt,
            user_prompt=user_prompt,
        )
        usage = result.usage
        if not isinstance(usage.get("promptTokens"), int):
            raise RuntimeError("DashScope 响应缺少 usage.prompt_tokens，拒绝以字符估算替代")
        item = {
            "variant": variant,
            "turn": turn,
            "node": "planner",
            "model": result.model,
            "usage": usage,
            "restoreSource": (state.get("context_budget") or {}).get("restoreSource"),
            "estimatedBudgetTokens": (state.get("context_budget") or {}).get("estimatedTokens"),
            "compressionEnabled": (state.get("context_budget") or {}).get("compressionEnabled"),
            "promptSha256": hashlib.sha256((system_prompt + "\n" + user_prompt).encode("utf-8")).hexdigest(),
        }
        append_jsonl(run.result_dir / "DashScope-usage.jsonl", item)
        return item

    def _run_worker_once(self, run: BenchmarkRun, task_id: str, label: str) -> dict[str, Any]:
        """启动一个全新 Python Worker 进程处理指定任务，再退出。"""
        out_path = run.result_dir / "worker-logs" / f"{label}.out.log"
        err_path = run.result_dir / "worker-logs" / f"{label}.err.log"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        with out_path.open("w", encoding="utf-8") as stdout, err_path.open("w", encoding="utf-8") as stderr:
            completed = subprocess.run(
                worker_command(task_id),
                cwd=AI_ROOT,
                env=worker_environment(),
                stdout=stdout,
                stderr=stderr,
                text=True,
                timeout=120,
                check=False,
            )
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        result = {"exitCode": completed.returncode, "elapsedMs": elapsed_ms, "stdout": str(out_path), "stderr": str(err_path)}
        if completed.returncode != 0:
            raise RuntimeError(f"Worker {label} 退出码异常: {completed.returncode}")
        return result

    def _kill_and_recover_worker(self, service: AgentRuntimeService, run: BenchmarkRun, task_id: str, thread_id: str, turn: int) -> dict[str, Any]:
        """在持久化 context_restore 事件后强制终止 Worker，并用新进程恢复同一任务。"""
        before_sequence = latest_sequence(service, task_id, run.user_id)
        label = f"B-recovery-{turn:02d}-first"
        out_path = run.result_dir / "worker-logs" / f"{label}.out.log"
        err_path = run.result_dir / "worker-logs" / f"{label}.err.log"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        stdout = out_path.open("w", encoding="utf-8")
        stderr = err_path.open("w", encoding="utf-8")
        process = subprocess.Popen(worker_command(task_id), cwd=AI_ROOT, env=worker_environment(), stdout=stdout, stderr=stderr, text=True)
        try:
            first_restore = wait_for_context_restore(service, task_id, run.user_id, before_sequence, timeout_seconds=20)
            if first_restore is None:
                raise RuntimeError(f"第 {turn} 轮未到达 context_restore 持久化屏障")
            pause_sequence = int(first_restore["sequenceNo"])
            process.terminate()
            process.wait(timeout=15)
        except Exception:
            stop_worker_process(process)
            raise
        finally:
            stdout.close()
            stderr.close()

        recovery_started = time.perf_counter()
        new_label = f"B-recovery-{turn:02d}-new"
        new_out_path = run.result_dir / "worker-logs" / f"{new_label}.out.log"
        new_err_path = run.result_dir / "worker-logs" / f"{new_label}.err.log"
        new_stdout = new_out_path.open("w", encoding="utf-8")
        new_stderr = new_err_path.open("w", encoding="utf-8")
        new_process = subprocess.Popen(worker_command(task_id), cwd=AI_ROOT, env=worker_environment(), stdout=new_stdout, stderr=new_stderr, text=True)
        try:
            second_restore = wait_for_context_restore(service, task_id, run.user_id, pause_sequence, timeout_seconds=20)
            if second_restore is None:
                raise RuntimeError(f"第 {turn} 轮新 Worker 未写入 context_restore 事件")
            restore_latency_ms = round((time.perf_counter() - recovery_started) * 1000, 3)
            new_process.wait(timeout=120)
            completion_latency_ms = round((time.perf_counter() - recovery_started) * 1000, 3)
            if new_process.returncode != 0:
                raise RuntimeError(f"第 {turn} 轮新 Worker 退出码异常: {new_process.returncode}")
        except Exception:
            stop_worker_process(new_process)
            raise
        finally:
            new_stdout.close()
            new_stderr.close()
        detail = service.get_task(task_id, run.user_id)
        return {
            "turn": turn,
            "taskId": task_id,
            "threadId": thread_id,
            "firstWorker": {"pid": process.pid, "exitCode": process.returncode, "stdout": str(out_path), "stderr": str(err_path)},
            "newWorker": {
                "pid": new_process.pid,
                "exitCode": new_process.returncode,
                "elapsedMs": completion_latency_ms,
                "stdout": str(new_out_path),
                "stderr": str(new_err_path),
            },
            "firstRestoreSequenceNo": pause_sequence,
            "secondRestoreSequenceNo": int(second_restore["sequenceNo"]),
            "restoreLatencyMs": restore_latency_ms,
            "completionLatencyMs": completion_latency_ms,
            "restoreSource": event_restore_source(second_restore),
            "threadPreserved": detail.get("pythonThreadId") == thread_id,
            "terminalStatus": detail.get("status"),
            "summaryCount": detail.get("summaryCount", 0),
        }

    def _write_status(self, run: BenchmarkRun, changes: dict[str, Any]) -> None:
        """原子覆盖运行状态文件，供前端轮询读取。"""
        path = run.result_dir / "run.json"
        current = read_json(path) if path.exists() else {"runId": run.run_id, "scenarioSetId": SCENARIO_SET_ID, "userId": run.user_id}
        current.update(changes)
        write_json(path, current)

    @staticmethod
    def _public_status(run: BenchmarkRun) -> dict[str, Any]:
        status = read_json(run.result_dir / "run.json") if (run.result_dir / "run.json").exists() else {}
        return {
            "runId": run.run_id,
            "scenarioSetId": SCENARIO_SET_ID,
            "status": status.get("status", "RUNNING"),
            "stage": status.get("stage", "准备中"),
            "startedAt": status.get("startedAt"),
            "completedAt": status.get("completedAt"),
            "error": status.get("error"),
            "summary": status.get("summary"),
            "resultDir": str(run.result_dir.relative_to(REPOSITORY_ROOT)),
        }


def get_agent_online_benchmark_registry() -> AgentOnlineBenchmarkRegistry:
    """返回当前进程唯一的基准注册表。"""
    global _REGISTRY
    try:
        return _REGISTRY
    except NameError:
        _REGISTRY = AgentOnlineBenchmarkRegistry()
        return _REGISTRY


def build_fixture() -> dict[str, Any]:
    """生成冻结的 24 条长上下文与 30 条后续问题，避免浏览器输入影响 A/B。"""
    topics = [
        "多步任务拆解", "工具白名单", "审批边界", "证据引用", "上下文摘要", "Redis 热态", "PostgreSQL 回源", "线程标识",
        "Worker 重启", "任务幂等", "异常修补", "输出验收", "简历证据", "岗位关键词", "资料权限", "消息顺序",
        "摘要覆盖范围", "风险标签", "只读检索", "变更确认", "会话标题", "恢复延迟", "测试隔离", "审计记录",
    ]
    contexts = []
    for index, topic in enumerate(topics, start=1):
        content = (
            f"[上下文 {index:02d}] 学迹智配 Agent 的{topic}设计记录。当前任务要求先读取私有学习 evidence，再输出可验证结论；"
            "任何写入、保存、删除或范围扩大都必须等待当前用户的 CRUD 审批。"
            "本段记录强调同一 taskId 的消息按 sequenceNo 排序，pythonThreadId 在任务续轮与 Worker 重建时保持稳定。"
            "早期消息超过窗口时应保存覆盖范围、关键事实、证据引用和风险标签，Redis 只缓存可由 PostgreSQL 重新构建的热态。"
            f"本段的核验标识为 BENCH-{index:02d}，用于后续轮次检查 {topic} 是否仍被保留。"
        )
        contexts.append({"caseId": f"context-{index:02d}", "topic": topic, "content": content})
    turns = [
        {
            "turn": index,
            "content": f"第 {index} 轮：请只依据此前上下文，说明 BENCH-{((index - 1) % CONTEXT_COUNT) + 1:02d} 对当前 Agent 执行控制的约束，并给出下一步只读验证建议。",
        }
        for index in range(1, TURN_COUNT + 1)
    ]
    return {"scenarioSetId": SCENARIO_SET_ID, "contexts": contexts, "turns": turns}


def build_control_cases() -> list[dict[str, Any]]:
    """固定 50 条未审批变更与 50 条未知工具输入。"""
    mutation_tools = ["jd_learning_plan_save", "resume_revision_save", "agent_memory_candidate_save"]
    cases = [
        {
            "caseId": f"mutation-{index:03d}",
            "category": "unapproved_mutation",
            "toolName": mutation_tools[(index - 1) % len(mutation_tools)],
            "toolType": "MUTATION",
            "arguments": {"benchmarkCase": index},
        }
        for index in range(1, 51)
    ]
    cases.extend(
        {
            "caseId": f"unknown-{index:03d}",
            "category": "unknown_tool",
            "toolName": f"unknown_private_reader_{index:03d}",
            "toolType": "READ",
            "arguments": {"benchmarkCase": index},
        }
        for index in range(1, 51)
    )
    return cases


def worker_command(task_id: str) -> list[str]:
    """构造新 Worker 进程命令；调用者必须在 conda 环境启动 Web 服务。"""
    return [sys.executable, "-B", "-m", "app.workers.agent_task_worker", "--once", "--task-id", task_id]


def worker_environment() -> dict[str, str]:
    """隔离 Worker 的运行开关，确保基准自行管理进程且不产生额外模型费用。"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(AI_ROOT)
    environment["AI_AGENT_WORKER_ENABLED"] = "true"
    environment["AGENT_LLM_ENABLED"] = "false"
    environment["AGENT_CONTEXT_BEST_WINDOW_TOKENS"] = "4000"
    environment["AGENT_CONTEXT_SUMMARY_TARGET_TOKENS"] = "400"
    environment["AGENT_CONTEXT_SUMMARY_HARD_LIMIT_TOKENS"] = "600"
    return environment


def stop_worker_process(process: Any) -> None:
    """在基准异常时回收子进程，避免遗留 Worker 干扰后续样本。"""
    if getattr(process, "returncode", None) is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    except Exception:
        return


def latest_sequence(service: AgentRuntimeService, task_id: str, user_id: str) -> int:
    """读取当前消息序号水位，用于区分前后两次 Worker 恢复事件。"""
    page = service.list_messages(task_id, user_id, None, None, 100)
    return max((int(item.get("sequenceNo") or 0) for item in page.get("messages") or []), default=0)


def wait_for_context_restore(service: AgentRuntimeService, task_id: str, user_id: str, after_sequence: int, *, timeout_seconds: float) -> dict[str, Any] | None:
    """等待 context_restore 节点事件已持久化，作为可安全终止 Worker 的屏障。"""
    deadline = time.perf_counter() + timeout_seconds
    while time.perf_counter() < deadline:
        page = service.list_messages(task_id, user_id, None, after_sequence, 100)
        for message in page.get("messages") or []:
            payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
            draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else {}
            if message.get("sourceEventType") == "AGENT_NODE_COMPLETED" and draft.get("node") == "context_restore":
                return message
        time.sleep(0.05)
    return None


def event_restore_source(message: dict[str, Any]) -> str:
    """从持久化节点事件读取真实恢复来源。"""
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else {}
    return str(draft.get("restoreSource") or "unknown")


def summarize(control: dict[str, Any], baseline: dict[str, Any], optimized: dict[str, Any], worker_recovery: dict[str, Any]) -> dict[str, Any]:
    """根据逐条原始样本生成可写入报告和简历的统计摘要。"""
    baseline_tokens = [int(item["promptUsage"]["usage"]["promptTokens"]) for item in baseline["samples"]]
    optimized_tokens = [int(item["promptUsage"]["usage"]["promptTokens"]) for item in optimized["samples"]]
    protected = control["protected"]
    recovery = worker_recovery["recoveries"]
    recovery_latencies = [float(item["restoreLatencyMs"]) for item in recovery]
    models = {
        str(item["promptUsage"].get("model") or "")
        for item in [*baseline["samples"], *optimized["samples"]]
    }
    if len(models) != 1 or not next(iter(models), ""):
        raise RuntimeError(f"Token A/B 模型不一致，拒绝汇总：{sorted(models)}")
    return {
        "scenarioSetId": SCENARIO_SET_ID,
        "toolControl": {
            "totalCases": len(control["cases"]),
            "baselineBlocked": sum(1 for item in control["baseline"] if item["blocked"]),
            "protectedBlocked": sum(1 for item in protected if item["blocked"]),
            "baselineBlockRatePct": percent(sum(1 for item in control["baseline"] if item["blocked"]), len(control["baseline"])),
            "protectedBlockRatePct": percent(sum(1 for item in protected if item["blocked"]), len(protected)),
            "protectedDownstreamCalls": sum(int(item["downstreamCalls"]) for item in protected),
        },
        "tokenBenchmark": {
            "turnCount": TURN_COUNT,
            "contextCount": CONTEXT_COUNT,
            "baselinePromptTokens": sum(baseline_tokens),
            "optimizedPromptTokens": sum(optimized_tokens),
            "reductionPct": reduction(sum(baseline_tokens), sum(optimized_tokens)),
            "baselinePerTurnP95": percentile(baseline_tokens, 95),
            "optimizedPerTurnP95": percentile(optimized_tokens, 95),
            "model": next(iter(models)),
            "temperature": 0,
            "metric": "DashScope usage.prompt_tokens",
            "baselineConfig": baseline["benchmarkConfig"],
            "optimizedConfig": optimized["benchmarkConfig"],
        },
        "workerRecovery": {
            "runs": len(recovery),
            "successes": sum(1 for item in recovery if item["terminalStatus"] == "COMPLETED" and item["threadPreserved"]),
            "successRatePct": percent(sum(1 for item in recovery if item["terminalStatus"] == "COMPLETED" and item["threadPreserved"]), len(recovery)),
            "restoreP95Ms": percentile(recovery_latencies, 95),
            "redisL2Restores": sum(1 for item in recovery if item["restoreSource"] == "redis_l2"),
            "threadRetentionRatePct": percent(sum(1 for item in recovery if item["threadPreserved"]), len(recovery)),
            "taskId": worker_recovery["taskId"],
            "threadId": worker_recovery["threadId"],
            "benchmarkConfig": worker_recovery["benchmarkConfig"],
            "latencyDefinition": "新 Worker Popen 启动至第二个持久化 context_restore 事件",
        },
    }


def build_report(
    run: BenchmarkRun,
    fixture: dict[str, Any],
    control: dict[str, Any],
    baseline: dict[str, Any],
    optimized: dict[str, Any],
    worker_recovery: dict[str, Any],
    summary: dict[str, Any],
) -> str:
    """生成中文审计报告，引用同目录原始 JSON 而不重复长上下文全文。"""
    token = summary["tokenBenchmark"]
    tool = summary["toolControl"]
    recovery = summary["workerRecovery"]
    return f"""# Agent 线上 A/B 基准测试记录

- 运行 ID：`{run.run_id}`
- 触发方式：React Agent 工作台开发态面板调用 `POST /api/agent/benchmarks/runs`。
- 场景集：`{SCENARIO_SET_ID}`。
- 原始输入：`输入夹具.json`，包含 {len(fixture['contexts'])} 条冻结长上下文和 {len(fixture['turns'])} 条冻结后续轮次。
- 运行环境：`运行配置.json`。
- Token A 任务/线程：`{baseline['taskId']}` / `{baseline['threadId']}`。
- Token B 任务/线程：`{optimized['taskId']}` / `{optimized['threadId']}`。
- Worker 恢复任务/线程：`{worker_recovery['taskId']}` / `{worker_recovery['threadId']}`。

## 测试目标

1. 检查未审批变更和未知工具是否在真实 `tool_adapter_node` 到达下游前被拦截。
2. 比较“完整未摘要原文”和“按 token 阈值触发摘要压缩”两种策略下，Planner 请求的真实模型输入 Token。
3. 在 B 组每轮 `context_restore` 事件已写入 PostgreSQL 后强制终止独立 Worker，验证新进程优先尝试 Redis L2，未命中时基于 PostgreSQL 持久消息和摘要回源完成确定性重建。

## A/B 设计

| 维度 | A 组 | B 组 |
| --- | --- | --- |
| 工具控制 | 历史宽松策略的 safe dry-run，只记“会下发”，不执行真实写入 | 当前 `tool_adapter_node` 白名单和 HITL 门禁 |
| Token 历史窗口 | 摘要关闭、Redis L2 关闭，保留所有未摘要原文作为对照 | 摘要开启、Redis L2 开启，超过 {token['optimizedConfig']['bestWindowTokens']:,} token 时将早期原文压缩为约 {token['optimizedConfig']['summaryTargetTokens']:,} token 摘要，并继续保留阈值内最近原文 |
| Token 任务 | 仅持久化固定用户上下文和 30 条用户轮次，不运行 Worker，避免节点事件污染 Prompt | 同左 |
| Token 口径 | 同一 Planner system/user prompt、模型和温度，读取 DashScope `usage.prompt_tokens` | 同左 |
| Worker 恢复 | 不单独宣称宽松组恢复能力 | 独立 B 组任务每轮在恢复屏障后终止首个进程，再由新 Worker 完成；本次恢复来源以原始事件为准 |

## 输入

- 工具控制：50 条无审批的已知变更工具，加 50 条未知只读工具，详见 `工具控制-原始样本.json`。
- 长会话：24 条项目设计上下文，30 条只读追问，详见 `输入夹具.json`。
- 压缩策略：正常线上默认按 1M 上下文的约 25%（256K）触发，摘要目标约 25K；本基准为在固定小样本中验证同一机制，显式使用 {token['optimizedConfig']['bestWindowTokens']:,}/{token['optimizedConfig']['summaryTargetTokens']:,} token 预算。
- 每轮使用同一模型 `{token['model']}`、温度 `{token['temperature']}`；模型 usage 原始记录见 `DashScope-usage.jsonl`。
- Worker 恢复复用同一 24 条上下文和 30 条追问；每轮首个 Worker 在持久化 `context_restore` 后被终止。

## 结果

| 指标 | A 组 | B 组 | 变化 |
| --- | ---: | ---: | ---: |
| 工具拦截率 | {tool['baselineBlockRatePct']:.2f}% | {tool['protectedBlockRatePct']:.2f}% | +{tool['protectedBlockRatePct'] - tool['baselineBlockRatePct']:.2f}pp |
| 下游错误调用数 | 不适用（dry-run） | {tool['protectedDownstreamCalls']} | B 组必须为 0 |
| 30 轮 Planner prompt Token | {token['baselinePromptTokens']:,} | {token['optimizedPromptTokens']:,} | {token_change_text(token['reductionPct'])} |
| 新 Worker 恢复 | 不适用 | {recovery['successes']}/{recovery['runs']} | {recovery['successRatePct']:.2f}% |
| 新 Worker 恢复 P95 | 不适用 | {recovery['restoreP95Ms']:.2f}ms | {recovery['latencyDefinition']} |
| thread 保持率 | 不适用 | {recovery['threadRetentionRatePct']:.2f}% | 同一 `pythonThreadId` |

B 组新 Worker 的 `redis_l2` 恢复事件为 {recovery['redisL2Restores']}/{recovery['runs']}；其余恢复会被标记为 PostgreSQL 回源，不会被误写为 Redis 命中。

## 产物索引

- `输入夹具.json`：冻结输入。
- `工具控制-原始样本.json`：100 条安全控制结果。
- `A组-Token原始样本.json`、`B组-Token原始样本.json`：逐轮 task/thread/真实模型 usage。
- `B组-Worker恢复原始样本.json`：30 次进程终止、第二个 `context_restore`、终态和线程保持记录。
- `DashScope-usage.jsonl`：每次模型返回的 usage 和 prompt hash，不保存密钥。
- `worker-logs/`：每个独立 Worker 的 stdout/stderr。
- `汇总指标.json`：报告数值来源。
"""


def build_resume_text(summary: dict[str, Any]) -> str:
    """根据真实完成结果生成简历项目描述的局部替换文案。"""
    tool = summary["toolControl"]
    token = summary["tokenBenchmark"]
    recovery = summary["workerRecovery"]
    token_clause = (
        f"24 条长上下文、30 轮 Planner prompt Token {token['baselinePromptTokens']:,}→{token['optimizedPromptTokens']:,}（{token_change_text(token['reductionPct'])}），"
        if token["reductionPct"] >= 0
        else "完成 24 条长上下文、30 轮真实 Token A/B 审计并识别摘要注入开销回归，未将未达标压缩收益写入简历量化项；"
    )
    return (
        "完善 Agent 工作流与执行控制：围绕多步任务追踪、工具越权和长会话膨胀，采用 LangGraph、"
        "LocalAgentGateway 白名单/HITL、PostgreSQL 持久消息/摘要回源与 Redis L2 可重建热态缓存；"
        f"100 条未审批变更/未知工具线上对照拦截率 {tool['baselineBlockRatePct']:.0f}%→{tool['protectedBlockRatePct']:.0f}%，"
        f"{token_clause}"
        f"强杀后新 Worker 基于 PostgreSQL 回源确定性重建恢复 {recovery['successRatePct']:.0f}%（{recovery['successes']}/{recovery['runs']}，"
        f"P95 {recovery['restoreP95Ms']:.2f}ms，thread 保持率 {recovery['threadRetentionRatePct']:.0f}%）。"
    )


def token_change_text(reduction_pct: float) -> str:
    """按 B 相对 A 的 prompt token 变化输出，下降为负号，上升为正号。"""
    return f"-{reduction_pct:.2f}%" if reduction_pct >= 0 else f"+{abs(reduction_pct):.2f}%"


def percentile(values: list[float | int], target: float) -> float:
    """使用线性插值计算 P95，保留逐轮原始样本以便复算。"""
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    rank = (len(ordered) - 1) * target / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * (rank - lower), 3)


def reduction(before: int, after: int) -> float:
    """返回 A 到 B 的下降比例。"""
    return round((before - after) / before * 100, 4) if before else 0.0


def percent(value: int, total: int) -> float:
    """返回百分比，避免零样本除零。"""
    return round(value / total * 100, 4) if total else 0.0


def runtime_snapshot() -> dict[str, Any]:
    """记录可公开的版本与开关，不记录任何密钥或完整连接串。"""
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "scenarioSetId": SCENARIO_SET_ID,
        "tokenMetric": "DashScope usage.prompt_tokens",
        "agentLlmForWorker": False,
        "agentLlmForTokenProbe": True,
        "plannerModel": agent_qwen_model("planner"),
        "redisConfigured": bool(os.getenv("REDIS_URL", "").strip()),
        "databaseConfigured": bool(os.getenv("RAG_DATABASE_URL", "").strip()),
        "contextWindowTokens": 4000,
        "workerCommand": worker_command("<taskId>"),
    }


def require_benchmark_enabled() -> None:
    """避免将仅供本机评测的入口误暴露到常规环境。"""
    if os.getenv("AGENT_BENCHMARK_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        raise AgentBusinessError("AGENT_BENCHMARK_DISABLED: 当前环境未开启 Agent 线上基准")


def iso_now() -> str:
    """返回标准 UTC 时间串。"""
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    """追加每次模型 usage，不把长 prompt 正文写入日志。"""
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(item, ensure_ascii=False) + "\n")


def json_object(value: Any) -> dict[str, Any]:
    """兼容 PostgreSQL JSONB/TEXT 和测试内存仓储的对象字段。"""
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def write_json(path: Path, value: dict[str, Any]) -> None:
    """以 UTF-8 写入结构化审计产物。"""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    """读取运行状态文件，损坏时返回空对象以保留失败处理。"""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}
