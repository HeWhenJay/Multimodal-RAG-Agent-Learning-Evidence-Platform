from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from agents.gateway.java_gateway import JavaAgentGatewayClient
from agents.read_only.read_only_graph import int_value, prefetch_memory_context, task_query, text_value, tool_observation_summary
from agents.resume_adapter.resume_template_fill import fill_resume_template
from app.schemas.agent import AgentTaskEvent, AgentTaskResumeRequest, AgentTaskStartRequest, AgentTaskStartResponse, AgentToolCallEvent


def start_planning_agent(request: AgentTaskStartRequest, client: JavaAgentGatewayClient) -> AgentTaskStartResponse:
    """启动阶段 3 规划任务，先请求用户确认计划。"""
    thread_id = request.threadId or request.taskId
    memory_context = prefetch_memory_context(
        task_id=request.taskId,
        thread_id=thread_id,
        task_input=request.input,
        query=task_query(request.input, "planning_task"),
        client=client,
    )
    plan = build_plan(request.input)
    client.publish_event(
        AgentTaskEvent(
            eventType="TASK_STARTED",
            status="RUNNING",
            pythonThreadId=thread_id,
            draft={"message": "规划 Agent 已启动，等待计划确认", "memoryContext": memory_context},
        )
    )
    client.publish_event(
        AgentTaskEvent(
            eventType="REVIEW_REQUESTED",
            status="WAITING_PLAN_REVIEW",
            pythonThreadId=thread_id,
            draft={"planSummary": plan["title"], "memoryContext": memory_context},
            reviewRequest={
                "id": f"review-plan-{request.taskId}",
                "reviewType": "PLAN",
                "proposal": {**plan, "memoryCount": len(memory_context)},
            },
        )
    )
    return AgentTaskStartResponse(taskId=request.taskId, threadId=thread_id, accepted=True, status="WAITING_PLAN_REVIEW")


def resume_planning_agent(request: AgentTaskResumeRequest, client: JavaAgentGatewayClient) -> AgentTaskStartResponse:
    """根据计划、输出或 CRUD 审批结果恢复规划任务。"""
    thread_id = request.threadId or request.taskId
    if request.decision != "APPROVED":
        client.publish_event(
            AgentTaskEvent(
                eventType="TASK_FAILED",
                status="FAILED",
                pythonThreadId=thread_id,
                errorCode="AGENT_REVIEW_REJECTED",
                errorMessage="用户未批准规划任务继续执行",
            )
        )
        return AgentTaskStartResponse(taskId=request.taskId, threadId=thread_id, accepted=True, status="FAILED")
    if request.reviewType == "CRUD":
        return execute_approved_mutation(request, client, thread_id)
    if request.reviewType == "OUTPUT":
        if should_request_crud_review(request.input):
            review_request = build_crud_review_request(request)
            client.publish_event(
                AgentTaskEvent(
                    eventType="MUTATION_PROPOSED",
                    status="WAITING_CRUD_REVIEW",
                    pythonThreadId=thread_id,
                    draft={"message": "输出已确认，等待保存类变更审批"},
                    reviewRequest=review_request,
                )
            )
            return AgentTaskStartResponse(taskId=request.taskId, threadId=thread_id, accepted=True, status="WAITING_CRUD_REVIEW")
        client.publish_event(
            AgentTaskEvent(
                eventType="TASK_COMPLETED",
                status="COMPLETED",
                pythonThreadId=thread_id,
                final={"answer": "用户已确认规划输出", "riskLevel": "LOW"},
            )
        )
        return AgentTaskStartResponse(taskId=request.taskId, threadId=thread_id, accepted=True, status="COMPLETED")
    draft = execute_planning_analysis(request, client)
    client.publish_event(
        AgentTaskEvent(
            eventType="DRAFT_UPDATED",
            status="RUNNING",
            pythonThreadId=thread_id,
            draft=draft,
        )
    )
    client.publish_event(
        AgentTaskEvent(
            eventType="REVIEW_REQUESTED",
            status="WAITING_OUTPUT_REVIEW",
            pythonThreadId=thread_id,
            draft=draft,
            reviewRequest={
                "id": f"review-output-{request.taskId}",
                "reviewType": "OUTPUT",
                "proposal": {
                    "summary": draft["matchSummary"],
                    "riskLevel": draft["riskLevel"],
                    "evidenceCount": len(draft["evidenceIds"]),
                },
            },
        )
    )
    return AgentTaskStartResponse(taskId=request.taskId, threadId=thread_id, accepted=True, status="WAITING_OUTPUT_REVIEW")


