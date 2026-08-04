"""Agent 上下文预算的 tokenizer 适配；基准最终数字仍以模型 usage 为准。"""

from __future__ import annotations

import json
from typing import Any


TOKENIZER_NAME = "cl100k_base"


def count_tokens(value: Any) -> int:
    """使用显式 tokenizer 计算预算 token，依赖缺失时保留保守字符回退。"""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    if not text:
        return 0
    try:
        import tiktoken

        return len(tiktoken.get_encoding(TOKENIZER_NAME).encode(text))
    except Exception:
        return max(1, len(text) // 2)


def tokenizer_metadata() -> dict[str, str]:
    """返回不会泄露输入正文的 tokenizer 口径。"""
    try:
        import tiktoken  # noqa: F401

        return {"tokenizer": TOKENIZER_NAME, "mode": "tiktoken"}
    except Exception:
        return {"tokenizer": "character_half", "mode": "fallback"}
