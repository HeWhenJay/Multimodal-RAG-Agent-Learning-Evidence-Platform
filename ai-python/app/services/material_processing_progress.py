"""将资料状态和细粒度日志聚合为稳定的前端处理进度快照。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any

from app.schemas.rag import MaterialProcessingPhase, MaterialProcessingProgress


@dataclass(frozen=True)
class ProcessingPhaseDefinition:
    """定义工作台稳定展示的标准处理阶段。"""

    code: str
    label: str


PROCESSING_PHASES = (
    ProcessingPhaseDefinition("UPLOAD", "接收与存储"),
    ProcessingPhaseDefinition("PARSE", "内容解析"),
    ProcessingPhaseDefinition("CHUNK", "结构切块"),
    ProcessingPhaseDefinition("EMBEDDING", "向量生成"),
    ProcessingPhaseDefinition("INDEX", "索引写入"),
    ProcessingPhaseDefinition("READY", "完成入库"),
)
PHASE_INDEX = {phase.code: index for index, phase in enumerate(PROCESSING_PHASES)}
PHASE_LABELS = {phase.code: phase.label for phase in PROCESSING_PHASES}
TERMINAL_STATUSES = {"READY", "PARTIAL", "FAILED"}
PROCESSING_STATUSES = {"PENDING", "PARSING", "REINDEXING", "UPLOADING", "PROCESSING", "RUNNING"}
STATUS_LABELS = {
    "PENDING": "等待处理",
    "PARSING": "解析中",
    "REINDEXING": "重建索引中",
    "UPLOADING": "上传中",
    "PROCESSING": "处理中",
    "RUNNING": "处理中",
    "READY": "已入库",
    "PARTIAL": "部分入库",
    "FAILED": "处理失败",
}
WINDOWS_PATH_PATTERN = re.compile(r"(?i)(?<![a-z0-9])(?:[a-z]:[\\/])[^\s,，;；<>\"']+")
PRIVATE_URI_PATTERN = re.compile(r"(?i)\b(?:file|oss)://[^\s,，;；<>\"']+")
PRIVATE_UNIX_PATH_PATTERN = re.compile(r"(?i)(?<!\w)/(?:home|users|tmp|private/tmp|var/tmp)/[^\s,，;；<>\"']+")


def build_material_processing_progress(
    *,
    material_status: str | None,
    events: Sequence[object],
    created_at: datetime | str | None,
    updated_at: datetime | str | None,
    failure_summary: str | None = None,
    now: datetime | None = None,
) -> MaterialProcessingProgress:
    """聚合原始事件，输出可直接驱动进度条和阶段列表的快照。"""
    status = normalize_status(material_status)
    ordered_events = list(events)
    latest_event = ordered_events[0] if ordered_events else None
    is_terminal = status in TERMINAL_STATUSES
    is_processing = status in PROCESSING_STATUSES
    current_phase_code = resolve_current_phase(status, latest_event)
    phase_events = latest_phase_events(ordered_events)
    phases = build_phases(status, current_phase_code, phase_events, is_processing)
    latest_status = event_status(latest_event)
    current_stage_code, current_stage_label = resolve_current_stage(status, latest_event, current_phase_code)
    failure_message = resolve_failure_message(status, latest_event, failure_summary)
    message = resolve_message(status, latest_event, failure_message)
    detail = public_progress_text(event_value(latest_event, "detail"), "") or None
    percent = resolve_percent(status, latest_event, current_phase_code, latest_status)
    started_at = resolve_started_at(status, created_at, updated_at, ordered_events)
    last_updated_at = resolve_last_updated_at(updated_at, ordered_events)
    elapsed_seconds = resolve_elapsed_seconds(
        started_at,
        last_updated_at,
        now=now,
        use_now=is_processing,
    )
    completed_phase_count = sum(phase.status == "COMPLETED" for phase in phases)
    return MaterialProcessingProgress(
        materialStatus=status,
        statusLabel=STATUS_LABELS.get(status, status or "状态未知"),
        isProcessing=is_processing,
        isTerminal=is_terminal,
        currentPhaseCode=current_phase_code,
        currentPhaseLabel=PHASE_LABELS[current_phase_code],
        currentStageCode=current_stage_code,
        currentStageLabel=current_stage_label,
        message=message,
        detail=detail,
        percent=percent,
        currentStep=safe_integer(event_value(latest_event, "currentStep")),
        totalSteps=safe_integer(event_value(latest_event, "totalSteps")),
        currentChunk=safe_integer(event_value(latest_event, "currentChunk")),
        totalChunks=safe_integer(event_value(latest_event, "totalChunks")),
        completedPhaseCount=completed_phase_count,
        totalPhaseCount=len(PROCESSING_PHASES),
        startedAt=datetime_text(started_at),
        lastUpdatedAt=datetime_text(last_updated_at),
        elapsedSeconds=elapsed_seconds,
        failureMessage=failure_message,
        nextAction=resolve_next_action(status, current_phase_code, latest_status),
        phases=phases,
    )


def resolve_current_phase(status: str, latest_event: object | None) -> str:
    """优先使用资料终态，再把最新细分事件映射到标准阶段。"""
    if status in {"READY", "PARTIAL"}:
        return "READY"
    event_phase = event_phase_code(latest_event)
    if event_phase:
        return event_phase
    if status in {"PARSING", "REINDEXING"}:
        return "PARSE"
    if status == "FAILED":
        return "INDEX"
    return "UPLOAD"


def resolve_current_stage(status: str, latest_event: object | None, phase_code: str) -> tuple[str, str]:
    """返回原始细分阶段；终态缺少完成事件时使用受控阶段补偿。"""
    if status in {"READY", "PARTIAL"}:
        return "index.completed", "索引完成"
    stage_code = text_value(event_value(latest_event, "stageCode"))
    stage_label = public_progress_text(event_value(latest_event, "stageLabel"), "")
    if stage_code:
        return stage_code, stage_label or PHASE_LABELS[phase_code]
    fallback_codes = {
        "UPLOAD": "upload.pending",
        "PARSE": "parse.pending",
        "CHUNK": "chunk.pending",
        "EMBEDDING": "embedding.pending",
        "INDEX": "index.pending",
        "READY": "index.completed",
    }
    return fallback_codes[phase_code], PHASE_LABELS[phase_code]


def latest_phase_events(events: Sequence[object]) -> dict[str, object]:
    """按接口返回的倒序事件列表保留每个标准阶段的最新事件。"""
    result: dict[str, object] = {}
    for event in events:
        phase_code = event_phase_code(event)
        if phase_code and phase_code not in result:
            result[phase_code] = event
    return result


def build_phases(
    material_status: str,
    current_phase_code: str,
    phase_events: Mapping[str, object],
    is_processing: bool,
) -> list[MaterialProcessingPhase]:
    """结合当前阶段推导各标准阶段的完成、运行和失败状态。"""
    current_index = PHASE_INDEX[current_phase_code]
    result: list[MaterialProcessingPhase] = []
    for index, definition in enumerate(PROCESSING_PHASES):
        event = phase_events.get(definition.code)
        status = phase_display_status(
            material_status=material_status,
            phase_index=index,
            current_index=current_index,
            event=event,
            is_processing=is_processing,
        )
        message = public_progress_text(event_value(event, "message"), "") or None
        if message is None and status == "COMPLETED":
            message = f"{definition.label}已完成"
        result.append(
            MaterialProcessingPhase(
                phaseCode=definition.code,
                phaseLabel=definition.label,
                status=status,
                message=message,
                updatedAt=datetime_text(event_datetime(event)),
            )
        )
    return result


def phase_display_status(
    *,
    material_status: str,
    phase_index: int,
    current_index: int,
    event: object | None,
    is_processing: bool,
) -> str:
    """计算单个标准阶段状态，资料主状态优先于子步骤偶发失败。"""
    if material_status in {"READY", "PARTIAL"}:
        return "COMPLETED"
    if phase_index < current_index:
        return "COMPLETED"
    if phase_index > current_index:
        return "PENDING"
    if material_status == "FAILED":
        return "FAILED"
    if not is_processing:
        return "PENDING"
    return "COMPLETED" if event_status(event) == "COMPLETED" else "RUNNING"


def event_phase_code(event: object | None) -> str | None:
    """把 Python 与历史 Java 的阶段码归并到六个标准阶段。"""
    stage_code = text_value(event_value(event, "stageCode")).lower()
    if not stage_code:
        return phase_from_step(event)
    if stage_code == "index.completed":
        return "READY"
    if stage_code == "index.request" or stage_code.startswith(("upload.", "storage.")):
        return "UPLOAD"
    if stage_code.startswith("parse."):
        return "PARSE"
    if stage_code.startswith(("sanitize.", "chunk.", "summary.")):
        return "CHUNK"
    if stage_code.startswith("embedding."):
        return "EMBEDDING"
    if stage_code == "index.failed" or stage_code.startswith(("vector.", "memory.", "index.")):
        return "INDEX"
    return phase_from_step(event)


def phase_from_step(event: object | None) -> str | None:
    """旧事件缺少标准阶段码时按八步索引流程补偿。"""
    step = safe_integer(event_value(event, "currentStep"))
    if step is None:
        return None
    if step <= 1:
        return "UPLOAD"
    if step <= 3:
        return "PARSE"
    if step <= 6:
        return "CHUNK"
    if step == 7:
        return "EMBEDDING"
    return "INDEX"


def resolve_message(status: str, latest_event: object | None, failure_message: str | None) -> str:
    """生成当前动作主文案，日志缺失时返回明确的降级说明。"""
    if status == "FAILED":
        return failure_message or "资料处理失败，请查看资料库中的失败记录"
    if status == "READY":
        return "资料已完成解析和索引，可以开始检索"
    if status == "PARTIAL":
        return "资料已部分入库，可以检索已成功识别的内容"
    event_message = public_progress_text(event_value(latest_event, "message"), "")
    if event_message:
        return event_message
    fallback = {
        "PENDING": "任务已创建，正在等待后台 worker 接收",
        "PARSING": "后台任务已启动，等待上报详细解析进度",
        "REINDEXING": "重建任务已启动，等待上报详细索引进度",
        "UPLOADING": "正在接收上传文件",
        "PROCESSING": "后台正在处理资料",
        "RUNNING": "后台正在处理资料",
    }
    return fallback.get(status, "等待后台上报详细进度")


def resolve_failure_message(status: str, latest_event: object | None, failure_summary: str | None) -> str | None:
    """失败原因优先使用资料受控摘要，并隐藏内部路径。"""
    if status != "FAILED":
        return None
    summary = public_progress_text(failure_summary, "")
    if summary:
        return summary
    if event_status(latest_event) == "FAILED":
        message = public_progress_text(event_value(latest_event, "message"), "")
        if message:
            return message
    return "后台处理失败，未返回可展示的详细原因"


def resolve_next_action(status: str, phase_code: str, latest_status: str) -> str:
    """告诉用户系统接下来会做什么，终态给出可执行建议。"""
    if status == "FAILED":
        return "可前往资料库检查资料并发起重建索引"
    if status == "PARTIAL":
        return "已识别内容可以检索；如需补全，可发起高精度重建"
    if status == "READY":
        return "资料已可用于 RAG 检索"
    if latest_status == "FAILED":
        return "当前子步骤异常，系统将继续重试或使用降级解析"
    actions = {
        "UPLOAD": "文件接收完成后将进入内容解析，无需保持页面开启",
        "PARSE": "解析完成后将按标题、段落和句子结构切块",
        "CHUNK": "切块完成后将生成可检索的向量表示",
        "EMBEDDING": "向量生成完成后将写入检索索引，无需保持页面开启",
        "INDEX": "索引写入完成后资料即可用于检索",
        "READY": "资料即将完成入库",
    }
    return actions[phase_code]


def resolve_percent(status: str, latest_event: object | None, phase_code: str, latest_status: str) -> int:
    """优先使用真实事件百分比，缺失时根据标准阶段提供稳定兜底。"""
    if status in {"READY", "PARTIAL"}:
        return 100
    event_percent = safe_integer(event_value(latest_event, "percent"))
    if event_percent is not None:
        return max(0, min(100, event_percent))
    phase_index = PHASE_INDEX[phase_code]
    phase_fraction = 1 if latest_status == "COMPLETED" else 0.25
    inferred = round((phase_index + phase_fraction) * 100 / len(PROCESSING_PHASES))
    return max(0, min(99, inferred))


def resolve_started_at(
    status: str,
    created_at: datetime | str | None,
    updated_at: datetime | str | None,
    events: Sequence[object],
) -> datetime | None:
    """普通入库从资料创建计时，重建索引从最近状态更新时间计时。"""
    if status == "REINDEXING":
        return parse_datetime(updated_at) or earliest_event_datetime(events) or parse_datetime(created_at)
    return parse_datetime(created_at) or earliest_event_datetime(events) or parse_datetime(updated_at)


def resolve_last_updated_at(updated_at: datetime | str | None, events: Sequence[object]) -> datetime | None:
    """选择资料更新时间和最近进度事件中的较新值。"""
    candidates = [value for value in (parse_datetime(updated_at), latest_event_datetime(events)) if value is not None]
    if not candidates:
        return None
    return max(candidates, key=datetime_epoch)


def resolve_elapsed_seconds(
    started_at: datetime | None,
    last_updated_at: datetime | None,
    *,
    now: datetime | None,
    use_now: bool,
) -> int:
    """运行中任务计算到当前时刻，终态计算到最后一次更新。"""
    if started_at is None:
        return 0
    end_at = (now or datetime.now().astimezone()) if use_now else last_updated_at
    if end_at is None:
        return 0
    return max(0, round(datetime_epoch(end_at) - datetime_epoch(started_at)))


def earliest_event_datetime(events: Sequence[object]) -> datetime | None:
    """读取当前窗口中最早的进度时间。"""
    values = [value for value in (event_datetime(event) for event in events) if value is not None]
    return min(values, key=datetime_epoch) if values else None


def latest_event_datetime(events: Sequence[object]) -> datetime | None:
    """读取当前窗口中最新的进度时间。"""
    values = [value for value in (event_datetime(event) for event in events) if value is not None]
    return max(values, key=datetime_epoch) if values else None


def event_datetime(event: object | None) -> datetime | None:
    """兼容 Pydantic 模型和字典事件的时间字段。"""
    return parse_datetime(event_value(event, "createdAt"))


def parse_datetime(value: datetime | str | None) -> datetime | None:
    """容错解析 ISO 时间，非法值不影响资料列表。"""
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def datetime_epoch(value: datetime) -> float:
    """为时区时间和本地无时区数据库时间生成可比较时间戳。"""
    if value.tzinfo is None:
        local_timezone = datetime.now().astimezone().tzinfo
        value = value.replace(tzinfo=local_timezone)
    return value.timestamp()


def datetime_text(value: datetime | None) -> str | None:
    """保持现有接口的 ISO 时间文本格式。"""
    return value.isoformat() if value is not None else None


def event_status(event: object | None) -> str:
    """将事件状态限制到公开进度枚举。"""
    status = normalize_status(text_value(event_value(event, "status")))
    return status if status in {"RUNNING", "COMPLETED", "FAILED"} else "RUNNING"


def event_value(event: object | None, name: str) -> Any:
    """兼容字典、Pydantic 模型和测试替身。"""
    if event is None:
        return None
    if isinstance(event, Mapping):
        return event.get(name)
    return getattr(event, name, None)


def normalize_status(value: str | None) -> str:
    """统一资料和事件状态大小写。"""
    return text_value(value).upper()


def safe_integer(value: object) -> int | None:
    """读取进度数字，拒绝布尔值和非法文本。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def text_value(value: object) -> str:
    """将可选值转换为去除首尾空白的文本。"""
    return "" if value is None else str(value).strip()


def public_progress_text(value: object, fallback: str) -> str:
    """隐藏本地路径和私有对象地址，避免诊断文本泄露运行环境。"""
    text = text_value(value)
    if not text:
        return fallback
    text = PRIVATE_URI_PATTERN.sub("[私有对象地址已隐藏]", text)
    text = WINDOWS_PATH_PATTERN.sub("[本地路径已隐藏]", text)
    text = PRIVATE_UNIX_PATH_PATTERN.sub("[本地路径已隐藏]", text)
    return text[:500] or fallback