def execute_approved_mutation(
    request: AgentTaskResumeRequest,
    client: JavaAgentGatewayClient,
    thread_id: str,
) -> AgentTaskStartResponse:
    """CRUD 审批通过后调用 Java 变更网关执行保存类操作。"""
    payload = build_mutation_payload(request)
    result = client.execute_mutation_tool(payload)
    status = str(result.get("status") or "FAILED")
    client.publish_event(
        AgentTaskEvent(
            eventType="TOOL_OBSERVATION",
            status="RUNNING" if status == "SUCCEEDED" else "FAILED",
            pythonThreadId=thread_id,
            toolCall=AgentToolCallEvent(
                id=payload["toolCallId"],
                toolName=payload["toolName"],
                toolType="MUTATION",
                status=status,
                response=tool_observation_summary(result),
                ownershipVerified=bool(result.get("ownershipVerified")),
                scope=result.get("scope"),
                errorCode=result.get("errorCode"),
                errorMessage=result.get("errorMessage"),
            ),
        )
    )
    if status != "SUCCEEDED":
        client.publish_event(
            AgentTaskEvent(
                eventType="TASK_FAILED",
                status="FAILED",
                pythonThreadId=thread_id,
                errorCode=str(result.get("errorCode") or "AGENT_MUTATION_FAILED"),
                errorMessage=str(result.get("errorMessage") or "变更工具执行失败"),
            )
        )
        return AgentTaskStartResponse(taskId=request.taskId, threadId=thread_id, accepted=True, status="FAILED")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    client.publish_event(
        AgentTaskEvent(
            eventType="TASK_COMPLETED",
            status="COMPLETED",
            pythonThreadId=thread_id,
            final={
                "answer": "用户已审批并保存 Agent 草稿",
                "operationId": data.get("operationId"),
                "operationStatus": data.get("status"),
                "undoDeadline": data.get("undoDeadline"),
            },
        )
    )
    return AgentTaskStartResponse(taskId=request.taskId, threadId=thread_id, accepted=True, status="COMPLETED")


def should_request_crud_review(task_input: dict[str, Any]) -> bool:
    """判断输出确认后是否需要继续进入 CRUD 审批。"""
    if bool(task_input.get("saveDraft")):
        return True
    tool_hints = task_input.get("toolHints")
    return isinstance(tool_hints, list) and any(
        str(item) in {"jd_learning_plan_save", "resume_revision_save", "agent_task_cancel_request"} for item in tool_hints
    )


def build_crud_review_request(request: AgentTaskResumeRequest) -> dict[str, Any]:
    """构造保存类 CRUD 审批请求。"""
    tool_name = mutation_tool_name(request.input)
    operation_type = {
        "resume_revision_save": "RESUME_REVISION_SAVE",
        "jd_learning_plan_save": "JD_PLAN_SAVE",
        "agent_task_cancel_request": "TASK_CANCEL",
    }[tool_name]
    idempotency_key = mutation_idempotency_key(request, tool_name)
    return {
        "id": f"review-crud-{request.taskId}",
        "reviewType": "CRUD",
        "proposal": {
            "title": "保存 Agent 草稿确认",
            "toolName": tool_name,
            "operationType": operation_type,
            "resourceType": "agent_task" if tool_name == "agent_task_cancel_request" else "agent_task_draft",
            "resourceId": request.taskId,
            "idempotencyKey": idempotency_key,
            "riskLevel": "MEDIUM",
            "undoable": True,
            "undoWindowMinutes": 30,
            "summary": "该操作只会修改当前 Agent 任务自身状态，不会重建资料索引或写入外部业务表。",
        },
    }


