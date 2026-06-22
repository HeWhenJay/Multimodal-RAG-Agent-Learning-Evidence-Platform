from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from app.schemas.resume_template import (
    ResumeContentPatch,
    ResumePatchEvidence,
    ResumePatchGenerationRequest,
    ResumePatchGenerationResponse,
    ResumeTemplateBinding,
)
from rag.resume_template.docx_patch import validate_resume_patches


SCHEMA_NAME = "resume_content_patch_response"


def resume_patch_json_schema() -> dict[str, Any]:
    """返回供 Structured Outputs 使用的严格 JSON Schema。"""
    patch_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "fieldId",
            "sourceTextHash",
            "newText",
            "rewriteReason",
            "evidenceIds",
            "confidence",
            "riskFlags",
            "status",
        ],
        "properties": {
            "fieldId": {"type": "string", "minLength": 1},
            "sourceTextHash": {"type": "string", "minLength": 16},
            "newText": {"type": "string", "maxLength": 2000},
            "rewriteReason": {"type": "string", "minLength": 1, "maxLength": 500},
            "evidenceIds": {"type": "array", "items": {"type": "string"}},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "riskFlags": {
                "type": "array",
                "items": {
                    "type": "string",
                    "enum": [
                        "NONE",
                        "MISSING_EVIDENCE",
                        "LOW_CONFIDENCE",
                        "OVER_LENGTH",
                        "LAYOUT_RISK",
                        "SENSITIVE_INFO",
                        "UNSUPPORTED_REGION",
                        "INJECTION_RISK",
                    ],
                },
            },
            "status": {
                "type": "string",
                "enum": ["DRAFT", "VALIDATED", "CONFIRMED", "REJECTED", "EXPORTED"],
            },
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["patches"],
        "properties": {
            "patches": {
                "type": "array",
                "items": patch_schema,
            }
        },
    }


def generate_resume_patches(request: ResumePatchGenerationRequest) -> ResumePatchGenerationResponse:
    """生成字段级内容补丁草稿，并在返回前做二次校验。"""
    schema = resume_patch_json_schema()
    provider = choose_provider(request.provider)
    raw_patches: list[ResumeContentPatch]
    validation_errors: list[str] = []
    if provider == "openai":
        try:
            raw_patches = generate_with_openai_structured_outputs(request, schema)
        except Exception as exc:
            validation_errors.append(f"OpenAI Structured Outputs 调用失败，已使用本地安全草稿: {exc}")
            provider = "local"
            raw_patches = generate_local_drafts(request.fields, request.evidenceCandidates)
    elif provider == "dashscope":
        try:
            raw_patches = generate_with_dashscope_json_mode(request)
        except Exception as exc:
            validation_errors.append(f"百炼兼容 JSON 生成失败，已使用本地安全草稿: {exc}")
            provider = "local"
            raw_patches = generate_local_drafts(request.fields, request.evidenceCandidates)
    else:
        raw_patches = generate_local_drafts(request.fields, request.evidenceCandidates)
    allowed_evidence_ids = [item.evidenceId for item in request.evidenceCandidates]
    validation = validate_resume_patches(
        template_id=request.templateId,
        version=request.version,
        fields=request.fields,
        patches=raw_patches,
        allowed_evidence_ids=allowed_evidence_ids,
    )
    return ResumePatchGenerationResponse(
        templateId=request.templateId,
        version=request.version,
        provider=provider,
        schemaName=SCHEMA_NAME,
        strictSchema=schema,
        patches=raw_patches,
        validationErrors=[*validation_errors, *validation.validationErrors],
    )


def choose_provider(provider: str) -> str:
    """选择补丁生成 provider，未配置 Key 时回退本地草稿。"""
    if provider == "openai":
        return "openai"
    if provider == "dashscope":
        return "dashscope"
    if provider == "local":
        return "local"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    if os.getenv("DASHSCOPE_API_KEY"):
        return "dashscope"
    return "local"


