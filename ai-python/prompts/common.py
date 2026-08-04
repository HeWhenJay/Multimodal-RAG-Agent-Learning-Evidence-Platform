"""Prompt 模板共用的序列化工具。"""

from __future__ import annotations

import json
from typing import Any


def json_prompt(payload: dict[str, Any]) -> str:
    """把动态输入稳定序列化为中文 JSON，避免业务代码重复拼接 Prompt。"""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)