def build_mutation_payload(request: AgentTaskResumeRequest) -> dict[str, Any]:
    """根据 CRUD 审批决策构造 Java mutation gateway 请求。"""
    tool_name = text_value(request.decisionPayload.get("toolName")) or mutation_tool_name(request.input)
    review_id = text_value(request.decisionPayload.get("reviewId")) or f"review-crud-{request.taskId}"
    idempotency_key = (
        text_value(request.decisionPayload.get("idempotencyKey"))
        or mutation_idempotency_key(request, tool_name)
    )
    operation_id = text_value(request.decisionPayload.get("operationId")) or f"operation-{tool_name}-{request.taskId}"
    return {
        "taskId": request.taskId,
        "toolCallId": f"tool-call-mutation-{uuid.uuid4().hex}",
        "approvalId": review_id,
        "operationId": operation_id,
        "toolName": tool_name,
        "idempotencyKey": idempotency_key,
        "arguments": {
            "source": "planning_task",
            "reason": request.decisionPayload.get("comment") or "用户审批通过保存 Agent 草稿",
        },
    }


def mutation_tool_name(task_input: dict[str, Any]) -> str:
    """从输入提示中选择阶段 4 初版支持的变更工具。"""
    tool_hints = task_input.get("toolHints")
    if isinstance(tool_hints, list):
        for item in tool_hints:
            if str(item) in {"resume_revision_save", "jd_learning_plan_save", "agent_task_cancel_request"}:
                return str(item)
    return "jd_learning_plan_save"


def mutation_idempotency_key(request: AgentTaskResumeRequest, tool_name: str) -> str:
    """生成稳定的保存类幂等键。"""
    return f"{tool_name}-{request.taskId}-v1"


def build_plan(task_input: dict[str, Any]) -> dict[str, Any]:
    """生成规划类任务的计划审批内容。"""
    goal = text_value(task_input.get("goal")) or "JD/简历适配分析"
    return {
        "title": f"{goal[:40]} 计划",
        "steps": ["读取当前用户 RAG 证据", "对齐 JD 要求与简历证据", "生成能力缺口和学习建议"],
        "tools": ["rag_query_probe_non_persistent", "resume_evidence_aligner", "gap_analyzer", "evidence_quality_auditor"],
        "requiresOutputReview": True,
        "riskLevel": "LOW",
        "guardrails": ["只读分析", "不保存业务数据", "不执行 CRUD 变更"],
    }


