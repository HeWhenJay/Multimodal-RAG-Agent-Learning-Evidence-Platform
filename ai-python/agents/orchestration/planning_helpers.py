from __future__ import annotations

import re
import uuid
from typing import Any

from agents.gateway.java_gateway import JavaAgentGatewayClient
from agents.orchestration.read_only_helpers import text_value, tool_observation_summary
from app.schemas.agent import AgentTaskEvent, AgentTaskResumeRequest, AgentTaskStartResponse, AgentToolCallEvent


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
            eventType="TOOL_CALL_COMPLETED",
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
        str(item) in {
            "jd_learning_plan_save",
            "resume_revision_save",
            "agent_task_cancel_request",
            "agent_memory_candidate_save",
        }
        for item in tool_hints
    )


def build_mutation_payload(request: AgentTaskResumeRequest) -> dict[str, Any]:
    """根据 CRUD 审批决策构造 Java mutation gateway 请求。"""
    tool_name = text_value(request.decisionPayload.get("toolName")) or mutation_tool_name(request.input)
    review_id = text_value(request.decisionPayload.get("reviewId")) or f"review-crud-{request.taskId}"
    idempotency_key = text_value(request.decisionPayload.get("idempotencyKey")) or mutation_idempotency_key(request, tool_name)
    operation_id = text_value(request.decisionPayload.get("operationId")) or f"operation-{tool_name}-{request.taskId}"
    return {
        "taskId": request.taskId,
        "toolCallId": f"tool-call-mutation-{uuid.uuid4().hex}",
        "approvalId": review_id,
        "operationId": operation_id,
        "toolName": tool_name,
        "idempotencyKey": idempotency_key,
        "arguments": {
            "source": "unified_agent_graph",
            "reason": request.decisionPayload.get("comment") or "用户审批通过保存 Agent 草稿",
        },
    }


def mutation_tool_name(task_input: dict[str, Any]) -> str:
    """从输入提示中选择阶段 4 初版支持的变更工具。"""
    tool_hints = task_input.get("toolHints")
    if isinstance(tool_hints, list):
        for item in tool_hints:
            if str(item) in {
                "resume_revision_save",
                "jd_learning_plan_save",
                "agent_task_cancel_request",
                "agent_memory_candidate_save",
            }:
                return str(item)
    return "jd_learning_plan_save"


def mutation_idempotency_key(request: AgentTaskResumeRequest, tool_name: str) -> str:
    """生成稳定的保存类幂等键。"""
    return f"{tool_name}-{request.taskId}-v1"


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
