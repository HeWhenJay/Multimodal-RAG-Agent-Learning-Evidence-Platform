"""视频上传合并后的并发分段解析工具。"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import tempfile
from typing import Any

from app.schemas.rag import DocumentBlock
from rag.core.models import ParsedBlockDocument
from rag.loaders.document_parsers import DocumentParserRouter, mark_video_evidence_quality
from rag.loaders.parse_quality import QualitySignals, evaluate_parse_quality
from rag.observability.process_logger import process_event
from rag.observability.progress import RagProgressReporter
from video.chunking.video_processing import (
    TranscriptCue,
    cues_to_srt,
    ffmpeg_executable,
    ffmpeg_timeout_seconds,
    parse_srt_cues,
    probe_media_duration_strict,
    seconds_to_timestamp,
    timestamp_to_seconds,
)


DEFAULT_SEGMENT_TARGET_MIB = 20
DEFAULT_WORKER_COUNT = 2
MIN_SEGMENT_SECONDS = 15
MEANINGFUL_VIDEO_CHANNELS = {"subtitle", "frame_ocr"}
PARSER_NAME_MAX_LENGTH = 80


@dataclass(frozen=True)
class VideoSegmentTask:
    """描述 FFmpeg 生成的一个可独立解析媒体片段。"""

    index: int
    path: Path
    start_seconds: int
    end_seconds: int


@dataclass(frozen=True)
class SegmentParseResult:
    """保存单个 Worker 的片段解析结果。"""

    task: VideoSegmentTask
    parsed: ParsedBlockDocument


def parse_video_source_with_worker_pool(
    *,
    parser_router: DocumentParserRouter,
    document_id: str,
    title: str,
    document_type: str,
    source: str,
    user_id: str,
    visibility_scope: str,
    source_path: str,
    source_reference: str | None = None,
    filename: str,
    content_type: str | None,
    high_precision: bool,
    progress_reporter: RagProgressReporter | None = None,
) -> ParsedBlockDocument | None:
    """把合并后的视频切成媒体片段，并用后端 Worker 池并发解析后聚合 blocks。

    返回 None 表示 FFmpeg、文件大小、切片或解析结果不满足并发条件，调用方应回落
    到原有整视频解析路径，保证线上上传链路可用性优先。
    """

    if not read_bool("RAG_VIDEO_PARALLEL_SEGMENTS_ENABLED", True):
        return None
    original_path = Path(source_path).expanduser().resolve()
    evidence_source_path = source_reference or str(original_path)
    if not original_path.is_file():
        return None
    target_bytes = segment_target_bytes()
    worker_count = worker_count_from_env()
    if worker_count < 2:
        return None
    file_size = original_path.stat().st_size
    if file_size <= target_bytes:
        return None
    ffmpeg = ffmpeg_executable()
    duration = probe_media_duration_strict(original_path)
    if not ffmpeg or not duration:
        return None

    segment_seconds = choose_segment_seconds(file_size=file_size, duration_seconds=duration, target_bytes=target_bytes)
    if segment_seconds >= int(math.ceil(duration)):
        return None

    with tempfile.TemporaryDirectory(prefix="rag-video-segments-") as tmp:
        tasks, split_warning = split_video_for_parallel_parse(
            ffmpeg=ffmpeg,
            source_path=original_path,
            tmp_dir=Path(tmp),
            segment_seconds=segment_seconds,
            duration_seconds=duration,
        )
        if len(tasks) < 2:
            return None
        sidecar_warning = write_segment_sidecar_subtitles(original_path, tasks)

        active_workers = min(worker_count, len(tasks))
        target_mib = max(1, target_bytes // (1024 * 1024))
        progress_emit(
            progress_reporter,
            "parse.video",
            f"视频已按约 {target_mib}MiB 目标生成 {len(tasks)} 个媒体片段，启动 {active_workers} 个后端 Worker 并发处理",
            current_step=2,
            total_steps=8,
            total_chunks=len(tasks),
            percent=12,
        )
        process_event(
            stage="parse.video.parallel",
            action="video_parallel_segments_created",
            message="视频合并文件已切成可并发解析媒体片段",
            context={
                "documentId": document_id,
                "segmentCount": len(tasks),
                "workerCount": active_workers,
                "targetBytes": target_bytes,
                "segmentSeconds": segment_seconds,
            },
        )
        results, warnings = parse_segments_with_shared_queue(
            parser_router=parser_router,
            tasks=tasks,
            worker_count=active_workers,
            document_id=document_id,
            title=title,
            document_type=document_type,
            source=source,
            user_id=user_id,
            visibility_scope=visibility_scope,
            original_source_path=evidence_source_path,
            filename=filename,
            content_type=content_type,
            high_precision=high_precision,
            progress_reporter=progress_reporter,
        )
        if split_warning:
            warnings.insert(0, split_warning)
        if sidecar_warning:
            warnings.insert(0, sidecar_warning)
        if not results:
            return None

        parsed = combine_segment_results(
            parser_router=parser_router,
            document_id=document_id,
            title=title,
            original_source_path=evidence_source_path,
            results=results,
            warnings=warnings,
        )
        if not has_meaningful_video_blocks(parsed.blocks):
            return None
        progress_emit(
            progress_reporter,
            "parse.completed",
            f"视频分片并发解析完成：{len(results)}/{len(tasks)} 个片段已聚合",
            status="COMPLETED" if len(results) == len(tasks) else "RUNNING",
            current_step=5,
            total_steps=8,
            current_chunk=len(results),
            total_chunks=len(tasks),
            percent=55,
            parser=parsed.parser,
        )
        return parsed


def split_video_for_parallel_parse(
    *,
    ffmpeg: str,
    source_path: Path,
    tmp_dir: Path,
    segment_seconds: int,
    duration_seconds: float,
) -> tuple[list[VideoSegmentTask], str | None]:
    """使用 FFmpeg 将合并后原视频切成可独立解析的媒体片段。"""

    suffix = source_path.suffix if source_path.suffix else ".mp4"
    pattern = tmp_dir / f"segment-%04d{suffix}"
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0",
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        str(segment_seconds),
        "-reset_timestamps",
        "1",
        str(pattern),
    ]
    try:
        subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=ffmpeg_timeout_seconds(),
        )
    except Exception as exc:
        process_event(
            stage="parse.video.parallel",
            action="video_parallel_split_failed",
            message="FFmpeg 视频切片失败，回落整视频解析",
            context={"sourcePath": str(source_path), "errorType": exc.__class__.__name__},
        )
        return [], "video.parallel.split: FFmpeg 视频切片失败，已回落整视频解析"

    files = sorted(tmp_dir.glob(f"segment-*{suffix}"))
    tasks: list[VideoSegmentTask] = []
    for index, path in enumerate(files, start=1):
        if not path.is_file() or path.stat().st_size == 0:
            continue
        start = (index - 1) * segment_seconds
        end = min(int(math.ceil(duration_seconds)), index * segment_seconds)
        tasks.append(VideoSegmentTask(index=index, path=path, start_seconds=start, end_seconds=end))
    return tasks, None


def parse_segments_with_shared_queue(
    *,
    parser_router: DocumentParserRouter,
    tasks: list[VideoSegmentTask],
    worker_count: int,
    document_id: str,
    title: str,
    document_type: str,
    source: str,
    user_id: str,
    visibility_scope: str,
    original_source_path: str,
    filename: str,
    content_type: str | None,
    high_precision: bool,
    progress_reporter: RagProgressReporter | None,
) -> tuple[list[SegmentParseResult], list[str]]:
    """模拟 Kafka 后端 Worker 池：多个 Worker 从共享队列抢占片段任务。"""

    task_queue: queue.Queue[VideoSegmentTask | None] = queue.Queue()
    for task in tasks:
        task_queue.put(task)
    for _ in range(worker_count):
        task_queue.put(None)

    results: list[SegmentParseResult] = []
    warnings: list[str] = []
    lock = threading.Lock()

    def worker_loop(worker_no: int) -> None:
        """后端 Worker 循环从共享队列抢片段，完成后写回内存聚合区。"""

        while True:
            task = task_queue.get()
            try:
                if task is None:
                    return
                progress_emit(
                    progress_reporter,
                    "parse.video",
                    f"后端 Worker-{worker_no} 正在解析视频媒体片段 {task.index}/{len(tasks)}",
                    current_step=3,
                    total_steps=8,
                    current_chunk=task.index,
                    total_chunks=len(tasks),
                    percent=15 + round(35 * task.index / max(1, len(tasks))),
                )
                parsed = parser_router.parse_video_source(
                    document_id=f"{document_id}-media-segment-{task.index:04d}",
                    title=f"{title}（片段 {task.index}）",
                    document_type=document_type,
                    source=source,
                    user_id=user_id,
                    visibility_scope=visibility_scope,
                    source_path=str(task.path),
                    source_reference=original_source_path,
                    filename=segment_filename(filename, task.index),
                    content_type=content_type,
                    high_precision=high_precision,
                    progress_reporter=None,
                )
                adjusted = replace(
                    parsed,
                    blocks=[
                        adjust_segment_block(
                            block,
                            document_id=document_id,
                            title=title,
                            original_source_path=original_source_path,
                            task=task,
                        )
                        for block in parsed.blocks
                    ],
                )
                with lock:
                    results.append(SegmentParseResult(task=task, parsed=adjusted))
                progress_emit(
                    progress_reporter,
                    "parse.video",
                    f"后端 Worker-{worker_no} 已完成视频媒体片段 {task.index}/{len(tasks)}",
                    current_step=4,
                    total_steps=8,
                    current_chunk=task.index,
                    total_chunks=len(tasks),
                    percent=20 + round(35 * task.index / max(1, len(tasks))),
                    parser=parsed.parser,
                )
            except Exception as exc:
                with lock:
                    warnings.append(f"video.parallel.worker[{worker_no}].segment[{getattr(task, 'index', '?')}]: {exc.__class__.__name__}")
            finally:
                task_queue.task_done()

    workers = [
        threading.Thread(target=worker_loop, args=(index,), name=f"rag-video-worker-{index}", daemon=True)
        for index in range(1, worker_count + 1)
    ]
    for worker in workers:
        worker.start()
    task_queue.join()
    for worker in workers:
        worker.join(timeout=1)
    return sorted(results, key=lambda item: item.task.index), warnings


def write_segment_sidecar_subtitles(source_path: Path, tasks: list[VideoSegmentTask]) -> str | None:
    """将原视频同名侧车字幕按片段写到临时媒体旁，保留字幕优先解析路径。"""

    subtitle_path = next(
        (candidate for candidate in (source_path.with_suffix(".srt"), source_path.with_suffix(".vtt")) if candidate.is_file()),
        None,
    )
    if subtitle_path is None:
        return None
    try:
        raw_text = subtitle_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        raw_text = subtitle_path.read_text(encoding="gb18030", errors="replace")
    except OSError as exc:
        return f"video.parallel.subtitle: 无法读取侧车字幕，已继续原视频解析: {exc.__class__.__name__}"

    cues = parse_srt_cues(raw_text)
    if not cues:
        return "video.parallel.subtitle: 侧车字幕未解析到有效时间轴，已继续原视频解析"

    written = 0
    for task in tasks:
        segment_cues: list[TranscriptCue] = []
        for cue in cues:
            if cue.end_seconds <= task.start_seconds or cue.start_seconds >= task.end_seconds:
                continue
            start = max(0.0, cue.start_seconds - task.start_seconds)
            end = max(start + 0.05, min(float(task.end_seconds), cue.end_seconds) - task.start_seconds)
            segment_cues.append(
                TranscriptCue(
                    index=len(segment_cues) + 1,
                    start_seconds=start,
                    end_seconds=end,
                    text=cue.text,
                )
            )
        if not segment_cues:
            continue
        task.path.with_suffix(".srt").write_text(cues_to_srt(segment_cues), encoding="utf-8")
        written += 1
    process_event(
        stage="parse.video.parallel",
        action="video_parallel_sidecar_subtitles_created",
        message="视频侧车字幕已按媒体片段写入临时目录",
        context={"subtitlePath": str(subtitle_path), "segmentCount": len(tasks), "writtenSegments": written},
    )
    return None


def combine_segment_results(
    *,
    parser_router: DocumentParserRouter,
    document_id: str,
    title: str,
    original_source_path: str,
    results: list[SegmentParseResult],
    warnings: list[str],
) -> ParsedBlockDocument:
    """按原始时间轴聚合各片段 blocks，并复用解析器最终归一化与摘要索引。"""

    blocks: list[DocumentBlock] = []
    parsers: list[str] = []
    quality_messages: list[str] = []
    for result in results:
        blocks.extend(result.parsed.blocks)
        parsers.append(result.parsed.parser)
        quality_messages.extend(result.parsed.parse_quality.messages)
        quality_messages.extend(result.parsed.warnings)

    text_chars = sum(len(block.contentText) for block in blocks)
    quality = evaluate_parse_quality(
        QualitySignals(
            native_text_chars=text_chars,
            paragraph_count=sum(1 for block in blocks if block.blockType == "text"),
            image_count=sum(1 for block in blocks if block.blockType == "image"),
        ),
        high_precision=False,
    )
    if blocks and text_chars > 0:
        quality = mark_video_evidence_quality(quality, blocks)
    if quality_messages:
        quality = quality.model_copy(update={"messages": list(dict.fromkeys([*quality.messages, *quality_messages]))})
    parser = build_parallel_parser_name(parsers)
    finalizer = getattr(parser_router, "_finalize")
    return finalizer(document_id, blocks, parser, quality, list(dict.fromkeys(warnings)))


def adjust_segment_block(
    block: DocumentBlock,
    *,
    document_id: str,
    title: str,
    original_source_path: str,
    task: VideoSegmentTask,
) -> DocumentBlock:
    """把片段内相对时间戳恢复为原始视频时间轴，并统一 sourcePath。"""

    start_time = offset_timestamp(block.startTime, task.start_seconds) if block.startTime else None
    end_time = offset_timestamp(block.endTime, task.start_seconds) if block.endTime else None
    metadata = offset_metadata_times(block.metadata, task.start_seconds)
    metadata.update(
        {
            "videoMediaSegmentIndex": task.index,
            "videoMediaSegmentStartTime": seconds_to_timestamp(task.start_seconds),
            "videoMediaSegmentEndTime": seconds_to_timestamp(task.end_seconds),
            "videoMediaSegmentFilename": task.path.name,
            "sourcePath": original_source_path,
        }
    )
    section_title = block.sectionTitle
    if start_time and end_time:
        if block.metadata.get("evidenceChannel") == "video_segment_summary":
            section_title = f"视频片段摘要 {start_time} - {end_time}"
        else:
            section_title = f"{start_time} - {end_time}"
    elif start_time:
        section_title = start_time
    return block.model_copy(
        update={
            "documentId": document_id,
            "sourceTitle": title,
            "sourcePath": original_source_path,
            "startTime": start_time,
            "endTime": end_time,
            "sectionTitle": section_title,
            "metadata": metadata,
        }
    )


def offset_metadata_times(metadata: dict[str, Any], offset_seconds: int) -> dict[str, Any]:
    """调整 metadata 内常见视频时间字段，保持 evidence 引用可追踪。"""

    result = dict(metadata)
    for key in ("startTime", "endTime"):
        if result.get(key):
            result[key] = offset_timestamp(str(result[key]), offset_seconds)
    for key in ("frameTimeRanges", "visualTimeRanges"):
        ranges = result.get(key)
        if isinstance(ranges, list):
            result[key] = [
                {
                    **item,
                    "startTime": offset_timestamp(str(item.get("startTime") or item.get("start") or ""), offset_seconds),
                    "endTime": offset_timestamp(str(item.get("endTime") or item.get("end") or item.get("startTime") or ""), offset_seconds),
                }
                if isinstance(item, dict)
                else item
                for item in ranges
            ]
    for key in ("sourceFrameTimes", "visualSourceFrameTimes"):
        values = result.get(key)
        if isinstance(values, list):
            result[key] = [offset_timestamp(str(value), offset_seconds) for value in values]
    return result


def offset_timestamp(value: str, offset_seconds: int) -> str:
    """把片段内时间戳加上片段起点，统一输出为 HH:MM:SS。"""

    return seconds_to_timestamp(timestamp_to_seconds(value) + offset_seconds)


def has_meaningful_video_blocks(blocks: list[DocumentBlock]) -> bool:
    """判断并发解析是否得到真实可检索视频 evidence，避免把元数据 fallback 当成功。"""

    channels = {block.metadata.get("evidenceChannel") for block in blocks}
    if channels.intersection(MEANINGFUL_VIDEO_CHANNELS):
        return True
    return any(
        block.metadata.get("evidenceChannel") == "video_segment_summary"
        and (block.metadata.get("sourceBlockIds") or block.metadata.get("frameBlockIds"))
        for block in blocks
    )


def choose_segment_seconds(*, file_size: int, duration_seconds: float, target_bytes: int) -> int:
    """按文件大小和时长比例推算接近目标 MiB 的切片时长。"""

    raw_seconds = duration_seconds * target_bytes / max(1, file_size)
    return max(MIN_SEGMENT_SECONDS, int(math.ceil(raw_seconds)))


def segment_target_bytes() -> int:
    """读取媒体片段目标大小，默认与前端上传分片 20MiB 对齐。"""

    mib = positive_int("RAG_VIDEO_PARALLEL_SEGMENT_TARGET_MIB", DEFAULT_SEGMENT_TARGET_MIB)
    return max(1, mib) * 1024 * 1024


def worker_count_from_env() -> int:
    """读取后端片段 Worker 数量，默认 2 个 Worker 抢同一个队列。"""

    return positive_int("RAG_VIDEO_PARALLEL_WORKERS", DEFAULT_WORKER_COUNT)


def segment_filename(filename: str, index: int) -> str:
    """给解析器提供稳定片段文件名，避免不同片段日志难以区分。"""

    path = Path(filename)
    suffix = path.suffix or ".mp4"
    return f"{path.stem or 'video'}-segment-{index:04d}{suffix}"


def build_parallel_parser_name(parsers: list[str]) -> str:
    """生成受数据库字段长度约束的并发解析器名称。"""

    unique = [item for item in dict.fromkeys(parsers) if item]
    prefix = "video-parallel-worker-pool+"
    if not unique:
        return prefix + "video"

    candidate = prefix + "+".join(unique)
    if len(candidate) <= PARSER_NAME_MAX_LENGTH:
        return candidate

    omitted_count = max(0, len(unique) - 1)
    suffix = f"+{omitted_count}more" if omitted_count else ""
    first_parser_budget = max(1, PARSER_NAME_MAX_LENGTH - len(prefix) - len(suffix))
    return prefix + unique[0][:first_parser_budget] + suffix


def progress_emit(
    progress_reporter: RagProgressReporter | None,
    stage_code: str,
    message: str,
    **kwargs: Any,
) -> None:
    """进度上报失败不影响 Worker 解析，避免并发日志阻断索引。"""

    if progress_reporter is None:
        return
    try:
        progress_reporter.emit(stage_code, message, **kwargs)
    except Exception:
        return


def read_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量，支持本地测试快速开关。"""

    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def positive_int(name: str, default: int) -> int:
    """读取正整数环境变量，非法值回落默认配置。"""

    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default