def execute_planning_analysis(request: AgentTaskResumeRequest, client: JavaAgentGatewayClient) -> dict[str, Any]:
    """执行只读证据检索并生成 JD/简历适配草稿。"""
    task_input = request.input or {}
    jd_text = text_value(task_input.get("jobDescription"))
    resume_text = text_value(task_input.get("resumeText"))
    goal = text_value(task_input.get("goal")) or "分析 JD 与简历证据差距"
    memory_context = prefetch_memory_context(
        task_id=request.taskId,
        thread_id=request.threadId or request.taskId,
        task_input=task_input,
        query=task_query(task_input, "planning_task"),
        client=client,
    )
    web_references = execute_web_search_if_enabled(request, client, goal, jd_text)
    question = build_evidence_question(goal, jd_text, resume_text)
    tool_call_id = f"tool-call-{uuid.uuid4().hex}"
    result = client.execute_read_tool(
        {
            "taskId": request.taskId,
            "toolCallId": tool_call_id,
            "toolName": "rag_query_probe_non_persistent",
            "arguments": {
                "question": question,
                "topK": int_value(task_input.get("topK"), 6),
                "candidateMultiplier": int_value(task_input.get("candidateMultiplier"), 4),
            },
        }
    )
    client.publish_event(
        AgentTaskEvent(
            eventType="TOOL_OBSERVATION",
            status="RUNNING",
            pythonThreadId=request.threadId or request.taskId,
            toolCall=AgentToolCallEvent(
                id=tool_call_id,
                toolName="rag_query_probe_non_persistent",
                status=str(result.get("status") or "FAILED"),
                response=tool_observation_summary(result),
                ownershipVerified=bool(result.get("ownershipVerified")),
                scope=result.get("scope"),
                errorCode=result.get("errorCode"),
                errorMessage=result.get("errorMessage"),
            ),
        )
    )
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    evidences = data.get("evidences") if isinstance(data.get("evidences"), list) else []
    evidence_ids = [str(item.get("evidenceId")) for item in evidences if isinstance(item, dict) and item.get("evidenceId")]
    requirements = extract_requirements(jd_text or goal)
    alignment = build_alignment(requirements, resume_text, evidence_ids)
    gaps = build_gaps(alignment)
    risk_level = "LOW" if evidence_ids and not any(item["status"] == "missing" for item in alignment) else "MEDIUM"
    resume_template_fill = fill_resume_template_if_requested(request, alignment, gaps, evidence_ids)
    draft = {
        "matchSummary": build_match_summary(alignment, evidence_ids),
        "alignment": alignment,
        "gaps": gaps,
        "evidenceIds": evidence_ids,
        "memoryContext": memory_context,
        "webReferences": web_references,
        "resumeTemplateFill": resume_template_fill,
        "answer": text_value(data.get("answer")),
        "expandedQueries": data.get("expandedQueries") if isinstance(data.get("expandedQueries"), list) else [],
        "riskLevel": risk_level,
    }
    pending_candidates = propose_memory_candidates(request, client, draft)
    if pending_candidates:
        draft["pendingMemoryCandidates"] = pending_candidates
        draft["memoryCandidateCount"] = len(pending_candidates)
    return draft


def propose_memory_candidates(request: AgentTaskResumeRequest, client: JavaAgentGatewayClient, draft: dict[str, Any]) -> list[dict[str, Any]]:
    """请求 Java Tool Gateway 生成待确认记忆候选，不直接落库或激活。"""
    payload = {
        "taskId": request.taskId,
        "toolCallId": f"tool-call-memory-candidate-{uuid.uuid4().hex}",
        "toolName": "agent_memory_candidate_proposer",
        "arguments": {
            "taskInput": request.input or {},
            "draft": draft,
            "final": {},
            "toolObservations": [],
        },
    }
    try:
        result = client.execute_read_tool(payload)
    except Exception:
        return []
    if result.get("status") != "SUCCEEDED":
        return []
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    candidates = data.get("candidates")
    if not isinstance(candidates, list):
        return []
    return [item for item in candidates if isinstance(item, dict)]


