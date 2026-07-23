"""简历证据改写 LangGraph 子图的节点契约测试。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agents.orchestration import pae_react_graph as graph
from app.schemas.agent import AgentTaskEvent


class FailingJsonClient:
    """让节点使用确定性 fallback，避免单元测试依赖真实模型和密钥。"""

    def complete_json(self, **_kwargs: Any) -> Any:
        raise RuntimeError("测试环境不调用远程模型")


class CapturingJsonClient:
    """捕获验收节点提示词，确认其能看到字段候选和 evidence 引文。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(data={"accepted": True, "reason": "证据链可进入人工审批"}, model="test-model")


class ScriptedGateway:
    """记录子图工具顺序，并返回带完整来源字段的受控 RAG 结果。"""

    def __init__(self, evidences: list[dict[str, Any]]) -> None:
        self.evidences = evidences
        self.tool_names: list[str] = []
        self.events: list[dict[str, Any]] = []

    def execute_read_tool(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool_name = str(payload["toolName"])
        self.tool_names.append(tool_name)
        if tool_name == "rag_query_probe_non_persistent":
            return {
                "taskId": payload["taskId"],
                "toolCallId": payload["toolCallId"],
                "toolName": tool_name,
                "status": "SUCCEEDED",
                "ownershipVerified": True,
                "scope": "current_user",
                "data": {
                    "answer": "已找到学习证据。",
                    "expandedQueries": [payload["arguments"]["question"]],
                    "evidences": self.evidences,
                    "evidenceCount": len(self.evidences),
                    "diagnostics": {"provider": "test-rag"},
                },
            }
        if tool_name == "agent_memory_candidate_proposer":
            return {"status": "SUCCEEDED", "data": {"candidates": []}}
        raise AssertionError(f"不应调用工具：{tool_name}")

    def restore_context(self, _task_id: str, **_kwargs: Any) -> dict[str, Any]:
        """提供空上下文，使完整 StateGraph 可在无数据库测试替身下执行。"""
        return {
            "messageWindow": [],
            "compressionCandidateMessages": [],
            "activeSummaries": [],
            "summarySegments": [],
            "budgetMetadata": {"restoreSource": "test"},
        }

    def publish_event(self, event: AgentTaskEvent) -> None:
        self.events.append(event.model_dump(by_alias=True, exclude_none=True))


@pytest.fixture
def resume_input() -> dict[str, Any]:
    """提供包含 JD、原简历和私有检索约束的最小输入。"""
    return {
        "goal": "针对 AI Agent 开发实习优化我的项目经历",
        "resumeRewriteRequested": True,
        "targetJobTitle": "AI Agent 开发实习生",
        "jobDescription": "熟悉 Python、FastAPI 和 RAG 检索；具备 Agent 工作流项目经验优先。",
        "resumeText": "项目：学习证据平台，使用 Python 和 FastAPI 实现资料检索服务。",
        "metadataFilter": {"documentType": "learning_material"},
        "topK": 4,
    }


@pytest.fixture
def rag_evidences() -> list[dict[str, Any]]:
    """模拟 RAG 返回的完整 evidence 元数据。"""
    return [
        {
            "evidenceId": "evidence-rag-1",
            "documentTitle": "RAG 项目报告",
            "sectionName": "混合检索实现",
            "snippet": "使用 Python 与 FastAPI 构建 RAG 服务，结合 BM25、向量召回和 RRF 融合。",
            "source": "learning_material",
            "score": 0.94,
        }
    ]


def build_resume_state(task_input: dict[str, Any]) -> dict[str, Any]:
    """构造已通过 PLAN 审批的简历改写状态。"""
    state = graph.initial_state("task-resume-1", "planning_task", "thread-resume-1", task_input, plan_approved=True)
    return {**state, "subgraph": "planning", "resume_rewrite_required": True}


def test_compiled_graph_keeps_resume_agents_in_evidence_first_order() -> None:
    """StateGraph 的静态边必须保证修改建议不会绕过 JD 或 RAG evidence 节点。"""
    compiled = graph.build_unified_graph(ScriptedGateway([]))
    graph_view = compiled.get_graph()
    edges = {(edge.source, edge.target) for edge in graph_view.edges}

    assert ("resume_jd_analyzer", "resume_evidence_retriever") in edges
    assert ("resume_evidence_retriever", "resume_evidence_summarizer") in edges
    assert ("resume_evidence_summarizer", "resume_revision_advisor") in edges
    assert ("resume_revision_advisor", "resume_patch_builder") in edges
    assert ("resume_patch_builder", "resume_rewrite_acceptance") in edges


def test_full_graph_routes_resume_rewrite_to_output_review_without_second_rag(monkeypatch: pytest.MonkeyPatch, resume_input: dict[str, Any], rag_evidences: list[dict[str, Any]]) -> None:
    """批准 PLAN 后，完整图应仅检索一次 evidence 并直接生成 OUTPUT 审批草稿。"""
    monkeypatch.setattr(graph, "get_agent_qwen_client", lambda: FailingJsonClient())
    gateway = ScriptedGateway(rag_evidences)
    state = graph.initial_state("task-resume-full", "planning_task", "thread-resume-full", resume_input, plan_approved=True)

    result = graph.build_unified_graph(gateway).invoke(state)

    assert result["status"] == "WAITING_OUTPUT_REVIEW"
    assert gateway.tool_names == ["rag_query_probe_non_persistent", "agent_memory_candidate_proposer"]
    assert result["draft_result"]["resumeRewrite"]["patches"]
    assert any(event["eventType"] == "REVIEW_REQUESTED" for event in gateway.events)


def test_resume_rewrite_nodes_retrieve_evidence_before_generating_patch(monkeypatch: pytest.MonkeyPatch, resume_input: dict[str, Any], rag_evidences: list[dict[str, Any]]) -> None:
    """JD、RAG、证据摘要、建议、补丁的顺序必须固定，建议不能早于 evidence。"""
    monkeypatch.setattr(graph, "get_agent_qwen_client", lambda: FailingJsonClient())
    gateway = ScriptedGateway(rag_evidences)
    state = build_resume_state(resume_input)

    state = graph.resume_jd_analyzer_node(state, gateway)
    assert state["resume_jd_profile"]["mustRequirements"]
    assert gateway.tool_names == []

    state = graph.resume_evidence_retriever_node(state, gateway)
    assert gateway.tool_names == ["rag_query_probe_non_persistent"]
    assert state["resume_evidence_bundle"]["items"][0] == rag_evidences[0]

    state = graph.resume_evidence_summarizer_node(state, gateway)
    assert state["resume_evidence_bundle"]["evidenceIds"] == ["evidence-rag-1"]
    assert state["resume_evidence_bundle"]["requirementEvidence"]

    state = graph.resume_revision_advisor_node(state, gateway)
    assert state["resume_revision_advice"]["patches"]
    assert all(
        set(patch.get("evidenceIds") or []).issubset({"evidence-rag-1"})
        for patch in state["resume_revision_advice"]["patches"]
    )

    state = graph.resume_patch_builder_node(state)
    assert state["resume_patch_candidate"]["toolName"] == "resume_patch_builder"
    assert all(patch["status"] == "PENDING_REVIEW" for patch in state["resume_patch_candidate"]["patches"])

    state = graph.resume_rewrite_acceptance_node(state, gateway)
    assert state["status"] == "WAITING_OUTPUT_REVIEW"
    assert state["draft_result"]["jdProfile"] == state["resume_jd_profile"]
    assert state["draft_result"]["evidenceBundle"]["items"][0]["snippet"] == rag_evidences[0]["snippet"]
    assert gateway.tool_names == ["rag_query_probe_non_persistent", "agent_memory_candidate_proposer"]


def test_resume_rewrite_without_related_evidence_only_returns_gap_suggestions(monkeypatch: pytest.MonkeyPatch, resume_input: dict[str, Any]) -> None:
    """空 evidence 不能被错误包装为岗位能力支撑。"""
    monkeypatch.setattr(graph, "get_agent_qwen_client", lambda: FailingJsonClient())
    gateway = ScriptedGateway([])
    state = build_resume_state(resume_input)

    state = graph.resume_jd_analyzer_node(state, gateway)
    state = graph.resume_evidence_retriever_node(state, gateway)
    state = graph.resume_evidence_summarizer_node(state, gateway)
    state = graph.resume_revision_advisor_node(state, gateway)

    assert state["resume_evidence_bundle"]["evidenceIds"] == []
    assert all(
        patch["riskFlags"] == ["MISSING_EVIDENCE"]
        for patch in state["resume_revision_advice"]["patches"]
        if patch["field"] in {"summary", "skills", "project_experience"}
    )
    assert all(
        not patch["evidenceIds"]
        for patch in state["resume_revision_advice"]["patches"]
        if patch["field"] in {"summary", "skills", "project_experience"}
    )


def test_revision_advice_strips_unknown_evidence_and_never_confirms_patch() -> None:
    """模型返回的未知 evidence 和确认状态不得进入最终字段候选。"""
    fallback = {
        "contentMap": {"project_experience": "基于学习证据改写项目经历"},
        "rewriteTargets": ["RAG 检索"],
        "patches": [],
        "message": "安全 fallback",
    }
    candidate = {
        "contentMap": {"project_experience": "实现 RAG 检索服务"},
        "patches": [
            {
                "field": "project_experience",
                "suggestedText": "实现 RAG 检索服务",
                "reason": "测试来源过滤",
                "evidenceIds": ["unknown-evidence"],
                "riskFlags": ["NONE"],
                "status": "CONFIRMED",
            }
        ],
        "message": "测试建议",
    }

    advice = graph.sanitize_resume_revision_advice(candidate, fallback, {"evidenceIds": ["allowed-evidence"]})
    patch = advice["patches"][0]

    assert patch["evidenceIds"] == []
    assert patch["riskFlags"] == ["MISSING_EVIDENCE"]
    assert patch["status"] == "PENDING_REVIEW"


def test_unrelated_evidence_and_model_failure_cannot_support_resume_fact(monkeypatch: pytest.MonkeyPatch, resume_input: dict[str, Any]) -> None:
    """无关 Java 资料即使在模型离线 fallback 下也不能支撑 Python 年限或性能事实。"""
    monkeypatch.setattr(graph, "get_agent_qwen_client", lambda: FailingJsonClient())
    unrelated_evidence = [
        {
            "evidenceId": "e-java",
            "documentTitle": "Java 基础课程笔记",
            "sectionName": "集合框架",
            "snippet": "学习 Java 集合框架、List 与 Map 的基本使用。",
            "source": "learning_material",
            "score": 0.96,
        }
    ]
    gateway = ScriptedGateway(unrelated_evidence)
    state = build_resume_state(resume_input)

    state = graph.resume_jd_analyzer_node(state, gateway)
    state = graph.resume_evidence_retriever_node(state, gateway)
    state = graph.resume_evidence_summarizer_node(state, gateway)
    state = graph.resume_revision_advisor_node(state, gateway)

    assert state["resume_evidence_bundle"]["supportedEvidenceIds"] == []
    for patch in state["resume_revision_advice"]["patches"]:
        if patch["field"] in {"summary", "skills", "project_experience"}:
            assert patch["evidenceIds"] == []
            assert patch["riskFlags"] == ["MISSING_EVIDENCE"]
            assert "五年" not in patch["suggestedText"]
            assert "99%" not in patch["suggestedText"]


def test_weak_requirement_cannot_enter_none_risk_skill_patch(monkeypatch: pytest.MonkeyPatch, resume_input: dict[str, Any]) -> None:
    """仅命中简历或 evidence 一侧的弱匹配，不能被写成已掌握技能。"""
    monkeypatch.setattr(graph, "get_agent_qwen_client", lambda: FailingJsonClient())
    state = build_resume_state(resume_input)
    state["resume_jd_profile"] = {
        "jobTitle": "后端实习生",
        "mustRequirements": [
            {"id": "req-python", "requirement": "Python", "priority": "HIGH", "keywords": ["Python"]},
            {"id": "req-rag", "requirement": "RAG", "priority": "HIGH", "keywords": ["RAG"]},
        ],
        "preferredRequirements": [],
    }
    state["resume_evidence_bundle"] = {
        "evidenceIds": ["e-rag"],
        "items": [{"evidenceId": "e-rag", "documentTitle": "RAG 课程", "sectionName": "检索", "snippet": "学习 RAG 检索基础。", "source": "learning_material", "score": 0.9}],
        "requirementEvidence": [
            {"requirementId": "req-python", "evidenceIds": []},
            {"requirementId": "req-rag", "evidenceIds": ["e-rag"]},
        ],
    }

    result = graph.resume_revision_advisor_node(state)
    skill_patch = next(patch for patch in result["resume_revision_advice"]["patches"] if patch["field"] == "skills")

    assert result["resume_evidence_bundle"]["supportedEvidenceIds"] == []
    assert skill_patch["suggestedText"] == "待补充岗位相关技能"
    assert skill_patch["evidenceIds"] == []
    assert skill_patch["riskFlags"] == ["MISSING_EVIDENCE"]


def test_course_evidence_cannot_become_project_experience_in_offline_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """课程基础练习即使匹配 JD，也不能在离线回退中被伪造为项目实践。"""
    monkeypatch.setattr(graph, "get_agent_qwen_client", lambda: FailingJsonClient())
    task_input = {
        "goal": "针对 Java 岗位优化简历",
        "resumeRewriteRequested": True,
        "targetJobTitle": "Java 开发实习生",
        "jobDescription": "熟悉 Java",
        "resumeText": "熟悉 Java 基础语法",
        "metadataFilter": {"documentType": "learning_material"},
        "topK": 3,
    }
    gateway = ScriptedGateway(
        [
            {
                "evidenceId": "e-course",
                "documentTitle": "Java 基础课程",
                "sectionName": "集合框架练习",
                "snippet": "完成 Java 集合框架基础练习。",
                "source": "learning_material",
                "score": 0.91,
            }
        ]
    )
    state = build_resume_state(task_input)

    state = graph.resume_jd_analyzer_node(state, gateway)
    state = graph.resume_evidence_retriever_node(state, gateway)
    state = graph.resume_evidence_summarizer_node(state, gateway)
    state = graph.resume_revision_advisor_node(state, gateway)
    project_patch = next(patch for patch in state["resume_revision_advice"]["patches"] if patch["field"] == "project_experience")

    assert state["resume_evidence_bundle"]["supportedEvidenceIds"] == ["e-course"]
    assert state["resume_evidence_bundle"]["projectEvidenceIds"] == []
    assert project_patch["evidenceIds"] == []
    assert project_patch["evidenceQuotes"] == []
    assert project_patch["riskFlags"] == ["MISSING_EVIDENCE"]
    assert not any(term in project_patch["suggestedText"] for term in {"项目实践", "实现", "开发", "构建"})


def test_development_course_evidence_cannot_ground_project_patch_even_with_exact_quote() -> None:
    """标题含开发但属于基础课程时，模型引用原文也不能把练习升级为项目经历。"""
    fallback = {
        "contentMap": {"project_experience": "当前证据不足以自动改写项目经历；请补充可引用的项目材料并由用户确认。"},
        "rewriteTargets": ["Java"],
        "patches": [
            {
                "field": "project_experience",
                "label": "项目经历",
                "suggestedText": "当前证据不足以自动改写项目经历；请补充可引用的项目材料并由用户确认。",
                "reason": "安全回退",
                "evidenceIds": [],
                "evidenceQuotes": [],
                "riskFlags": ["MISSING_EVIDENCE"],
                "status": "PENDING_REVIEW",
            }
        ],
        "gapSuggestions": [],
        "message": "安全 fallback",
    }
    bundle = {
        "evidenceIds": ["e-course"],
        "supportedEvidenceIds": ["e-course"],
        "requirementEvidence": [{"requirementId": "req-java", "evidenceIds": ["e-course"]}],
        "items": [
            {
                "evidenceId": "e-course",
                "documentTitle": "Java 开发基础课程",
                "sectionName": "集合框架练习",
                "snippet": "完成 Java 开发基础练习。",
            }
        ],
    }
    candidate = {
        "patches": [
            {
                "field": "project_experience",
                "suggestedText": "完成 Java 项目。",
                "reason": "模型错误升级课程练习",
                "evidenceIds": ["e-course"],
                "evidenceQuotes": [{"evidenceId": "e-course", "quote": "完成 Java 开发基础练习"}],
                "riskFlags": ["NONE"],
            }
        ]
    }

    advice = graph.sanitize_resume_revision_advice(candidate, fallback, bundle, "熟悉 Java 基础语法")
    patch = advice["patches"][0]

    assert patch["suggestedText"] == fallback["contentMap"]["project_experience"]
    assert patch["evidenceIds"] == []
    assert patch["riskFlags"] == ["MISSING_EVIDENCE"]


def test_revision_advice_rejects_unsupported_chinese_responsibility_claim() -> None:
    """精确引文未包含主导或上线等职责词时，模型不得新增这类中文事实。"""
    fallback = {
        "contentMap": {"project_experience": "围绕岗位要求补充可引用项目证据。"},
        "rewriteTargets": ["Python"],
        "patches": [],
        "gapSuggestions": [],
        "message": "安全 fallback",
    }
    bundle = {
        "evidenceIds": ["e-python"],
        "supportedEvidenceIds": ["e-python"],
        "requirementEvidence": [{"requirementId": "req-1", "evidenceIds": ["e-python"]}],
        "items": [
            {
                "evidenceId": "e-python",
                "documentTitle": "Python 课程记录",
                "sectionName": "基础语法",
                "snippet": "使用 Python 完成基础语法练习。",
            }
        ],
    }
    candidate = {
        "patches": [
            {
                "field": "project_experience",
                "suggestedText": "主导 Python 平台上线并降低延迟 99%。",
                "reason": "测试中文职责和指标校验",
                "evidenceIds": ["e-python"],
                "evidenceQuotes": [{"evidenceId": "e-python", "quote": "使用 Python 完成基础语法练习"}],
                "riskFlags": ["NONE"],
            }
        ]
    }

    advice = graph.sanitize_resume_revision_advice(candidate, fallback, bundle, "")
    patch = advice["patches"][0]

    assert patch["suggestedText"] == fallback["contentMap"]["project_experience"]
    assert patch["riskFlags"] == ["MISSING_EVIDENCE"]
    assert patch["evidenceIds"] == []


def test_acceptance_prompt_contains_patch_evidence_and_risk(monkeypatch: pytest.MonkeyPatch) -> None:
    """验收子 Agent 必须读取实际字段候选、evidence 引文、风险和缺口，而非空摘要。"""
    client = CapturingJsonClient()
    monkeypatch.setattr(graph, "get_agent_qwen_client", lambda: client)
    state = {
        "task_input": {},
        "resume_rewrite_draft": {
            "patches": [
                {
                    "field": "project_experience",
                    "suggestedText": "使用 Python 完成检索服务。",
                    "evidenceIds": ["e1"],
                    "evidenceQuotes": [{"evidenceId": "e1", "quote": "使用 Python 完成检索服务"}],
                    "riskFlags": ["NONE"],
                    "status": "PENDING_REVIEW",
                }
            ],
            "gapSuggestions": [{"skill": "Agent 编排", "priority": "HIGH", "suggestion": "补充项目证据"}],
            "evidenceBundle": {
                "evidenceIds": ["e1"],
                "items": [{"evidenceId": "e1", "documentTitle": "项目报告", "sectionName": "实现", "snippet": "使用 Python 完成检索服务"}],
            },
        },
    }

    result = graph.build_llm_resume_rewrite_acceptance(state, {"accepted": False})

    assert result["accepted"] is True
    assert client.calls
    prompt = client.calls[0]["user_prompt"]
    assert "patchReview" in prompt
    assert "project_experience" in prompt
    assert "evidenceQuotes" in prompt
    assert "gapSuggestions" in prompt
