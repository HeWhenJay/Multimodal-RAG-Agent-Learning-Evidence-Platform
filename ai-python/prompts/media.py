"""音视频模型使用的 Prompt。"""

from __future__ import annotations

from typing import Any

from prompts.common import json_prompt


RECOGNITION_TEXT_CORRECTION_PROMPT_VERSION = "recognition-text-correction-v1"


DEFAULT_ASR_PROMPT = (
    "请将音频转写为 SRT 字幕格式，只输出字幕内容。"
    "每段必须包含序号、HH:MM:SS,mmm --> HH:MM:SS,mmm 时间范围和中文转写文本。"
)


def recognition_text_correction_system_prompt() -> str:
    """约束模型只纠正 ASR/OCR 识别错误，不改写原始证据。"""
    return (
        "你是中文 ASR/OCR 识别文本纠错器。请结合同一批次上下文，只纠正有把握的同音字、"
        "形近字、漏字、多字、明显断句和标点错误。不得总结、润色、扩写、删减事实或改变原意；"
        "数字、英文技术名词、代码标识、URL、公式、Markdown、表格和换行结构必须尽量保持原样。"
        "不确定时保留原文。必须按输入 blockId 原样返回严格 JSON："
        '{"items":[{"blockId":"原ID","correctedText":"纠正后文本"}]}。'
        "不要输出 JSON 之外的任何内容。"
    )


def recognition_text_correction_user_prompt(items: list[dict[str, Any]]) -> str:
    """构造不含业务密钥的批量识别文本纠错输入。"""
    return json_prompt(
        {
            "promptVersion": RECOGNITION_TEXT_CORRECTION_PROMPT_VERSION,
            "task": "纠正 ASR/OCR 识别文本中的明显错别字，保持事实和格式不变",
            "items": items,
        }
    )