def execute_web_search_if_enabled(
    request: AgentTaskResumeRequest,
    client: JavaAgentGatewayClient,
    goal: str,
    jd_text: str,
) -> list[dict[str, Any]]:
    """按用户输入启用联网参考，失败时不阻断本地 RAG 对齐。"""
    task_input = request.input or {}
    if not web_search_enabled(task_input):
        return []
    query = text_value(task_input.get("webSearchQuery")) or build_web_search_query(goal, jd_text)
    tool_call_id = f"tool-call-web-{uuid.uuid4().hex}"
    result = client.execute_read_tool(
        {
            "taskId": request.taskId,
            "toolCallId": tool_call_id,
            "toolName": "web_search_probe",
            "arguments": {
                "query": query,
                "maxResults": int_value(task_input.get("webSearchMaxResults"), 5),
                "searchDepth": text_value(task_input.get("webSearchDepth")) or "basic",
                "topic": "general",
            },
        }
    )
    client.publish_event(
        AgentTaskEvent(
            eventType="TOOL_OBSERVATION",
            status="RUNNING",
            pythonThreadId=request.threadId or request.taskId,
            toolCall=AgentToolCallEvent(
                id=tool_call_id,
                toolName="web_search_probe",
                status=str(result.get("status") or "FAILED"),
                response=tool_observation_summary(result),
                ownershipVerified=bool(result.get("ownershipVerified")),
                scope=result.get("scope"),
                errorCode=result.get("errorCode"),
                errorMessage=result.get("errorMessage"),
            ),
        )
    )
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    references = data.get("results") if isinstance(data.get("results"), list) else []
    return [item for item in references if isinstance(item, dict)]


def web_search_enabled(task_input: dict[str, Any]) -> bool:
    """判断是否启用联网参考工具。"""
    if bool(task_input.get("enableWebSearch")):
        return True
    tool_hints = task_input.get("toolHints")
    return isinstance(tool_hints, list) and any(str(item) == "web_search_probe" for item in tool_hints)


def build_web_search_query(goal: str, jd_text: str) -> str:
    """生成联网搜索查询文本。"""
    if jd_text:
        return f"{goal} 公司背景 技能趋势 {jd_text[:160]}"
    return f"{goal} 公司背景 技能趋势"


def build_evidence_question(goal: str, jd_text: str, resume_text: str) -> str:
    """组合用于 RAG 探针的检索问题。"""
    parts = [goal]
    if jd_text:
        parts.append(f"JD 要求：{jd_text[:800]}")
    if resume_text:
        parts.append(f"简历摘要：{resume_text[:500]}")
    return "\n".join(parts)


def fill_resume_template_if_requested(
    request: AgentTaskResumeRequest,
    alignment: list[dict[str, Any]],
    gaps: list[dict[str, str]],
    evidence_ids: list[str],
) -> dict[str, Any]:
    """按输入模板路径生成简历 DOCX 填充草稿。"""
    task_input = request.input or {}
    if not resume_template_fill_enabled(task_input):
        return {}
    template_path_text = text_value(task_input.get("resumeTemplatePath"))
    if not template_path_text:
        return {
            "status": "SKIPPED",
            "errorCode": "RESUME_TEMPLATE_PATH_MISSING",
            "errorMessage": "未提供简历模板路径",
        }
    template_path = Path(template_path_text)
    output_dir = Path(text_value(task_input.get("resumeTemplateOutputDir")) or str(template_path.parent))
    output_path = output_dir / f"resume-{request.taskId}.docx"
    content_map = build_resume_content_map(request.input, alignment, gaps, evidence_ids)
    result = fill_resume_template(template_path=template_path, output_path=output_path, content_map=content_map)
    result["toolName"] = "resume_template_fill"
    result["contentMap"] = content_map
    return result


def resume_template_fill_enabled(task_input: dict[str, Any]) -> bool:
    """判断是否启用简历模板填充。"""
    tool_hints = task_input.get("toolHints")
    return isinstance(tool_hints, list) and any(str(item) == "resume_template_fill" for item in tool_hints)


def build_resume_content_map(
    task_input: dict[str, Any],
    alignment: list[dict[str, Any]],
    gaps: list[dict[str, str]],
    evidence_ids: list[str],
) -> dict[str, str]:
    """基于 JD、简历摘要和 evidence 生成占位符填充值。"""
    resume_text = text_value(task_input.get("resumeText"))
    supported = [item["requirement"] for item in alignment if item.get("status") == "supported"]
    weak = [item["requirement"] for item in alignment if item.get("status") == "weak"]
    missing = [item["requirement"] for item in alignment if item.get("status") == "missing"]
    skills = supported + weak
    return {
        "summary": build_resume_summary(resume_text, supported, evidence_ids),
        "skills": " / ".join(skills[:8]) or "待补充岗位相关技能",
        "project_experience": build_project_experience(supported, evidence_ids),
        "learning_plan": "；".join(item["suggestion"] for item in gaps[:3]) if gaps else "当前证据已覆盖主要岗位要求",
        "gap_summary": f"强支撑 {len(supported)} 项，证据偏弱 {len(weak)} 项，缺证据 {len(missing)} 项",
    }


