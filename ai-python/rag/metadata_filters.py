from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


BUSINESS_METADATA_FILTER_KEYS = {
    "documentId",
    "documentType",
    "source",
    "parser",
    "mediaType",
    "evidenceChannel",
    "blockType",
    "sectionName",
    "sectionKeyword",
    "pageIndex",
    "slideIndex",
}
SYSTEM_METADATA_FILTER_KEYS = {"userId", "visibilityScope"}
INTERNAL_IGNORED_KEYS_FIELD = "__ignoredMetadataFilterKeys"


@dataclass(frozen=True)
class MetadataFilterPlan:
    """保存查询时实际生效的业务过滤、系统过滤和忽略字段。"""

    business_filter: dict[str, Any] = field(default_factory=dict)
    system_filter: dict[str, Any] = field(default_factory=dict)
    ignored_keys: list[str] = field(default_factory=list)

    def effective_filter(self) -> dict[str, Any]:
        return {**self.business_filter, **self.system_filter}

    def diagnostics(self) -> dict[str, Any]:
        return {
            "effectiveMetadataFilter": dict(self.business_filter),
            "systemMetadataFilter": {"visibilityScope": self.system_filter.get("visibilityScope")}
            if self.system_filter.get("visibilityScope") is not None
            else {},
            "ignoredMetadataFilterKeys": list(dict.fromkeys(self.ignored_keys)),
        }


def build_metadata_filter_plan(metadata_filter: dict[str, Any] | None) -> MetadataFilterPlan:
    """清理查询过滤条件，只保留白名单字段并拆分业务过滤和系统过滤。"""
    business_filter: dict[str, Any] = {}
    system_filter: dict[str, Any] = {}
    ignored_keys: list[str] = []
    for key, value in (metadata_filter or {}).items():
        if key == INTERNAL_IGNORED_KEYS_FIELD:
            ignored_keys.extend(_string_items(value))
            continue
        if key in SYSTEM_METADATA_FILTER_KEYS:
            normalized = normalize_filter_value(key, value)
            if normalized is None:
                ignored_keys.append(key)
            else:
                system_filter[key] = normalized
            continue
        if key not in BUSINESS_METADATA_FILTER_KEYS:
            ignored_keys.append(key)
            continue
        normalized = normalize_filter_value(key, value)
        if normalized is None:
            ignored_keys.append(key)
            continue
        business_filter[key] = normalized
    return MetadataFilterPlan(
        business_filter=business_filter,
        system_filter=system_filter,
        ignored_keys=list(dict.fromkeys(ignored_keys)),
    )


def normalize_filter_value(key: str, value: Any) -> str | list[str] | None:
    """把过滤值规范为字符串或字符串数组，页码字段也按字符串比较。"""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (list, tuple, set)):
        values = [_normalize_single(item) for item in value]
        cleaned = [item for item in values if item]
        return cleaned or None
    return _normalize_single(value)


def matches_metadata_filter(metadata: dict[str, Any], plan: MetadataFilterPlan) -> bool:
    """判断内存模式 chunk metadata 是否符合系统和业务过滤。"""
    for key, value in plan.effective_filter().items():
        if key == "sectionKeyword":
            if not _matches_section_keyword(metadata, value):
                return False
            continue
        actual = metadata_value(metadata, key)
        if not _matches_exact(actual, value):
            return False
    return True


def metadata_value(metadata: dict[str, Any], key: str) -> Any:
    if key == "documentId":
        return metadata.get("documentId")
    if key == "sectionName":
        return metadata.get("sectionName") or metadata.get("sectionTitle")
    return metadata.get(key)


def format_metadata_filter_plan(plan: MetadataFilterPlan, *, total_count: int | None = None, filtered_count: int | None = None) -> str:
    """格式化过滤诊断，供 query.filter 进度详情展示。"""
    parts: list[str] = []
    if total_count is not None:
        parts.append(f"权限范围候选={total_count}")
    if filtered_count is not None:
        parts.append(f"业务过滤后={filtered_count}")
    parts.append("权限范围：个人私有资料")
    if plan.business_filter:
        business = "；".join(f"{key}={value}" for key, value in plan.business_filter.items())
        parts.append(f"业务过滤：{business}")
    else:
        parts.append("业务过滤：无")
    if plan.ignored_keys:
        parts.append(f"已忽略字段：{', '.join(plan.ignored_keys)}")
    return "；".join(parts)


def _matches_exact(actual: Any, expected: Any) -> bool:
    actual_text = "" if actual is None else str(actual)
    if isinstance(expected, list):
        return actual_text in {str(item) for item in expected}
    return actual_text == str(expected)


def _matches_section_keyword(metadata: dict[str, Any], expected: Any) -> bool:
    keywords = expected if isinstance(expected, list) else [expected]
    haystack = " ".join(
        str(item or "")
        for item in (
            metadata.get("sectionName"),
            metadata.get("sectionTitle"),
        )
    ).lower()
    return any(str(keyword).lower() in haystack for keyword in keywords if str(keyword).strip())


def _normalize_single(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_items(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [item for item in (_normalize_single(item) for item in value) if item]
    item = _normalize_single(value)
    return [item] if item else []
