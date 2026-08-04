"""简历字段补丁生成 Prompt。"""

from __future__ import annotations

from typing import Any

from prompts.common import json_prompt


RESUME_PATCH_PROMPT_VERSION = "resume-patch-v1"


def resume_patch_system_prompt(*, strict: bool = False) -> str:
    """返回简历字段补丁模型的系统 Prompt。"""
    if strict:
        return (
            "你是简历字段级内容补丁生成器。只输出字段内容补丁，不输出 DOCX、XML、样式、"
            "字体、布局、路径或 locationRefs。所有修改必须基于用户简历摘要，并尽量由 evidence 支撑。"
            "只输出符合 JSON Schema 的结果。"
        )
    return "你是简历字段级内容补丁生成器，只返回合法 JSON，不返回解释。"


def resume_patch_user_prompt(
    *,
    job_description: str,
    resume_text: str,
    fields: list[dict[str, Any]],
    evidences: list[dict[str, Any]],
) -> str:
    """返回脱敏的简历字段补丁 user Prompt。"""
    return json_prompt(
        {
            "任务": "根据用户简历摘要、JD 和 evidence 生成字段级内容补丁。禁止输出 style/font/layout/xml/path/locationRefs。",
            "岗位JD": job_description[:4000],
            "用户已上传简历摘要": resume_text[:4000],
            "字段": fields,
            "可用证据": evidences,
        }
    )