def build_resume_summary(resume_text: str, supported: list[str], evidence_ids: list[str]) -> str:
    """生成简历摘要占位符内容。"""
    base = resume_text[:120] if resume_text else "具备岗位相关项目学习与实践经历"
    if supported:
        return f"{base}；重点匹配 {'、'.join(supported[:4])}，可引用 evidence {len(evidence_ids)} 条。"
    return f"{base}；当前知识库 evidence 仍需补充。"


def build_project_experience(supported: list[str], evidence_ids: list[str]) -> str:
    """生成项目经历占位符内容。"""
    if supported:
        return f"围绕 {'、'.join(supported[:4])} 完成项目实践，沉淀可追溯学习证据 {len(evidence_ids)} 条。"
    return "围绕目标岗位继续补充项目实践、学习笔记和可引用成果。"


def extract_requirements(text: str) -> list[str]:
    """从 JD 文本中抽取简短要求，失败时使用通用要求。"""
    candidates = [item.strip(" ，。；;、") for item in re.split(r"[，。；;\n]", text) if item.strip()]
    filtered = [item for item in candidates if 2 <= len(item) <= 40]
    return (filtered or ["项目经验", "核心技术栈", "学习证据"])[:6]


def build_alignment(requirements: list[str], resume_text: str, evidence_ids: list[str]) -> list[dict[str, Any]]:
    """生成 supported/weak/missing 对齐矩阵。"""
    alignment: list[dict[str, Any]] = []
    for index, requirement in enumerate(requirements):
        supported_by_resume = requirement.lower() in resume_text.lower() if resume_text else False
        if evidence_ids and (supported_by_resume or index == 0):
            status = "supported"
        elif evidence_ids:
            status = "weak"
        else:
            status = "missing"
        alignment.append(
            {
                "requirement": requirement,
                "status": status,
                "evidenceIds": evidence_ids[:2] if status != "missing" else [],
                "reason": alignment_reason(status),
            }
        )
    return alignment


def build_gaps(alignment: list[dict[str, Any]]) -> list[dict[str, str]]:
    """根据对齐矩阵生成能力缺口。"""
    gaps = []
    for item in alignment:
        if item["status"] != "supported":
            gaps.append(
                {
                    "skill": item["requirement"],
                    "priority": "HIGH" if item["status"] == "missing" else "MEDIUM",
                    "suggestion": f"补充 {item['requirement']} 的项目证据、学习笔记或可引用成果",
                }
            )
    return gaps


def build_match_summary(alignment: list[dict[str, Any]], evidence_ids: list[str]) -> str:
    """生成简短中文匹配摘要。"""
    supported = sum(1 for item in alignment if item["status"] == "supported")
    weak = sum(1 for item in alignment if item["status"] == "weak")
    missing = sum(1 for item in alignment if item["status"] == "missing")
    return f"已基于当前用户知识库生成适配草稿：支持 {supported} 项、证据偏弱 {weak} 项、缺证据 {missing} 项；引用 evidence {len(evidence_ids)} 条。"


def alignment_reason(status: str) -> str:
    """返回对齐状态说明。"""
    return {
        "supported": "简历或知识库 evidence 能支撑该要求",
        "weak": "已有相关 evidence，但支撑强度不足",
        "missing": "当前知识库未找到可引用 evidence",
    }.get(status, "待确认")
