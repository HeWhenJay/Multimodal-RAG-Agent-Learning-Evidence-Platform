"""工作台和设置页的 Python 聚合业务服务。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import json
from typing import Any

from app.core.result import BusinessError
from app.page_data.repository import (
    PageDataRepository,
    PageDataRepositoryProtocol,
    PageDataTransaction,
    PageMaterialRecord,
    PageProgressRecord,
)
from app.schemas.page_data import (
    DashboardResponse,
    LearningMaterialPageResponse,
    RagProgressResponse,
    SystemSettingResponse,
)


class PageDataBusinessError(BusinessError):
    """页面数据领域可安全展示给前端的业务错误。"""


@dataclass(frozen=True)
class RecentTaskQuery:
    """归一化后的工作台近期资料筛选条件。"""

    start_date: date
    end_date: date
    limit: int


class PageDataService:
    """读取工作台聚合数据和系统设置，不依赖 Spring 服务。"""

    def __init__(
        self,
        repository: PageDataRepositoryProtocol | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository or PageDataRepository()
        self._clock = clock or (lambda: datetime.now().astimezone())

    def dashboard(
        self,
        user_id: str,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        recent_days: int | str | None = None,
        recent_limit: int | str | None = None,
    ) -> DashboardResponse:
        """按当前认证用户聚合资料、进度和全局错误统计。"""
        scoped_user_id = require_user_id(user_id)
        now = local_datetime(self._clock())
        recent_query = normalize_recent_task_query(now.date(), start_date, end_date, recent_days, recent_limit)
        recent_start_at = now - timedelta(days=7)
        error_start_at = now - timedelta(days=30)
        material_start_at = datetime.combine(recent_query.start_date, time.min)
        material_end_at = datetime.combine(recent_query.end_date + timedelta(days=1), time.min)
        with self._repository.transaction() as transaction:
            materials = transaction.list_materials_between(
                scoped_user_id,
                material_start_at,
                material_end_at,
                recent_query.limit,
            )
            return DashboardResponse(
                materialCount=transaction.material_count(scoped_user_id),
                materialDelta7Days=transaction.material_count_since(scoped_user_id, recent_start_at),
                evidenceCount=transaction.chunk_count(scoped_user_id),
                openErrorCount=transaction.count_open_errors_since(error_start_at),
                errorCount30Days=transaction.count_errors_since(error_start_at),
                recentTaskStartDate=recent_query.start_date.isoformat(),
                recentTaskEndDate=recent_query.end_date.isoformat(),
                recentTaskLimit=recent_query.limit,
                recentMaterials=[to_material_response(transaction, material) for material in materials],
            )

    def system_settings(self) -> list[SystemSettingResponse]:
        """按原 Java 排序结果返回系统设置展示项。"""
        with self._repository.transaction() as transaction:
            return [
                SystemSettingResponse(
                    key=record.key,
                    group=record.group,
                    label=record.label,
                    value=record.value,
                    sortOrder=record.sort_order,
                )
                for record in transaction.list_settings()
            ]


def require_user_id(value: str | None) -> str:
    """拒绝空用户标识，确保资料查询不会失去归属过滤。"""
    if value is None or not value.strip():
        raise PageDataBusinessError("登录状态已失效")
    return value.strip()


def normalize_recent_task_query(
    today: date,
    start_date: date | str | None,
    end_date: date | str | None,
    recent_days: int | str | None,
    recent_limit: int | str | None,
) -> RecentTaskQuery:
    """保持 Java 的日期截断、范围钳制和条数限制规则。"""
    safe_days = clamp(parse_optional_integer(recent_days, "近期天数"), default=7, minimum=1, maximum=7)
    safe_limit = clamp(parse_optional_integer(recent_limit, "近期资料条数"), default=5, minimum=1, maximum=50)
    earliest_date = today - timedelta(days=6)
    requested_end = parse_optional_date(end_date, "结束日期")
    requested_start = parse_optional_date(start_date, "开始日期")
    safe_end = clamp_date(requested_end or today, earliest_date, today)
    safe_start = clamp_date(requested_start, earliest_date, today) if requested_start else safe_end - timedelta(days=safe_days - 1)
    if safe_start > safe_end:
        safe_start = safe_end
    return RecentTaskQuery(start_date=safe_start, end_date=safe_end, limit=safe_limit)


def to_material_response(
    transaction: PageDataTransaction,
    material: PageMaterialRecord,
) -> LearningMaterialPageResponse:
    """将资料记录和合并去重后的 RAG 进度转换为前端响应。"""
    material_progress = progress_events(transaction, material.id)
    return LearningMaterialPageResponse(
        id=material.id,
        title=material.title,
        userId=material.user_id,
        documentType=material.document_type,
        source=material.source,
        status=material.status,
        parser=material.parser,
        documentSummary=material.document_summary,
        chunkCount=material.chunk_count,
        originalFilename=material.original_filename,
        originalFilePath=material.original_file_path,
        storageType=material.storage_type,
        objectKey=material.object_key,
        publicUrl=material.public_url,
        latestProgress=material_progress[0] if material_progress else None,
        progressEvents=material_progress,
        createdAt=local_datetime_or_none(material.created_at),
        updatedAt=local_datetime_or_none(material.updated_at),
    )


def progress_events(transaction: PageDataTransaction, material_id: int) -> list[RagProgressResponse]:
    """合并常规和视频关键进度，使用 Java 相同键值去重并保留最近 30 条。"""
    try:
        recent_progress = [to_progress_response(record) for record in transaction.list_progress(material_id, 40)]
        video_progress = [to_progress_response(record) for record in transaction.list_progress(material_id, 80, video_only=True)]
    except Exception:
        # 资料卡片不应因可观测性数据暂不可用而整体失败。
        return []
    seen: set[str] = set()
    result: list[RagProgressResponse] = []
    for progress in [*recent_progress, *video_progress]:
        key = progress_key(progress)
        if key in seen:
            continue
        seen.add(key)
        result.append(progress)
        if len(result) == 30:
            break
    return result


def to_progress_response(record: PageProgressRecord) -> RagProgressResponse:
    """读取脱敏 JSON 上下文，兼容旧日志字段补偿。"""
    context = parse_context(record.context_json)
    return RagProgressResponse(
        stageCode=text_or_none(context.get("stageCode")) or record.stage,
        stageLabel=text_or_none(context.get("stageLabel")),
        message=text_or_none(context.get("message")) or record.message or "",
        status=text_or_none(context.get("status")) or ("RUNNING" if record.success is True else "FAILED"),
        currentStep=integer_or_none(context.get("currentStep")),
        totalSteps=integer_or_none(context.get("totalSteps")),
        currentChunk=integer_or_none(context.get("currentChunk")),
        totalChunks=integer_or_none(context.get("totalChunks")),
        chunkId=text_or_none(context.get("chunkId")),
        blockId=text_or_none(context.get("blockId")),
        percent=integer_or_none(context.get("percent")),
        detail=text_or_none(context.get("detail")),
        createdAt=local_datetime_or_none(record.created_at),
    )


def progress_key(progress: RagProgressResponse) -> str:
    """生成与 Java `progressKey` 一致的去重键。"""
    return "|".join(
        (
            progress.stageCode or "",
            progress.message or "",
            progress.chunkId or "",
            str(progress.currentChunk),
            str(progress.totalChunks),
        )
    )


def parse_context(value: str | None) -> Mapping[str, Any]:
    """容错解析日志上下文，损坏历史日志按空对象处理。"""
    if value is None or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def text_or_none(value: object) -> str | None:
    """将非空上下文字段转换为文本。"""
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def integer_or_none(value: object) -> int | None:
    """读取 JSON 数字，布尔值和非法值不作为进度数字。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_optional_date(value: date | str | None, label: str) -> date | None:
    """解析 ISO 日期查询参数并返回中文业务错误。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and not value.strip():
        return None
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise PageDataBusinessError(f"{label}参数不合法") from None


def parse_optional_integer(value: int | str | None, label: str) -> int | None:
    """解析整数查询参数并保留 Java 风格业务信封。"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise PageDataBusinessError(f"{label}参数不合法")
    try:
        return int(value)
    except (TypeError, ValueError):
        raise PageDataBusinessError(f"{label}参数不合法") from None


def clamp(value: int | None, default: int, minimum: int, maximum: int) -> int:
    """将可选数字限制在闭区间内。"""
    if value is None:
        return default
    return max(minimum, min(value, maximum))


def clamp_date(value: date, minimum: date, maximum: date) -> date:
    """将日期限制在工作台可查询的最近七天范围。"""
    return max(minimum, min(value, maximum))


def local_datetime(value: datetime) -> datetime:
    """将时区时间转为 Java `LocalDateTime` 兼容的数据库时间。"""
    return value.replace(tzinfo=None)


def local_datetime_or_none(value: datetime | None) -> datetime | None:
    """把数据库时区时间转换为前端兼容的本地时间。"""
    return value.replace(tzinfo=None) if value is not None else None
