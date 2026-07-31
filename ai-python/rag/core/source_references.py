from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit


def public_http_source(value: object) -> str | None:
    """只返回浏览器可访问的 HTTP(S) 来源，过滤本地路径和私有对象定位符。"""
    if value is None:
        return None
    source = str(value).strip()
    if not source:
        return None
    try:
        parsed = urlsplit(source)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return source


def evidence_source_label(source_path: object, source: object) -> str:
    """生成可公开展示的 evidence 来源，不回显服务端文件系统路径。"""
    public_source = public_http_source(source_path) or public_http_source(source)
    if public_source:
        return public_source
    source_text = str(source or "").strip()
    if source_text and not looks_like_internal_location(source_text):
        return source_text
    return "受控内部来源"


def sanitize_evidence_metadata(value: Any, key: str = "") -> Any:
    """递归移除 evidence metadata 中不可公开的路径和 URL 字段。"""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for item_key, item_value in value.items():
            normalized_key = str(item_key).replace("_", "").lower()
            if isinstance(item_value, str) and normalized_key.endswith(("path", "url")):
                public_value = public_http_source(item_value)
                if public_value:
                    result[str(item_key)] = public_value
                continue
            result[str(item_key)] = sanitize_evidence_metadata(item_value, str(item_key))
        return result
    if isinstance(value, list):
        return [sanitize_evidence_metadata(item, key) for item in value]
    if isinstance(value, tuple):
        return [sanitize_evidence_metadata(item, key) for item in value]
    return value


def looks_like_internal_location(value: str) -> bool:
    """识别本地绝对路径、file URI、私有 OSS URI 和 worker 临时文件。"""
    source = value.strip()
    lowered = source.lower()
    return (
        lowered.startswith(("file://", "oss://"))
        or lowered.startswith(("/", "\\\\"))
        or (len(source) >= 3 and source[1:3] in {":\\", ":/"} and source[0].isalpha())
        or "rag-oss-" in lowered
        or "rag-video-segments-" in lowered
    )