def generate_with_openai_structured_outputs(request: ResumePatchGenerationRequest, schema: dict[str, Any]) -> list[ResumeContentPatch]:
    """调用 OpenAI Structured Outputs，强制模型返回 JSON Schema 补丁。"""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_RESUME_PATCH_MODEL", os.getenv("OPENAI_MODEL", "gpt-4.1-mini")),
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是简历字段级内容补丁生成器。只输出字段内容补丁，不输出 DOCX、XML、样式、"
                    "字体、布局、路径或 locationRefs。所有修改必须可由 evidence 支撑。"
                ),
            },
            {"role": "user", "content": build_generation_prompt(request)},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": SCHEMA_NAME,
                "schema": schema,
                "strict": True,
            },
        },
    )
    content = response.choices[0].message.content or "{}"
    return parse_patch_payload(content)


def generate_with_dashscope_json_mode(request: ResumePatchGenerationRequest) -> list[ResumeContentPatch]:
    """调用百炼 OpenAI 兼容 JSON 输出，随后用本地 schema 校验兜底。"""
    client = OpenAI(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        base_url=os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )
    response = client.chat.completions.create(
        model=os.getenv("DASHSCOPE_RESUME_PATCH_MODEL", os.getenv("RAG_LLM_MODEL", "qwen-plus")),
        temperature=0.2,
        messages=[
            {"role": "system", "content": "你是简历字段级内容补丁生成器，只返回合法 JSON，不返回解释。"},
            {"role": "user", "content": build_generation_prompt(request)},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    return parse_patch_payload(content)


def parse_patch_payload(content: str) -> list[ResumeContentPatch]:
    """解析模型返回的补丁数组，并拒绝额外字段。"""
    payload = json.loads(content)
    raw_patches = payload.get("patches", payload if isinstance(payload, list) else [])
    if not isinstance(raw_patches, list):
        raise ValueError("模型输出缺少 patches 数组")
    patches: list[ResumeContentPatch] = []
    errors: list[str] = []
    for index, item in enumerate(raw_patches):
        try:
            patches.append(ResumeContentPatch.model_validate(item))
        except ValidationError as exc:
            errors.append(f"patch[{index}] 不符合 schema: {exc.errors()}")
    if errors:
        raise ValueError("；".join(errors))
    return patches


def generate_local_drafts(fields: list[ResumeTemplateBinding], evidence_candidates: list[ResumePatchEvidence]) -> list[ResumeContentPatch]:
    """生成不改写原文的安全草稿，供无模型环境和测试使用。"""
    evidence_ids = [item.evidenceId for item in evidence_candidates[:3]]
    patches: list[ResumeContentPatch] = []
    for field in fields:
        if field.unsupportedRegions:
            risk_flags = ["UNSUPPORTED_REGION"]
            evidence = []
        elif field.requiredEvidencePolicy == "REQUIRED" and not evidence_ids:
            risk_flags = ["MISSING_EVIDENCE", "LOW_CONFIDENCE"]
            evidence = []
        else:
            risk_flags = ["NONE"]
            evidence = evidence_ids if field.requiredEvidencePolicy != "NONE" else []
        patches.append(
            ResumeContentPatch(
                fieldId=field.fieldId,
                sourceTextHash=field.sourceTextHash,
                newText=field.sourceText,
                rewriteReason="本地安全草稿保留原文，等待用户确认可支撑的改写内容。",
                evidenceIds=evidence,
                confidence=0.5 if risk_flags != ["NONE"] else 0.85,
                riskFlags=risk_flags,
                status="DRAFT",
            )
        )
    return patches


def build_generation_prompt(request: ResumePatchGenerationRequest) -> str:
    """构造脱敏提示词，避免要求模型接触 DOCX 排版定位。"""
    fields = [
        {
            "fieldId": field.fieldId,
            "sectionKey": field.sectionKey,
            "displayName": field.displayName,
            "sourceText": field.sourceText,
            "sourceTextHash": field.sourceTextHash,
            "maxChars": field.maxChars,
            "maxLines": field.maxLines,
            "requiredEvidencePolicy": field.requiredEvidencePolicy,
            "unsupportedRegions": field.unsupportedRegions,
        }
        for field in request.fields
    ]
    evidences = [item.model_dump() for item in request.evidenceCandidates]
    return json.dumps(
        {
            "任务": "根据 JD 和 evidence 生成字段级内容补丁。禁止输出 style/font/layout/xml/path/locationRefs。",
            "岗位JD": request.jobDescription[:4000],
            "字段": fields,
            "可用证据": evidences,
        },
        ensure_ascii=False,
    )
