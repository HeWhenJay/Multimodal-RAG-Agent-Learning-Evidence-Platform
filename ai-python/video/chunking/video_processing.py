from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field, replace
from math import ceil
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from video.asr.bailian_asr import BailianAsrClient
from video.ocr.bailian_ocr import BailianOcrClient
from app.schemas.rag import DocumentBlock
from rag.observability.process_logger import logged_rag_method, process_event
from rag.observability.progress import RagProgressReporter


VIDEO_FILE_TYPES = {"mp4", "mov", "m4v", "webm", "mkv", "avi"}


@dataclass(frozen=True)
class FrameImage:
    time_seconds: int
    path: Path
    trigger: str = "interval"
    diff_score: float | None = None
    slide_index: int | None = None
    visual_decision: str | None = None
    visual_group_id: str | None = None
    suspected_visual_group_id: str | None = None
    visual_hash: str | None = None
    visual_hash_distance: int | None = None
    visual_time_ranges: list[dict[str, str]] = field(default_factory=list)
    visual_source_frame_times: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FrameCandidateEvent:
    frame: FrameImage
    ocr_candidate: bool = True
    priority: int = 0


@dataclass
class VisualFrameGroup:
    group_id: str
    hash_value: str
    representative_path: Path
    first_time: int
    last_seen_time: int
    last_ocr_candidate_time: int
    slide_index: int | None = None
    verification_count: int = 0
    visual_only_times: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class AudioSegment:
    path: Path
    nominal_start: float
    nominal_end: float
    extract_start: float
    extract_end: float


@dataclass(frozen=True)
class TranscriptCue:
    index: int
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class VideoProcessingResult:
    transcript_text: str = ""
    transcript_parser: str = "bailian-asr-transcript"
    frame_blocks: list[DocumentBlock] = field(default_factory=list)
    parser: str = "video-processor"
    warnings: list[str] = field(default_factory=list)


@logged_rag_method("parse.video", "process_video_bytes", "处理上传视频字节")
def process_video_bytes(
    *,
    content: bytes,
    filename: str,
    document_id: str,
    source_title: str,
    source_path: str | None,
    ocr_client: BailianOcrClient,
    progress_reporter: RagProgressReporter | None = None,
) -> VideoProcessingResult:
    """处理原始视频字节，先落临时文件再进入统一视频处理流程。"""
    suffix = Path(filename).suffix or ".mp4"
    with tempfile.TemporaryDirectory(prefix="rag-video-") as tmp:
        tmp_dir = Path(tmp)
        video_path = tmp_dir / f"input{suffix}"
        video_path.write_bytes(content)
        return process_video_input(
            video_input=str(video_path),
            filename=filename,
            document_id=document_id,
            source_title=source_title,
            source_path=source_path,
            ocr_client=ocr_client,
            progress_reporter=progress_reporter,
        )


@logged_rag_method("parse.video", "process_video_source", "处理已保存视频来源")
def process_video_source(
    *,
    source_path: str,
    filename: str,
    document_id: str,
    source_title: str,
    ocr_client: BailianOcrClient,
    progress_reporter: RagProgressReporter | None = None,
) -> VideoProcessingResult:
    """按本地路径或公开视频 URL 处理长视频，避免 Java 转发完整视频字节。"""
    process_event(
        stage="parse.video",
        action="process_video_source_route",
        message="已进入视频来源路径处理",
        context={"sourcePath": source_path, "filename": filename},
    )
    if is_public_url(source_path):
        return process_video_input(
            video_input=source_path,
            filename=filename,
            document_id=document_id,
            source_title=source_title,
            source_path=source_path,
            ocr_client=ocr_client,
            progress_reporter=progress_reporter,
        )
    if source_path.startswith("oss://"):
        warning = stage_warning("video.source", "当前 sourcePath 是 oss:// 私有地址，Python 无法直接读取，请配置公开 OSS/CDN URL")
        return VideoProcessingResult(
            frame_blocks=[fallback_video_metadata_block(document_id, filename, source_title, source_path)],
            parser="video-metadata-fallback",
            warnings=[warning, stage_warning("video.fallback", "视频未生成可检索字幕或关键帧 OCR 文本")],
        )
    local_path = resolve_local_video_path(source_path)
    if not local_path.exists() or not local_path.is_file():
        warning = stage_warning("video.source", f"视频来源文件不存在或不可读取: {source_path}")
        return VideoProcessingResult(
            frame_blocks=[fallback_video_metadata_block(document_id, filename, source_title, source_path)],
            parser="video-metadata-fallback",
            warnings=[warning, stage_warning("video.fallback", "视频未生成可检索字幕或关键帧 OCR 文本")],
        )
    return process_video_input(
        video_input=str(local_path),
        filename=filename,
        document_id=document_id,
        source_title=source_title,
        source_path=source_path,
        ocr_client=ocr_client,
        progress_reporter=progress_reporter,
    )


@logged_rag_method("parse.video", "process_video_input", "统一处理视频输入")
def process_video_input(
    *,
    video_input: str,
    filename: str,
    document_id: str,
    source_title: str,
    source_path: str | None,
    ocr_client: BailianOcrClient,
    progress_reporter: RagProgressReporter | None = None,
) -> VideoProcessingResult:
    """统一处理本地文件或 URL 视频输入，生成字幕、关键帧 OCR 和视频元数据。"""
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="rag-video-work-") as tmp:
        tmp_dir = Path(tmp)
        transcript_text, transcript_parser, asr_warnings = transcribe_video_input(
            video_input,
            tmp_dir,
            source_path,
            progress_reporter=progress_reporter,
        )
        warnings.extend(asr_warnings)

        frames, frame_warnings = extract_keyframes(video_input, tmp_dir, progress_reporter=progress_reporter)
        warnings.extend(frame_warnings)
        frame_blocks, ocr_warnings = ocr_video_frames(
            frames=frames,
            document_id=document_id,
            file_type=normalize_video_file_type(filename),
            source_title=source_title,
            source_path=source_path,
            ocr_client=ocr_client,
            progress_reporter=progress_reporter,
        )
        warnings.extend(ocr_warnings)

    if transcript_text and source_path and is_public_url(source_path):
        transcript_text = prepend_video_url_header(transcript_text, source_path)

    if not transcript_text and not frame_blocks:
        warnings.append(stage_warning("video.fallback", "视频未生成可检索字幕或关键帧 OCR 文本"))
        frame_blocks = [fallback_video_metadata_block(document_id, filename, source_title, source_path)]

    parser_parts = ["video"]
    if transcript_text:
        if transcript_parser.startswith("sidecar-subtitle"):
            parser_parts.append("subtitle")
        elif transcript_parser.startswith("embedded-subtitle"):
            parser_parts.append("embedded-subtitle")
        elif transcript_parser.startswith("estimated-srt"):
            parser_parts.append("estimated-srt")
        else:
            parser_parts.append("bailian-asr")
    if any(block.metadata.get("evidenceChannel") == "frame_ocr" for block in frame_blocks):
        parser_parts.append("keyframe-ocr")
    if any(
        block.metadata.get("evidenceChannel") == "frame_ocr" and block.metadata.get("frameTrigger") == "ppt_flip"
        for block in frame_blocks
    ):
        parser_parts.append("ppt-flip-detect")
    return VideoProcessingResult(
        transcript_text=transcript_text,
        transcript_parser=transcript_parser,
        frame_blocks=frame_blocks,
        parser="+".join(parser_parts),
        warnings=warnings,
    )


@logged_rag_method("parse.video.asr", "transcribe_video_input", "执行视频 ASR 转写")
def transcribe_video_input(
    video_input: str,
    tmp_dir: Path,
    source_path: str | None,
    *,
    progress_reporter: RagProgressReporter | None = None,
) -> tuple[str, str | None, list[str]]:
    """对视频输入执行转写；优先字幕，缺字幕时再按来源选择 filetrans 或分段 ASR。"""
    client = BailianAsrClient()
    warnings: list[str] = []

    subtitle_text, subtitle_source, subtitle_warnings = load_sidecar_subtitle(video_input, source_path)
    warnings.extend(subtitle_warnings)
    if subtitle_text:
        return subtitle_text, "sidecar-subtitle-transcript", warnings

    embedded_checked = False
    if should_probe_embedded_subtitle_before_asr(video_input, source_path):
        embedded_checked = True
        embedded_text, embedded_warnings = extract_embedded_subtitle(video_input, tmp_dir)
        warnings.extend(embedded_warnings)
        if embedded_text:
            return embedded_text, "embedded-subtitle-transcript", warnings

    if is_public_url(source_path):
        if client.should_call_dashscope and client.api_key and client.should_call_filetrans(source_path):
            emit_model_progress(
                progress_reporter,
                f"目前在使用 {client.filetrans_model} 模型完成视频异步 ASR 转写事件",
                percent=16,
                detail=f"目前在使用 {client.filetrans_model} 模型完成视频异步 ASR 转写事件",
            )
        transcript_text, filetrans_warnings = client.transcribe_source_url(
            str(source_path),
            progress_callback=lambda event: emit_filetrans_progress(progress_reporter, client, event),
        )
        warnings.extend(stage_warning("video.asr", warning) for warning in filetrans_warnings)
        if transcript_text:
            if client.should_call_dashscope and client.api_key and client.should_call_filetrans(source_path):
                emit_model_progress(
                    progress_reporter,
                    f"已使用 {client.filetrans_model} 模型完成视频异步 ASR 转写事件",
                    percent=18,
                    detail=f"已使用 {client.filetrans_model} 模型完成视频异步 ASR 转写事件",
            )
            return transcript_text, "bailian-asr-transcript", warnings

    if not embedded_checked:
        embedded_text, embedded_warnings = extract_embedded_subtitle(video_input, tmp_dir)
        warnings.extend(embedded_warnings)
        if embedded_text:
            return embedded_text, "embedded-subtitle-transcript", warnings

    segments, segment_warnings = extract_audio_segments(video_input, tmp_dir)
    warnings.extend(segment_warnings)
    if not segments:
        return "", None, warnings

    cues: list[TranscriptCue] = []
    plain_parts: list[str] = []
    for segment_index, segment in enumerate(segments, start=1):
        if client.should_call_dashscope and client.api_key:
            emit_model_progress(
                progress_reporter,
                f"第 {segment_index}/{len(segments)} 段：目前在使用 {client.model} 模型完成视频音频同步 ASR 转写事件",
                percent=16,
                detail=f"目前在使用 {client.model} 模型完成视频音频同步 ASR 转写事件",
            )
        segment_text, asr_warnings = client.transcribe_audio_file(segment.path)
        warnings.extend(stage_warning(f"video.asr.segment[{segment_index}]", warning) for warning in asr_warnings)
        if not segment_text:
            continue
        if client.should_call_dashscope and client.api_key:
            emit_model_progress(
                progress_reporter,
                f"第 {segment_index}/{len(segments)} 段：已使用 {client.model} 模型完成视频音频同步 ASR 转写事件",
                percent=18,
                detail=f"已使用 {client.model} 模型完成视频音频同步 ASR 转写事件",
            )
        if transcript_has_timestamps(segment_text):
            segment_cues = parse_srt_cues(offset_srt_transcript(segment_text, segment.extract_start))
        else:
            estimated = estimate_srt_from_transcript(segment_text, segment.extract_end - segment.extract_start)
            segment_cues = parse_srt_cues(offset_srt_transcript(estimated, segment.extract_start))
            plain_parts.append(segment_text)
            warnings.append(stage_warning(
                f"video.asr.segment[{segment_index}].timestamp",
                "百炼同步 ASR 未返回时间戳，已按分段时长生成估算字幕时间段",
            ))
        cues.extend(cue for cue in segment_cues if cue_center_in_segment(cue, segment))

    merged = merge_transcript_cues(cues, overlap_seconds=audio_overlap_seconds())
    if merged:
        return cues_to_srt(merged), "bailian-asr-transcript", warnings
    if plain_parts:
        duration = probe_media_duration(video_input)
        return estimate_srt_from_transcript(" ".join(plain_parts), duration), "estimated-srt-transcript", warnings
    return "", None, warnings


@logged_rag_method("parse.video.subtitle", "load_sidecar_subtitle", "加载视频同目录字幕")
def load_sidecar_subtitle(video_input: str, source_path: str | None) -> tuple[str, str | None, list[str]]:
    """优先读取同名 .srt/.vtt/.txt 侧车字幕，作为无 FFmpeg 时的视频时间戳证据来源。"""
    warnings: list[str] = []
    candidates = sidecar_subtitle_candidates(video_input, source_path)
    for candidate in candidates:
        if not candidate.exists() or not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = candidate.read_text(encoding="gb18030", errors="ignore")
        normalized = normalize_text(text.replace("\ufeff", ""))
        if not normalized:
            continue
        if candidate.suffix.lower() not in {".srt", ".vtt", ".txt"}:
            continue
        if candidate.suffix.lower() == ".vtt" and not normalized.lstrip().startswith("webvtt"):
            normalized = f"WEBVTT\n\n{normalized}"
        if candidate.suffix.lower() == ".txt" and not transcript_has_timestamps(normalized):
            continue
        process_event(
            stage="parse.video.subtitle",
            action="load_sidecar_subtitle_found",
            message=f"已加载侧车字幕: {candidate.name}",
            context={"subtitlePath": str(candidate), "subtitleType": candidate.suffix.lower().lstrip(".")},
        )
        return normalized, str(candidate), warnings
    return "", None, warnings


def sidecar_subtitle_candidates(video_input: str, source_path: str | None) -> list[Path]:
    """生成视频侧车字幕候选路径，兼容同目录同名和扩展名替换。"""
    candidates: list[Path] = []
    if source_path:
        base = Path(source_path)
        for stem in subtitle_stems(base.stem):
            candidates.append(base.with_name(f"{stem}.srt"))
            candidates.append(base.with_name(f"{stem}.vtt"))
            candidates.append(base.with_name(f"{stem}.txt"))
    input_path = Path(video_input)
    for stem in subtitle_stems(input_path.stem):
        candidates.append(input_path.with_name(f"{stem}.srt"))
        candidates.append(input_path.with_name(f"{stem}.vtt"))
        candidates.append(input_path.with_name(f"{stem}.txt"))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        resolved = str(candidate)
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(candidate)
    return unique


def subtitle_stems(stem: str) -> list[str]:
    """生成视频同名字幕候选 stem，兼容 *.subtitled.mp4 -> *.srt 这类命名。"""
    stems = [stem]
    stripped = stem
    for suffix in (".subtitled", ".subtitle", ".withsubtitles", "_subtitled", "_subtitle"):
        if stripped.endswith(suffix):
            stripped = stripped[: -len(suffix)]
            stems.append(stripped)
    if stripped and stripped not in stems:
        stems.append(stripped)
    return list(dict.fromkeys(stems))


def should_probe_embedded_subtitle_before_asr(video_input: str, source_path: str | None) -> bool:
    """判断是否应在 ASR 前抽取内嵌字幕，避免普通公开视频先被远程 FFmpeg 探测拖慢。"""
    if not is_public_url(video_input):
        return True
    candidates = [video_input, source_path or ""]
    subtitle_markers = (".subtitled", ".subtitle", "_subtitled", "_subtitle", "withsubtitles")
    for value in candidates:
        if not value:
            continue
        parsed_path = unquote(urlparse(value).path if is_public_url(value) else value).lower()
        stem = Path(parsed_path).stem
        if any(marker in stem for marker in subtitle_markers):
            return True
    return False


@logged_rag_method("parse.video.subtitle", "extract_embedded_subtitle", "抽取视频内嵌字幕")
def extract_embedded_subtitle(video_input: str, tmp_dir: Path) -> tuple[str, list[str]]:
    """尝试用 FFmpeg 抽取第一个内嵌字幕轨，适配 *.subtitled.mp4。"""
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return "", [stage_warning("video.subtitle.embedded", "未找到 FFmpeg，跳过视频内嵌字幕提取")]
    subtitle_path = tmp_dir / "embedded-subtitle.srt"
    command = [
        ffmpeg,
        "-y",
        "-i",
        video_input,
        "-map",
        "0:s:0",
        str(subtitle_path),
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
        return "", [stage_warning("video.subtitle.embedded", f"FFmpeg 未提取到内嵌字幕: {exc}")]
    if not subtitle_path.exists() or subtitle_path.stat().st_size == 0:
        return "", [stage_warning("video.subtitle.embedded", "FFmpeg 未生成可用内嵌字幕文件")]
    try:
        text = subtitle_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = subtitle_path.read_text(encoding="gb18030", errors="ignore")
    normalized = normalize_text(text.replace("\ufeff", ""))
    if not normalized:
        return "", [stage_warning("video.subtitle.embedded", "内嵌字幕文件为空")]
    process_event(
        stage="parse.video.subtitle",
        action="extract_embedded_subtitle_found",
        message="已提取视频内嵌字幕",
        context={"subtitlePath": str(subtitle_path), "subtitleChars": len(normalized)},
    )
    return normalized, []


@logged_rag_method("parse.video.audio", "extract_audio_segments", "抽取视频音频分段")
def extract_audio_segments(video_input: str, tmp_dir: Path) -> tuple[list[AudioSegment], list[str]]:
    """按固定窗口抽取音频段，边界保留重叠，避免切断关键连续表达。"""
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return [], [stage_warning("video.audio.extract", "未找到 FFmpeg，跳过视频音频轨提取")]
    duration = probe_media_duration(video_input)
    segment_seconds = max(30, int(os.getenv("RAG_VIDEO_AUDIO_SEGMENT_SECONDS", "300")))
    overlap = audio_overlap_seconds()
    segment_dir = tmp_dir / "audio-segments"
    segment_dir.mkdir(parents=True, exist_ok=True)
    segments: list[AudioSegment] = []
    warnings: list[str] = []
    start = 0.0
    index = 1
    while start < duration:
        nominal_end = min(duration, start + segment_seconds)
        extract_start = max(0.0, start - overlap)
        extract_end = min(duration, nominal_end + overlap)
        audio_path = segment_dir / f"audio-{index:04d}.wav"
        command = [
            ffmpeg,
            "-y",
            "-ss",
            str(round(extract_start, 3)),
            "-i",
            video_input,
            "-t",
            str(round(max(1.0, extract_end - extract_start), 3)),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            str(audio_path),
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
            warnings.append(stage_warning("video.audio.extract", f"FFmpeg 提取音频分段 {index} 失败: {exc}"))
        if audio_path.exists() and audio_path.stat().st_size > 0:
            segments.append(AudioSegment(audio_path, start, nominal_end, extract_start, extract_end))
        else:
            warnings.append(stage_warning("video.audio.extract", f"FFmpeg 未生成可用音频分段 {index}"))
        start = nominal_end
        index += 1
    if not segments:
        warnings.append(stage_warning("video.audio.extract", "FFmpeg 未生成任何可用音频分段"))
    return segments, warnings


@logged_rag_method("parse.video.frame", "extract_keyframes", "抽取视频关键帧")
def extract_keyframes(
    video_input: str,
    tmp_dir: Path,
    *,
    progress_reporter: RagProgressReporter | None = None,
) -> tuple[list[FrameImage], list[str]]:
    """按 V6 策略抽取全时长候选帧，再执行两阶段 OCR 帧选择。"""
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return [], [stage_warning("video.frame.extract", "未找到 FFmpeg，跳过视频关键帧抽取")]
    sample_interval = max(1, int(os.getenv("RAG_VIDEO_FRAME_SAMPLE_INTERVAL_SECONDS", "5")))
    keep_interval = max(sample_interval, int(os.getenv("RAG_VIDEO_FRAME_INTERVAL_SECONDS", "30")))
    max_frames = video_ocr_frame_limit()
    max_candidates = int(os.getenv("RAG_VIDEO_FRAME_MAX_CANDIDATES", "720"))
    if max_frames is not None:
        max_candidates = max(max_frames, max_candidates)
    scan_mode = video_frame_scan_mode()
    max_frames_label = format_ocr_frame_limit(max_frames)
    warnings: list[str] = []
    emit_video_progress(
        progress_reporter,
        "parse.video.frame.extract",
        f"正在按 {sample_interval}s 间隔扫描视频候选帧，{max_frames_label}",
        percent=18,
        detail=(
            f"scanMode={scan_mode}; sampleIntervalSeconds={sample_interval}; "
            f"maxCandidates={max_candidates}; maxOcrFrames={max_frames_label}; "
            f"keepIntervalSeconds={keep_interval}; minIntervalSeconds={video_frame_min_interval_seconds()}"
        ),
    )

    candidates: list[FrameImage]
    if scan_mode == "prefix":
        candidates, prefix_warnings = extract_prefix_frame_candidates(
            video_input,
            tmp_dir,
            ffmpeg=ffmpeg,
            sample_interval=sample_interval,
            max_candidates=max_candidates,
        )
        warnings.extend(prefix_warnings)
    elif scan_mode == "full":
        candidates, full_warnings = extract_full_frame_candidates(
            video_input,
            tmp_dir,
            ffmpeg=ffmpeg,
            sample_interval=sample_interval,
            max_candidates=max_candidates,
        )
        warnings.extend(full_warnings)
    else:
        candidates, full_warnings = extract_full_frame_candidates(
            video_input,
            tmp_dir,
            ffmpeg=ffmpeg,
            sample_interval=sample_interval,
            max_candidates=max_candidates,
        )
        warnings.extend(full_warnings)
        if not candidates:
            warnings.append(stage_warning("video.frame.extract", "auto 全时长抽帧失败，已降级为 prefix 开头扫描"))
            candidates, prefix_warnings = extract_prefix_frame_candidates(
                video_input,
                tmp_dir,
                ffmpeg=ffmpeg,
                sample_interval=sample_interval,
                max_candidates=max_candidates,
            )
            warnings.extend(prefix_warnings)

    if not candidates:
        return [], warnings or [stage_warning("video.frame.extract", "FFmpeg 未生成关键帧图片")]
    emit_video_progress(
        progress_reporter,
        "parse.video.frame.candidates",
        f"已抽取 {len(candidates)} 个全视频候选帧，准备执行 PPT 翻页检测和视觉去重",
        percent=18,
        detail=(
            f"candidateCount={len(candidates)}; maxOcrFrames={max_frames_label}; "
            f"scanMode={scan_mode}; visualDedupEnabled={visual_dedup_enabled()}; "
            f"keepIntervalSeconds={keep_interval}; minIntervalSeconds={video_frame_min_interval_seconds()}"
        ),
    )
    if progress_reporter is None:
        selected, selection_warnings = select_ppt_slide_frames(
            candidates,
            keep_interval_seconds=keep_interval,
            max_frames=max_frames,
        )
    else:
        selected, selection_warnings = select_ppt_slide_frames(
            candidates,
            keep_interval_seconds=keep_interval,
            max_frames=max_frames,
            progress_reporter=progress_reporter,
        )
    warnings.extend(selection_warnings)
    return selected, warnings


def extract_prefix_frame_candidates(
    video_input: str,
    tmp_dir: Path,
    *,
    ffmpeg: str,
    sample_interval: int,
    max_candidates: int,
) -> tuple[list[FrameImage], list[str]]:
    """保留旧版开头扫描行为，按固定采样间隔最多抽取 max_candidates 帧。"""
    frame_dir = tmp_dir / "frames-prefix"
    reset_directory(frame_dir)
    frame_pattern = frame_dir / "frame-%04d.jpg"
    command = [
        ffmpeg,
        "-y",
        "-i",
        video_input,
        "-vf",
        f"fps=1/{sample_interval}",
        "-frames:v",
        str(max_candidates),
        str(frame_pattern),
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
        return [], [stage_warning("video.frame.extract", f"FFmpeg 抽取关键帧失败: {exc}")]
    return frame_candidates_from_directory(frame_dir, sample_interval), []


def extract_full_frame_candidates(
    video_input: str,
    tmp_dir: Path,
    *,
    ffmpeg: str,
    sample_interval: int,
    max_candidates: int,
) -> tuple[list[FrameImage], list[str]]:
    """按视频总时长动态放大采样间隔，避免长视频只覆盖开头几分钟。"""
    warnings: list[str] = []
    duration = probe_media_duration_strict(video_input)
    if duration is None:
        return [], [stage_warning("video.frame.extract", "无法严格探测视频时长，full 抽帧不可用")]
    target_candidates = max(1, int(os.getenv("RAG_VIDEO_FRAME_TARGET_CANDIDATES", "360")))
    effective_interval = max(sample_interval, ceil(duration / target_candidates))
    estimated_candidates = ceil(duration / effective_interval)
    if estimated_candidates > max_candidates:
        effective_interval = max(effective_interval, ceil(duration / max_candidates))
    frame_dir = tmp_dir / "frames-full"
    reset_directory(frame_dir)
    frame_pattern = frame_dir / "frame-%04d.jpg"
    command = [
        ffmpeg,
        "-y",
        "-i",
        video_input,
        "-vf",
        f"fps=1/{effective_interval}",
        "-frames:v",
        str(max_candidates),
        str(frame_pattern),
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
        reset_directory(frame_dir)
        return [], [stage_warning("video.frame.extract", f"FFmpeg 全时长抽取关键帧失败: {exc}")]
    candidates = frame_candidates_from_directory(frame_dir, effective_interval)
    if not candidates:
        warnings.append(stage_warning("video.frame.extract", "FFmpeg 全时长模式未生成关键帧图片"))
    else:
        process_event(
            stage="parse.video.frame",
            action="extract_full_frame_candidates_completed",
            message="已完成视频全时长候选帧抽取",
            context={
                "durationSeconds": round(duration, 3),
                "effectiveIntervalSeconds": effective_interval,
                "candidateCount": len(candidates),
                "maxCandidates": max_candidates,
            },
        )
    return candidates, warnings


def frame_candidates_from_directory(frame_dir: Path, interval_seconds: int) -> list[FrameImage]:
    """根据抽帧目录和有效间隔生成全局时间递增的候选帧。"""
    candidates: list[FrameImage] = []
    for index, path in enumerate(sorted(frame_dir.glob("frame-*.jpg"))):
        candidates.append(FrameImage(time_seconds=max(0, index * interval_seconds), path=path, trigger="candidate"))
    return candidates


def reset_directory(path: Path) -> None:
    """重建抽帧目录，避免降级时残留图片污染时间轴。"""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


@logged_rag_method("parse.video.frame", "select_ppt_slide_frames", "识别 PPT 翻页关键帧")
def select_ppt_slide_frames(
    candidates: list[FrameImage],
    *,
    keep_interval_seconds: int,
    max_frames: int | None,
    progress_reporter: RagProgressReporter | None = None,
) -> tuple[list[FrameImage], list[str]]:
    """先扫描全部候选帧生成事件，再按全时长时间桶选择 OCR 帧。"""
    if not candidates:
        return [], []
    sorted_candidates = sorted(candidates, key=lambda item: item.time_seconds)
    dedup_enabled = visual_dedup_enabled()
    max_frames_label = format_ocr_frame_limit(max_frames)
    emit_video_progress(
        progress_reporter,
        "parse.video.slide_detect",
        f"正在检测 PPT 翻页和视觉重复：候选帧 {len(sorted_candidates)} 个，{max_frames_label}",
        percent=19,
        detail=(
            f"candidateCount={len(sorted_candidates)}; maxOcrFrames={max_frames_label}; "
            f"visualDedupEnabled={dedup_enabled}; keepIntervalSeconds={keep_interval_seconds}; "
            f"minIntervalSeconds={video_frame_min_interval_seconds()}"
        ),
    )
    if dedup_enabled:
        events, warnings = build_visual_candidate_events(
            sorted_candidates,
            keep_interval_seconds=keep_interval_seconds,
            max_frames=max_frames,
        )
    else:
        events, warnings = build_basic_candidate_events(
            sorted_candidates,
            keep_interval_seconds=keep_interval_seconds,
        )
    selected = select_stage_b_ocr_frames(events, max_frames=max_frames)
    stats = frame_selection_stats(events, selected)
    selected_label = f"{stats['selectedCount']}/{max_frames} 帧" if max_frames is not None else f"{stats['selectedCount']} 帧（未设上限）"
    emit_video_progress(
        progress_reporter,
        "parse.video.slide_detect",
        (
            "PPT 翻页检测完成："
            f"候选帧 {stats['candidateCount']} 个，"
            f"翻页命中 {stats['pptFlipCount']} 个，"
            f"视觉重复跳过 {stats['repeatVisualCount']} 个，"
            f"最终进入 OCR {selected_label}"
        ),
        percent=19,
        detail=(
            f"triggerCounts={stats['triggerCounts']}; selectedTriggerCounts={stats['selectedTriggerCounts']}; "
            f"visualGroupCount={stats['visualGroupCount']}; maxOcrFrames={max_frames_label}; "
            f"keepIntervalSeconds={keep_interval_seconds}; minIntervalSeconds={video_frame_min_interval_seconds()}; "
            f"ocrCandidateCount={stats['ocrCandidateCount']}; selectedCount={stats['selectedCount']}; "
            f"repeatVisualCount={stats['repeatVisualCount']}; pptFlipCount={stats['pptFlipCount']}"
        ),
    )
    process_event(
        stage="parse.video.slide_detect",
        action="select_ppt_slide_frames_completed",
        message="PPT 翻页检测和 OCR 帧预算选择完成",
        context={
            **stats,
            "maxOcrFrames": max_frames,
            "maxOcrFramesConfigured": max_frames is not None,
            "keepIntervalSeconds": keep_interval_seconds,
            "minIntervalSeconds": video_frame_min_interval_seconds(),
        },
    )
    return selected, warnings


def build_basic_candidate_events(
    candidates: list[FrameImage],
    *,
    keep_interval_seconds: int,
) -> tuple[list[FrameCandidateEvent], list[str]]:
    """关闭视觉去重时，沿用 PPT 翻页加固定间隔兜底事件。"""
    warnings: list[str] = []
    threshold = ppt_flip_threshold()
    first = candidates[0]
    events = [
        FrameCandidateEvent(
            replace(first, trigger="initial_slide", diff_score=0.0, slide_index=1, visual_decision=None),
            ocr_candidate=True,
            priority=trigger_priority("initial_slide"),
        )
    ]
    last_event_frame = first
    last_event_time = first.time_seconds
    last_slide_index = 1
    for candidate in candidates[1:]:
        diff_score = None
        try:
            diff_score = image_difference_score(last_event_frame.path, candidate.path)
        except Exception as exc:
            warnings.append(stage_warning("video.slide_detect", f"{candidate.path.name} 画面差异计算失败: {exc}"))
        is_flip = diff_score is not None and diff_score >= threshold
        is_interval = candidate.time_seconds - last_event_time >= keep_interval_seconds
        if not is_flip and not is_interval:
            continue
        if is_flip:
            last_slide_index += 1
        trigger = "ppt_flip" if is_flip else "interval"
        frame = replace(
            candidate,
            trigger=trigger,
            diff_score=diff_score,
            slide_index=last_slide_index,
            visual_decision=None,
        )
        events.append(FrameCandidateEvent(frame, ocr_candidate=True, priority=trigger_priority(trigger)))
        last_event_frame = candidate
        last_event_time = candidate.time_seconds
    return events, warnings


def build_visual_candidate_events(
    candidates: list[FrameImage],
    *,
    keep_interval_seconds: int,
    max_frames: int | None,
) -> tuple[list[FrameCandidateEvent], list[str]]:
    """生成 V6 视觉四态事件，并把高置信重复中的少量样本提升为验证 OCR。"""
    warnings: list[str] = []
    try:
        first_hash = visual_hash_for_image(candidates[0].path)
    except Exception as exc:
        warnings.append(stage_warning("video.visual_hash", f"视觉指纹不可用，已回退基础关键帧选择: {exc}"))
        return build_basic_candidate_events(candidates, keep_interval_seconds=keep_interval_seconds)

    first_group = VisualFrameGroup(
        group_id="visual-0001",
        hash_value=first_hash,
        representative_path=candidates[0].path,
        first_time=candidates[0].time_seconds,
        last_seen_time=candidates[0].time_seconds,
        last_ocr_candidate_time=candidates[0].time_seconds,
        slide_index=1,
    )
    groups: list[VisualFrameGroup] = [first_group]
    events: list[FrameCandidateEvent] = [
        FrameCandidateEvent(
            replace(
                candidates[0],
                trigger="initial_slide",
                diff_score=0.0,
                slide_index=1,
                visual_decision="new_visual",
                visual_group_id=first_group.group_id,
                visual_hash=first_hash,
            ),
            ocr_candidate=True,
            priority=trigger_priority("initial_slide"),
        )
    ]

    threshold = ppt_flip_threshold()
    last_event_frame = candidates[0]
    last_event_time = candidates[0].time_seconds
    last_slide_index = 1
    for candidate in candidates[1:]:
        try:
            hash_value = visual_hash_for_image(candidate.path)
        except Exception as exc:
            warnings.append(stage_warning("video.visual_hash", f"{candidate.path.name} 视觉指纹计算失败: {exc}"))
            continue
        diff_score = None
        try:
            diff_score = image_difference_score(last_event_frame.path, candidate.path)
        except Exception as exc:
            warnings.append(stage_warning("video.slide_detect", f"{candidate.path.name} 画面差异计算失败: {exc}"))
        is_flip = diff_score is not None and diff_score >= threshold
        is_interval = candidate.time_seconds - last_event_time >= keep_interval_seconds
        closest_group, hash_distance, group_diff_score = closest_visual_group(candidate.path, hash_value, groups)
        decision = visual_decision_for(hash_distance, group_diff_score)

        if decision == "repeat_visual_confident" and closest_group:
            frame = replace(
                candidate,
                trigger="repeat_visual_confident",
                diff_score=group_diff_score,
                slide_index=None,
                visual_decision=decision,
                visual_group_id=closest_group.group_id,
                suspected_visual_group_id=closest_group.group_id,
                visual_hash=hash_value,
                visual_hash_distance=hash_distance,
            )
            events.append(FrameCandidateEvent(frame, ocr_candidate=False, priority=trigger_priority("repeat_visual_confident")))
            closest_group.last_seen_time = candidate.time_seconds
            continue

        if decision == "ambiguous_visual":
            group = create_visual_group(groups, hash_value, candidate.path, candidate.time_seconds, None)
            frame = replace(
                candidate,
                trigger="ambiguous_visual",
                diff_score=group_diff_score,
                slide_index=None,
                visual_decision=decision,
                visual_group_id=group.group_id,
                suspected_visual_group_id=closest_group.group_id if closest_group else None,
                visual_hash=hash_value,
                visual_hash_distance=hash_distance,
            )
            events.append(FrameCandidateEvent(frame, ocr_candidate=True, priority=trigger_priority("ambiguous_visual")))
            group.last_ocr_candidate_time = candidate.time_seconds
            last_event_frame = candidate
            last_event_time = candidate.time_seconds
            continue

        if is_flip:
            last_slide_index += 1
        trigger = "ppt_flip" if is_flip else "interval" if is_interval else "new_visual"
        group = create_visual_group(groups, hash_value, candidate.path, candidate.time_seconds, last_slide_index)
        frame = replace(
            candidate,
            trigger=trigger,
            diff_score=diff_score,
            slide_index=last_slide_index,
            visual_decision="new_visual",
            visual_group_id=group.group_id,
            visual_hash=hash_value,
            visual_hash_distance=hash_distance,
        )
        events.append(FrameCandidateEvent(frame, ocr_candidate=True, priority=trigger_priority(trigger)))
        group.last_ocr_candidate_time = candidate.time_seconds
        last_event_frame = candidate
        last_event_time = candidate.time_seconds

    events, verification_warnings = promote_visual_verification_events(events, max_frames=max_frames)
    warnings.extend(verification_warnings)
    return events, warnings


def promote_visual_verification_events(
    events: list[FrameCandidateEvent],
    *,
    max_frames: int | None,
) -> tuple[list[FrameCandidateEvent], list[str]]:
    """从高置信重复事件中抽少量验证 OCR，降低小字或数字变化漏检风险。"""
    ratio = max(0.0, float(os.getenv("RAG_VIDEO_FRAME_VISUAL_VERIFICATION_RATIO", "0.25")))
    primary_ocr_count = sum(
        1
        for event in events
        if event.ocr_candidate and event.frame.trigger in {"initial_slide", "ppt_flip", "interval", "new_visual", "ambiguous_visual"}
    )
    verification_base = max_frames if max_frames is not None else primary_ocr_count
    budget = max(1, int(verification_base * ratio)) if ratio > 0 and verification_base > 0 else 0
    per_group_limit = max(0, int(os.getenv("RAG_VIDEO_FRAME_MAX_VERIFICATIONS_PER_VISUAL_GROUP", "2")))
    verify_interval = max(0, int(os.getenv("RAG_VIDEO_FRAME_VISUAL_VERIFY_INTERVAL_SECONDS", "900")))
    stay_seconds = max(0, int(os.getenv("RAG_VIDEO_FRAME_VISUAL_STAY_VERIFY_SECONDS", "600")))
    revisit_seconds = max(0, int(os.getenv("RAG_VIDEO_FRAME_VISUAL_REVISIT_VERIFY_SECONDS", "1800")))
    bucket_seconds = stage_b_bucket_seconds([event.frame for event in events], max(1, verification_base))
    primary_buckets = {
        event.frame.time_seconds // bucket_seconds
        for event in events
        if event.ocr_candidate and event.frame.trigger in {"initial_slide", "ppt_flip", "interval", "new_visual", "ambiguous_visual"}
    }

    last_ocr_time: dict[str, int] = {}
    first_seen_time: dict[str, int] = {}
    previous_seen_time: dict[str, int] = {}
    verification_count: dict[str, int] = defaultdict(int)
    used_budget = 0
    skipped: list[dict[str, Any]] = []
    promoted: list[FrameCandidateEvent] = []

    for event in sorted(events, key=lambda item: item.frame.time_seconds):
        frame = event.frame
        group_id = frame.visual_group_id
        if not group_id:
            promoted.append(event)
            continue
        first_seen_time.setdefault(group_id, frame.time_seconds)
        previous_time = previous_seen_time.get(group_id, frame.time_seconds)
        previous_seen_time[group_id] = frame.time_seconds
        if event.ocr_candidate:
            last_ocr_time[group_id] = frame.time_seconds
            promoted.append(event)
            continue
        if frame.trigger != "repeat_visual_confident":
            promoted.append(event)
            continue

        bucket = frame.time_seconds // bucket_seconds
        interval_due = frame.time_seconds - last_ocr_time.get(group_id, first_seen_time[group_id]) >= verify_interval
        bucket_due = bucket not in primary_buckets
        stay_due = frame.time_seconds - first_seen_time[group_id] >= stay_seconds
        revisit_due = frame.time_seconds - previous_time >= revisit_seconds
        should_verify = interval_due or bucket_due or stay_due or revisit_due
        if not should_verify:
            promoted.append(event)
            continue
        if verification_count[group_id] >= per_group_limit:
            skipped.append({"time": seconds_to_timestamp(frame.time_seconds), "group": group_id, "reason": "per_group_limit"})
            promoted.append(event)
            continue
        if used_budget >= budget:
            skipped.append({"time": seconds_to_timestamp(frame.time_seconds), "group": group_id, "reason": "global_budget"})
            promoted.append(event)
            continue
        verification_count[group_id] += 1
        used_budget += 1
        last_ocr_time[group_id] = frame.time_seconds
        verified_frame = replace(frame, trigger="visual_verification", visual_decision="visual_verification", slide_index=None)
        promoted.append(FrameCandidateEvent(verified_frame, ocr_candidate=True, priority=trigger_priority("visual_verification")))

    warnings: list[str] = []
    if skipped:
        ranges = [{"startTime": item["time"], "endTime": item["time"], "reason": item["reason"], "visualGroupId": item["group"]} for item in skipped]
        warnings.append(
            stage_warning(
                "video.frame.visual_verification",
                "visualVerificationSkippedCount="
                f"{len(skipped)}; visualVerificationSkippedRanges={ranges}; "
                f"visualVerificationBudget={budget}; visualVerificationPerGroupLimit={per_group_limit}",
            )
        )
    return promoted, warnings


def select_stage_b_ocr_frames(events: list[FrameCandidateEvent], *, max_frames: int | None) -> list[FrameImage]:
    """按全视频时间桶和可选最终预算选择 OCR 帧，避免前缀偏置。"""
    ocr_events = [event for event in events if event.ocr_candidate]
    if not ocr_events:
        return []
    initial_events = [event for event in ocr_events if event.frame.trigger == "initial_slide"]
    selected: list[FrameCandidateEvent] = initial_events[:1]
    selected_ids = {id(event) for event in selected}
    effective_budget = max_frames if max_frames is not None else len(ocr_events)
    remaining_budget = max(0, effective_budget - len(selected))
    if remaining_budget <= 0:
        return attach_visual_only_ranges([event.frame for event in selected], events)

    bucket_seconds = stage_b_bucket_seconds([event.frame for event in events], effective_budget)
    buckets: dict[int, list[FrameCandidateEvent]] = defaultdict(list)
    for event in ocr_events:
        if id(event) in selected_ids:
            continue
        buckets[event.frame.time_seconds // bucket_seconds].append(event)
    for bucket, bucket_events in buckets.items():
        bucket_center = bucket * bucket_seconds + bucket_seconds / 2
        bucket_events.sort(key=lambda event, center=bucket_center: stage_b_event_sort_key(event, center))

    group_counts = selected_visual_group_counts(selected)
    min_interval = video_frame_min_interval_seconds()
    while remaining_budget > 0 and buckets:
        made_progress = False
        for bucket in stage_b_bucket_order(sorted(buckets.keys()), remaining_budget):
            if remaining_budget <= 0:
                break
            bucket_events = buckets[bucket]
            while bucket_events:
                event = bucket_events.pop(0)
                if not can_select_stage_b_event(event, selected, group_counts, min_interval):
                    continue
                selected.append(event)
                selected_ids.add(id(event))
                increment_visual_group_count(event, group_counts)
                remaining_budget -= 1
                made_progress = True
                break
            if not bucket_events:
                buckets.pop(bucket, None)
        if not made_progress:
            break

    selected_frames = [event.frame for event in sorted(selected, key=lambda item: item.frame.time_seconds)]
    return attach_visual_only_ranges(selected_frames, events)


def frame_selection_stats(events: list[FrameCandidateEvent], selected: list[FrameImage]) -> dict[str, Any]:
    """汇总 PPT 翻页检测和 OCR 预算选择统计，用于前端进度和控制面板。"""
    trigger_counts: dict[str, int] = defaultdict(int)
    selected_trigger_counts: dict[str, int] = defaultdict(int)
    visual_groups = {event.frame.visual_group_id for event in events if event.frame.visual_group_id}
    for event in events:
        trigger_counts[event.frame.trigger] += 1
    for frame in selected:
        selected_trigger_counts[frame.trigger] += 1
    return {
        "candidateCount": len(events),
        "ocrCandidateCount": sum(1 for event in events if event.ocr_candidate),
        "selectedCount": len(selected),
        "pptFlipCount": trigger_counts.get("ppt_flip", 0),
        "intervalCount": trigger_counts.get("interval", 0),
        "newVisualCount": trigger_counts.get("new_visual", 0),
        "ambiguousVisualCount": trigger_counts.get("ambiguous_visual", 0),
        "repeatVisualCount": trigger_counts.get("repeat_visual_confident", 0),
        "visualVerificationCount": trigger_counts.get("visual_verification", 0),
        "visualGroupCount": len(visual_groups),
        "triggerCounts": dict(trigger_counts),
        "selectedTriggerCounts": dict(selected_trigger_counts),
    }


def stage_b_bucket_order(bucket_keys: list[int], remaining_budget: int) -> list[int]:
    """当时间桶多于预算时按全时长均匀取桶，避免只选择前缀桶。"""
    if remaining_budget <= 0 or len(bucket_keys) <= remaining_budget:
        return bucket_keys
    if remaining_budget == 1:
        return [bucket_keys[len(bucket_keys) // 2]]
    selected_indexes = {
        round(index * (len(bucket_keys) - 1) / (remaining_budget - 1))
        for index in range(remaining_budget)
    }
    return [bucket_keys[index] for index in sorted(selected_indexes)]


def can_select_stage_b_event(
    event: FrameCandidateEvent,
    selected: list[FrameCandidateEvent],
    group_counts: dict[str, int],
    min_interval: int,
) -> bool:
    """判断候选事件是否满足最终 OCR 最小间隔和视觉组代表上限。"""
    frame = event.frame
    if frame.trigger != "initial_slide" and min_interval > 0:
        if any(abs(frame.time_seconds - item.frame.time_seconds) < min_interval for item in selected):
            return False
    group_id = frame.visual_group_id
    if group_id and frame.trigger not in {"ambiguous_visual", "visual_verification"}:
        max_representatives = max(1, int(os.getenv("RAG_VIDEO_FRAME_MAX_REPRESENTATIVES_PER_VISUAL_GROUP", "1")))
        if group_counts.get(group_id, 0) >= max_representatives:
            return False
    return True


def selected_visual_group_counts(selected: list[FrameCandidateEvent]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for event in selected:
        increment_visual_group_count(event, counts)
    return counts


def increment_visual_group_count(event: FrameCandidateEvent, counts: dict[str, int]) -> None:
    group_id = event.frame.visual_group_id
    if group_id and event.frame.trigger not in {"ambiguous_visual", "visual_verification"}:
        counts[group_id] += 1


def stage_b_event_sort_key(event: FrameCandidateEvent, bucket_center: float) -> tuple[float, int, int]:
    return (abs(event.frame.time_seconds - bucket_center), -event.priority, event.frame.time_seconds)


def attach_visual_only_ranges(selected_frames: list[FrameImage], events: list[FrameCandidateEvent]) -> list[FrameImage]:
    """把未 OCR 的高置信视觉重复时间只挂到 visual-only 字段。"""
    visual_only_times: dict[str, list[int]] = defaultdict(list)
    for event in events:
        if event.ocr_candidate:
            continue
        group_id = event.frame.visual_group_id
        if group_id:
            visual_only_times[group_id].append(event.frame.time_seconds)
    first_selected_index: dict[str, int] = {}
    for index, frame in enumerate(selected_frames):
        if frame.visual_group_id and frame.visual_group_id not in first_selected_index:
            first_selected_index[frame.visual_group_id] = index
    updated: list[FrameImage] = []
    for index, frame in enumerate(selected_frames):
        group_id = frame.visual_group_id
        if group_id and first_selected_index.get(group_id) == index and visual_only_times.get(group_id):
            times = sorted(dict.fromkeys(visual_only_times[group_id]))
            updated.append(
                replace(
                    frame,
                    visual_time_ranges=[{"startTime": seconds_to_timestamp(item), "endTime": seconds_to_timestamp(item)} for item in times],
                    visual_source_frame_times=[seconds_to_timestamp(item) for item in times],
                )
            )
        else:
            updated.append(frame)
    return updated


def create_visual_group(
    groups: list[VisualFrameGroup],
    hash_value: str,
    path: Path,
    time_seconds: int,
    slide_index: int | None,
) -> VisualFrameGroup:
    group = VisualFrameGroup(
        group_id=f"visual-{len(groups) + 1:04d}",
        hash_value=hash_value,
        representative_path=path,
        first_time=time_seconds,
        last_seen_time=time_seconds,
        last_ocr_candidate_time=time_seconds,
        slide_index=slide_index,
    )
    groups.append(group)
    return group


def closest_visual_group(
    path: Path,
    hash_value: str,
    groups: list[VisualFrameGroup],
) -> tuple[VisualFrameGroup | None, int | None, float | None]:
    closest: VisualFrameGroup | None = None
    closest_distance: int | None = None
    for group in groups:
        distance = hamming_distance(hash_value, group.hash_value)
        if closest_distance is None or distance < closest_distance:
            closest = group
            closest_distance = distance
    if closest is None:
        return None, None, None
    diff_score = None
    try:
        diff_score = image_difference_score(closest.representative_path, path)
    except Exception:
        pass
    return closest, closest_distance, diff_score


def visual_decision_for(hash_distance: int | None, diff_score: float | None) -> str:
    """基于保守 hash 距离和低像素差判断视觉重复状态。"""
    if hash_distance is None:
        return "new_visual"
    max_distance = max(0, int(os.getenv("RAG_VIDEO_FRAME_VISUAL_HASH_MAX_DISTANCE", "4")))
    ambiguous_margin = max(0, int(os.getenv("RAG_VIDEO_FRAME_VISUAL_AMBIGUOUS_MARGIN", "2")))
    same_diff_threshold = visual_same_diff_threshold()
    if diff_score is not None and hash_distance <= max_distance and diff_score <= same_diff_threshold:
        return "repeat_visual_confident"
    if diff_score is not None and diff_score >= ppt_flip_threshold():
        return "new_visual"
    if hash_distance <= max_distance + ambiguous_margin:
        return "ambiguous_visual"
    if diff_score is not None and diff_score <= max(ppt_flip_threshold(), same_diff_threshold * 2):
        return "ambiguous_visual"
    return "new_visual"


def visual_hash_for_image(path: Path) -> str:
    """用 Pillow 实现轻量视觉指纹，不引入 OpenCV、SSIM 或 pHash 依赖。"""
    algorithm = os.getenv("RAG_VIDEO_FRAME_VISUAL_HASH_ALGORITHM", "dhash").strip().lower()
    if algorithm == "ahash":
        return average_hash_for_image(path)
    return difference_hash_for_image(path)


def difference_hash_for_image(path: Path) -> str:
    from PIL import Image

    with Image.open(path) as image:
        pixels = list(image.convert("L").resize((9, 8)).getdata())
    bits: list[str] = []
    for row in range(8):
        offset = row * 9
        for col in range(8):
            bits.append("1" if pixels[offset + col] > pixels[offset + col + 1] else "0")
    return f"{int(''.join(bits), 2):016x}"


def average_hash_for_image(path: Path) -> str:
    from PIL import Image

    with Image.open(path) as image:
        pixels = list(image.convert("L").resize((8, 8)).getdata())
    average = sum(pixels) / max(len(pixels), 1)
    bits = ["1" if pixel >= average else "0" for pixel in pixels]
    return f"{int(''.join(bits), 2):016x}"


def hamming_distance(left_hash: str, right_hash: str) -> int:
    return (int(left_hash, 16) ^ int(right_hash, 16)).bit_count()


def stage_b_bucket_seconds(frames: list[FrameImage], max_frames: int) -> int:
    if not frames:
        return 1
    first_time = min(frame.time_seconds for frame in frames)
    last_time = max(frame.time_seconds for frame in frames)
    return max(1, ceil((last_time - first_time + 1) / max(1, max_frames)))


def trigger_priority(trigger: str) -> int:
    priorities = {
        "initial_slide": 100,
        "ppt_flip": 90,
        "ambiguous_visual": 85,
        "visual_verification": 82,
        "new_visual": 70,
        "interval": 60,
        "repeat_visual_confident": 10,
    }
    return priorities.get(trigger, 0)


def ppt_flip_threshold() -> float:
    return float(os.getenv("RAG_VIDEO_PPT_FLIP_DIFF_THRESHOLD", "0.08"))


def visual_same_diff_threshold() -> float:
    configured = os.getenv("RAG_VIDEO_FRAME_VISUAL_SAME_DIFF_THRESHOLD")
    if configured is not None and configured.strip() != "":
        return max(0.0, float(configured))
    return min(0.03, ppt_flip_threshold() * 0.5)


def visual_dedup_enabled() -> bool:
    return os.getenv("RAG_VIDEO_FRAME_VISUAL_DEDUP_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def video_frame_scan_mode() -> str:
    value = os.getenv("RAG_VIDEO_FRAME_SCAN_MODE", "auto").strip().lower()
    return value if value in {"auto", "prefix", "full"} else "auto"


def video_frame_min_interval_seconds() -> int:
    """读取最终 OCR 帧之间的最小间隔。"""
    return max(0, int(os.getenv("RAG_VIDEO_FRAME_MIN_INTERVAL_SECONDS", "30")))


def video_ocr_frame_limit() -> int | None:
    """读取显式 OCR 帧上限；未配置或小于 1 时不截断最终 OCR 帧。"""
    value = os.getenv("RAG_VIDEO_MAX_FRAMES")
    if value is None or value.strip() == "":
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def format_ocr_frame_limit(max_frames: int | None) -> str:
    """生成面向日志和前端进度的 OCR 帧上限说明。"""
    if max_frames is None:
        return "未设置最终 OCR 帧上限"
    return f"最终 OCR 预算最多 {max_frames} 帧"


def image_difference_score(left_path: Path, right_path: Path) -> float:
    """计算两张候选帧缩略图的平均像素差异，用于检测 PPT 翻页。"""
    try:
        from PIL import Image, ImageChops, ImageStat
    except ImportError as exc:
        raise RuntimeError("Pillow 不可用，无法检测 PPT 翻页") from exc

    with Image.open(left_path) as left_image, Image.open(right_path) as right_image:
        left = left_image.convert("L").resize((96, 54))
        right = right_image.convert("L").resize((96, 54))
        diff = ImageChops.difference(left, right)
        return round(ImageStat.Stat(diff).mean[0] / 255.0, 6)


def probe_media_duration(video_input: str | Path) -> float:
    """读取视频时长，供同步 ASR 纯文本降级为估算时间戳字幕。"""
    strict_duration = probe_media_duration_strict(video_input)
    if strict_duration is not None:
        return strict_duration
    return 60.0


def probe_media_duration_strict(video_input: str | Path) -> float | None:
    """严格读取视频时长，失败返回 None，供全时长抽帧决定是否降级。"""
    ffprobe = os.getenv("FFPROBE_COMMAND") or shutil.which("ffprobe")
    if ffprobe:
        command = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_input),
        ]
        try:
            result = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
            duration = float(result.stdout.strip())
            return max(1.0, duration) if duration > 0 else None
        except Exception:
            pass
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return None
    try:
        result = subprocess.run(
            [ffmpeg, "-i", str(video_input)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        output = f"{result.stderr}\n{result.stdout}"
        match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
        if match:
            hours, minutes, seconds = match.groups()
            duration = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            return max(1.0, duration) if duration > 0 else None
    except Exception:
        pass
    return None


def transcript_has_timestamps(text: str) -> bool:
    return "-->" in text or bool(re.search(r"\d{1,2}:\d{2}(?::\d{2})?", text))


SRT_RANGE_PATTERN = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}(?:[,.]\d{1,3})?)\s*-->\s*"
    r"(?P<end>\d{1,2}:\d{2}:\d{2}(?:[,.]\d{1,3})?)"
)


def parse_srt_cues(text: str) -> list[TranscriptCue]:
    """解析 SRT 字幕段，供长视频分段结果做全局时间合并。"""
    cues: list[TranscriptCue] = []
    groups = [group.strip() for group in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")) if group.strip()]
    for group in groups:
        lines = [line.strip() for line in group.splitlines() if line.strip()]
        if not lines:
            continue
        timestamp_index = next((index for index, line in enumerate(lines) if SRT_RANGE_PATTERN.search(line)), -1)
        if timestamp_index < 0:
            continue
        match = SRT_RANGE_PATTERN.search(lines[timestamp_index])
        if not match:
            continue
        content = normalize_text("\n".join(lines[timestamp_index + 1 :]))
        if not content:
            continue
        cues.append(
            TranscriptCue(
                index=len(cues) + 1,
                start_seconds=srt_timestamp_to_seconds(match.group("start")),
                end_seconds=srt_timestamp_to_seconds(match.group("end")),
                text=content,
            )
        )
    return cues


def offset_srt_transcript(text: str, offset_seconds: float) -> str:
    """把分段内时间戳平移到原视频全局时间轴。"""
    cues = parse_srt_cues(text)
    if not cues:
        return text
    shifted = [
        TranscriptCue(
            index=index,
            start_seconds=cue.start_seconds + offset_seconds,
            end_seconds=cue.end_seconds + offset_seconds,
            text=cue.text,
        )
        for index, cue in enumerate(cues, start=1)
    ]
    return cues_to_srt(shifted)


def cue_center_in_segment(cue: TranscriptCue, segment: AudioSegment) -> bool:
    """保留与名义分段相交的字幕，重叠区重复结果由后续合并去重。"""
    return cue.end_seconds > segment.nominal_start - 0.1 and cue.start_seconds < segment.nominal_end + 0.1


def merge_transcript_cues(cues: list[TranscriptCue], *, overlap_seconds: int) -> list[TranscriptCue]:
    """合并重叠分段产生的字幕，并去掉边界处重复识别结果。"""
    merged: list[TranscriptCue] = []
    for cue in sorted(cues, key=lambda item: (item.start_seconds, item.end_seconds, item.text)):
        if any(is_duplicate_cue(cue, existing, overlap_seconds) for existing in merged[-6:]):
            continue
        merged.append(cue)
    return [
        TranscriptCue(
            index=index,
            start_seconds=cue.start_seconds,
            end_seconds=max(cue.start_seconds + 0.2, cue.end_seconds),
            text=cue.text,
        )
        for index, cue in enumerate(merged, start=1)
    ]


def is_duplicate_cue(left: TranscriptCue, right: TranscriptCue, overlap_seconds: int) -> bool:
    left_text = normalize_text(left.text)
    right_text = normalize_text(right.text)
    if not left_text or not right_text:
        return False
    same_text = left_text == right_text or left_text in right_text or right_text in left_text
    close_time = abs(left.start_seconds - right.start_seconds) <= max(2, overlap_seconds)
    return same_text and close_time


def cues_to_srt(cues: list[TranscriptCue]) -> str:
    lines: list[str] = []
    for index, cue in enumerate(cues, start=1):
        lines.extend(
            [
                str(index),
                f"{seconds_to_srt_timestamp(cue.start_seconds)} --> {seconds_to_srt_timestamp(cue.end_seconds)}",
                cue.text,
                "",
            ]
        )
    return "\n".join(lines).strip()


def srt_timestamp_to_seconds(value: str) -> float:
    main, _, millis = value.replace(",", ".").partition(".")
    parts = [int(part) for part in main.split(":")]
    hours, minutes, seconds = parts[-3:]
    return hours * 3600 + minutes * 60 + seconds + (float(f"0.{millis}") if millis else 0.0)


def estimate_srt_from_transcript(text: str, duration_seconds: float) -> str:
    """把纯文本转写切成估算 SRT，作为 filetrans 不可用时的时间轴保底。"""
    cleaned = normalize_text(text)
    sentences = [item.strip() for item in re.split(r"(?<=[。！？!?\.])\s*", cleaned) if item.strip()]
    if not sentences:
        sentences = [cleaned] if cleaned else ["视频转写文本为空"]
    segment_seconds = max(1.0, duration_seconds / max(len(sentences), 1))
    lines: list[str] = []
    for index, sentence in enumerate(sentences, start=1):
        start = (index - 1) * segment_seconds
        end = duration_seconds if index == len(sentences) else min(duration_seconds, index * segment_seconds)
        lines.extend(
            [
                str(index),
                f"{seconds_to_srt_timestamp(start)} --> {seconds_to_srt_timestamp(end)}",
                sentence,
                "",
            ]
        )
    return "\n".join(lines).strip()


@logged_rag_method("parse.video.ocr", "ocr_video_frames", "执行视频关键帧 OCR")
def ocr_video_frames(
    *,
    frames: list[FrameImage],
    document_id: str,
    file_type: str,
    source_title: str,
    source_path: str | None,
    ocr_client: BailianOcrClient,
    progress_reporter: RagProgressReporter | None = None,
) -> tuple[list[DocumentBlock], list[str]]:
    blocks: list[DocumentBlock] = []
    warnings: list[str] = []
    video_url = source_path if is_public_url(source_path) else None
    for index, frame in enumerate(frames, start=1):
        image_bytes = frame.path.read_bytes()
        try:
            if ocr_client.available:
                emit_model_progress(
                    progress_reporter,
                    f"第 {index}/{len(frames)} 帧：目前在使用 {ocr_client.model} 模型完成关键帧 OCR 识别事件",
                    percent=20,
                    detail=f"目前在使用 {ocr_client.model} 模型完成关键帧 OCR 识别事件",
                    stage_code="parse.video.ocr",
                )
            ocr_result = ocr_client.recognize_image_bytes(
                image_bytes=image_bytes,
                filename=frame.path.name,
                retry_callback=lambda event, frame_index=index, total_frames=len(frames): emit_ocr_retry_progress(
                    progress_reporter,
                    event,
                    frame_index=frame_index,
                    total_frames=total_frames,
                ),
            )
        except Exception as exc:
            warnings.append(stage_warning(f"video.frame_ocr[{index}]", f"百炼 OCR 调用异常: {exc}"))
            ocr_result = None
        if ocr_client.available and ocr_result and ocr_result.text:
            emit_model_progress(
                progress_reporter,
                f"第 {index}/{len(frames)} 帧：已使用 {ocr_client.model} 模型完成关键帧 OCR 识别事件",
                percent=22,
                detail=f"已使用 {ocr_client.model} 模型完成关键帧 OCR 识别事件",
                stage_code="parse.video.ocr",
            )
        if ocr_result and ocr_result.warnings:
            warnings.extend(stage_warning(f"video.frame_ocr[{index}]", warning) for warning in ocr_result.warnings)
        text = normalize_text(ocr_result.text if ocr_result else "")
        if not text:
            fallback_text, fallback_warning = tesseract_frame_text(image_bytes)
            if fallback_warning:
                warnings.append(stage_warning(f"video.frame_ocr[{index}]", fallback_warning))
            text = fallback_text
        if not text:
            warnings.append(stage_warning(f"video.frame_ocr[{index}]", f"{frame.path.name} 未识别到可索引文字"))
            continue
        start_time = seconds_to_timestamp(frame.time_seconds)
        metadata = {
            "frameIndex": index,
            "frameTime": start_time,
            "startTime": start_time,
            "mediaType": "video",
            "evidenceChannel": "frame_ocr",
            "frameTrigger": frame.trigger,
            "frameDiffScore": frame.diff_score,
            "detectedSlideIndex": frame.slide_index,
            "visualDecision": frame.visual_decision,
            "visualGroupId": frame.visual_group_id,
            "suspectedVisualGroupId": frame.suspected_visual_group_id,
            "visualHash": frame.visual_hash,
            "visualHashDistance": frame.visual_hash_distance,
            "timeRanges": [{"startTime": start_time, "endTime": start_time}],
            "sourceFrameTimes": [start_time],
            **(ocr_result.metadata if ocr_result else {}),
        }
        if frame.visual_time_ranges:
            metadata["visualTimeRanges"] = frame.visual_time_ranges
        if frame.visual_source_frame_times:
            metadata["visualSourceFrameTimes"] = frame.visual_source_frame_times
        if video_url:
            metadata["videoUrl"] = video_url
        blocks.append(
            DocumentBlock(
                documentId=document_id,
                blockId=f"{document_id}-frame-{index}",
                fileType=file_type,
                blockType="image",
                slideIndex=frame.slide_index,
                startTime=start_time,
                sectionTitle=f"视频画面 {start_time}",
                contentText=f"视频画面 {start_time}\n{text}",
                assetPath=source_path,
                parseEngine=(ocr_result.parser if ocr_result else None) or "video-frame-ocr",
                confidence=max(ocr_result.confidence if ocr_result else 0.0, 0.72),
                sourceTitle=source_title,
                sourcePath=source_path,
                metadata=metadata,
            )
        )
    return blocks, warnings


@logged_rag_method("parse.video.summary", "build_video_segment_summary_blocks", "生成视频片段摘要块")
def build_video_segment_summary_blocks(
    *,
    document_id: str,
    file_type: str,
    source_title: str,
    source_path: str | None,
    transcript_blocks: list[DocumentBlock],
    frame_blocks: list[DocumentBlock],
) -> tuple[list[DocumentBlock], list[str]]:
    """把字幕和关键帧 OCR 汇总为可检索的视频片段摘要块。"""
    try:
        if transcript_blocks:
            return build_transcript_segment_summaries(
                document_id=document_id,
                file_type=file_type,
                source_title=source_title,
                source_path=source_path,
                transcript_blocks=transcript_blocks,
                frame_blocks=frame_blocks,
            ), []
        return build_frame_segment_summaries(
            document_id=document_id,
            file_type=file_type,
            source_title=source_title,
            source_path=source_path,
            frame_blocks=frame_blocks,
        ), []
    except Exception as exc:
        return [], [stage_warning("video.segment_summary", f"生成视频片段摘要失败: {exc}")]


@logged_rag_method("parse.video.summary", "build_transcript_segment_summaries", "生成字幕片段摘要")
def build_transcript_segment_summaries(
    *,
    document_id: str,
    file_type: str,
    source_title: str,
    source_path: str | None,
    transcript_blocks: list[DocumentBlock],
    frame_blocks: list[DocumentBlock],
) -> list[DocumentBlock]:
    segment_seconds = max(30, int(os.getenv("RAG_VIDEO_SEGMENT_SECONDS", "120")))
    max_cues = max(1, int(os.getenv("RAG_VIDEO_SEGMENT_MAX_CUES", "6")))
    groups: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    current_start = 0
    for block in transcript_blocks:
        block_start = timestamp_to_seconds(block.startTime)
        if not current:
            current = [block]
            current_start = block_start
            continue
        if block_start - current_start >= segment_seconds or len(current) >= max_cues:
            groups.append(current)
            current = [block]
            current_start = block_start
        else:
            current.append(block)
    if current:
        groups.append(current)

    summary_blocks: list[DocumentBlock] = []
    for index, group in enumerate(groups, start=1):
        start_time = group[0].startTime or "00:00:00"
        end_time = group[-1].endTime or group[-1].startTime or start_time
        start_seconds = timestamp_to_seconds(start_time)
        end_seconds = max(start_seconds, timestamp_to_seconds(end_time))
        matched_frames = frames_between(frame_blocks, start_seconds - 10, end_seconds + 15)
        subtitle_text = summarize_text(" ".join(block.contentText for block in group), 280)
        frame_text = summarize_text(" ".join(strip_frame_heading(block.contentText) for block in matched_frames), 220)
        content_parts = [
            f"视频片段摘要：{start_time} - {end_time}",
            f"字幕要点：{subtitle_text}",
        ]
        if frame_text:
            content_parts.append(f"画面线索：{frame_text}")
        summary_blocks.append(
            build_segment_summary_block(
                document_id=document_id,
                file_type=file_type,
                source_title=source_title,
                source_path=source_path,
                index=index,
                start_time=start_time,
                end_time=end_time,
                content_text="\n".join(content_parts),
                source_block_ids=[block.blockId for block in group],
                frame_block_ids=[block.blockId for block in matched_frames],
                frame_duplicate_group_ids=collect_frame_duplicate_group_ids(matched_frames),
                frame_time_ranges=collect_frame_time_ranges(matched_frames),
                source_frame_times=collect_source_frame_times(matched_frames),
                segment_kind="subtitle_frame",
            )
        )
    return summary_blocks


@logged_rag_method("parse.video.summary", "build_frame_segment_summaries", "生成画面 OCR 片段摘要")
def build_frame_segment_summaries(
    *,
    document_id: str,
    file_type: str,
    source_title: str,
    source_path: str | None,
    frame_blocks: list[DocumentBlock],
) -> list[DocumentBlock]:
    if not frame_blocks:
        return []
    segment_seconds = max(30, int(os.getenv("RAG_VIDEO_SEGMENT_SECONDS", "120")))
    groups: list[list[DocumentBlock]] = []
    current: list[DocumentBlock] = []
    current_start = 0
    for block in frame_blocks:
        block_start, _block_end = frame_block_bounds(block)
        if not current:
            current = [block]
            current_start = block_start
            continue
        if block_start - current_start >= segment_seconds:
            groups.append(current)
            current = [block]
            current_start = block_start
        else:
            current.append(block)
    if current:
        groups.append(current)

    summary_blocks: list[DocumentBlock] = []
    for index, group in enumerate(groups, start=1):
        start_time = group[0].startTime or "00:00:00"
        end_time = seconds_to_timestamp(max(frame_block_bounds(block)[1] for block in group))
        frame_text = summarize_text(" ".join(strip_frame_heading(block.contentText) for block in group), 320)
        summary_blocks.append(
            build_segment_summary_block(
                document_id=document_id,
                file_type=file_type,
                source_title=source_title,
                source_path=source_path,
                index=index,
                start_time=start_time,
                end_time=end_time,
                content_text="\n".join(
                    [
                        f"视频片段摘要：{start_time} - {end_time}",
                        f"画面线索：{frame_text}",
                    ]
                ),
                source_block_ids=[],
                frame_block_ids=[block.blockId for block in group],
                frame_duplicate_group_ids=collect_frame_duplicate_group_ids(group),
                frame_time_ranges=collect_frame_time_ranges(group),
                source_frame_times=collect_source_frame_times(group),
                segment_kind="frame_only",
            )
        )
    return summary_blocks


def build_segment_summary_block(
    *,
    document_id: str,
    file_type: str,
    source_title: str,
    source_path: str | None,
    index: int,
    start_time: str,
    end_time: str,
    content_text: str,
    source_block_ids: list[str],
    frame_block_ids: list[str],
    segment_kind: str,
    frame_duplicate_group_ids: list[str] | None = None,
    frame_time_ranges: list[dict[str, str]] | None = None,
    source_frame_times: list[str] | None = None,
) -> DocumentBlock:
    metadata = {
        "segmentIndex": index,
        "segmentKind": segment_kind,
        "startTime": start_time,
        "endTime": end_time,
        "mediaType": "video",
        "evidenceChannel": "video_segment_summary",
        "sourceBlockIds": source_block_ids,
        "frameBlockIds": frame_block_ids,
    }
    if frame_duplicate_group_ids:
        metadata["frameDuplicateGroupIds"] = frame_duplicate_group_ids
    if frame_time_ranges:
        metadata["frameTimeRanges"] = frame_time_ranges
    if source_frame_times:
        metadata["sourceFrameTimes"] = source_frame_times
    if is_public_url(source_path):
        metadata["videoUrl"] = source_path
    return DocumentBlock(
        documentId=document_id,
        blockId=f"{document_id}-video-segment-summary-{index}",
        fileType=file_type,
        blockType="text",
        startTime=start_time,
        endTime=end_time,
        sectionTitle=f"视频片段摘要 {start_time} - {end_time}",
        contentText=content_text,
        parseEngine="video-segment-summary",
        confidence=0.82,
        sourceTitle=source_title,
        sourcePath=source_path,
        metadata=metadata,
    )


def frames_between(frame_blocks: list[DocumentBlock], start_seconds: int, end_seconds: int) -> list[DocumentBlock]:
    result: list[DocumentBlock] = []
    for block in frame_blocks:
        if any(ranges_overlap(start_seconds, end_seconds, frame_start, frame_end) for frame_start, frame_end in frame_ranges(block)):
            result.append(block)
    if len(result) <= 3:
        return result
    segment_center = (start_seconds + end_seconds) / 2
    return sorted(result, key=lambda block: frame_match_sort_key(block, segment_center))[:3]


def frame_match_sort_key(block: DocumentBlock, segment_center: float) -> tuple[float, int, int, float]:
    ranges = frame_ranges(block)
    nearest_distance = min(abs(((start + end) / 2) - segment_center) for start, end in ranges)
    return (
        nearest_distance,
        -summary_trigger_priority(str((block.metadata or {}).get("frameTrigger") or "")),
        -len(strip_frame_heading(block.contentText)),
        -float(block.confidence or 0.0),
    )


def summary_trigger_priority(trigger: str) -> int:
    priorities = {
        "initial_slide": 6,
        "ppt_flip": 5,
        "ambiguous_visual": 4,
        "visual_verification": 4,
        "new_visual": 3,
        "interval": 2,
    }
    return priorities.get(trigger, 0)


def frame_block_bounds(block: DocumentBlock) -> tuple[int, int]:
    ranges = frame_ranges(block)
    starts = [item[0] for item in ranges]
    ends = [item[1] for item in ranges]
    return min(starts), max(ends)


def frame_ranges(block: DocumentBlock) -> list[tuple[int, int]]:
    metadata = block.metadata or {}
    raw_ranges = metadata.get("timeRanges")
    ranges: list[tuple[int, int]] = []
    if isinstance(raw_ranges, list):
        for item in raw_ranges:
            if not isinstance(item, dict):
                continue
            start = timestamp_to_seconds(str(item.get("startTime") or item.get("start") or ""))
            end = timestamp_to_seconds(str(item.get("endTime") or item.get("end") or item.get("startTime") or ""))
            ranges.append((start, max(start, end)))
    if ranges:
        return ranges
    start = timestamp_to_seconds(block.startTime)
    end = timestamp_to_seconds(block.endTime) if block.endTime else start
    return [(start, max(start, end))]


def ranges_overlap(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return max(left_start, right_start) <= min(left_end, right_end)


def collect_frame_duplicate_group_ids(frame_blocks: list[DocumentBlock]) -> list[str]:
    values: list[str] = []
    for block in frame_blocks:
        group_id = block.metadata.get("duplicateGroupId")
        if group_id:
            values.append(str(group_id))
    return list(dict.fromkeys(values))


def collect_frame_time_ranges(frame_blocks: list[DocumentBlock]) -> list[dict[str, str]]:
    ranges: list[dict[str, str]] = []
    for block in frame_blocks:
        raw_ranges = block.metadata.get("timeRanges")
        if isinstance(raw_ranges, list):
            for item in raw_ranges:
                if isinstance(item, dict) and item.get("startTime"):
                    ranges.append({
                        "startTime": str(item.get("startTime")),
                        "endTime": str(item.get("endTime") or item.get("startTime")),
                    })
            continue
        start_time = block.startTime or "00:00:00"
        ranges.append({"startTime": start_time, "endTime": block.endTime or start_time})
    unique: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for item in ranges:
        key = (item["startTime"], item["endTime"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def collect_source_frame_times(frame_blocks: list[DocumentBlock]) -> list[str]:
    times: list[str] = []
    for block in frame_blocks:
        raw_times = block.metadata.get("sourceFrameTimes")
        if isinstance(raw_times, list):
            times.extend(str(item) for item in raw_times)
        elif block.startTime:
            times.append(block.startTime)
    return list(dict.fromkeys(times))


def strip_frame_heading(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > 1 and lines[0].startswith("视频画面"):
        return " ".join(lines[1:])
    return " ".join(lines)


def summarize_text(text: str, max_chars: int) -> str:
    cleaned = normalize_text(text).replace("\n", " ")
    if not cleaned:
        return "暂无可用文字"
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip() + "..."


def fallback_video_metadata_block(
    document_id: str,
    filename: str,
    source_title: str,
    source_path: str | None,
) -> DocumentBlock:
    video_url = source_path if is_public_url(source_path) else None
    metadata = {
        "mediaType": "video",
        "evidenceChannel": "video_metadata",
        "filename": filename,
    }
    if video_url:
        metadata["videoUrl"] = video_url
    return DocumentBlock(
        documentId=document_id,
        blockId=f"{document_id}-video-metadata",
        fileType=normalize_video_file_type(filename),
        blockType="text",
        sectionTitle="视频资料元数据",
        contentText=f"视频资料《{source_title}》已上传，来源：{source_path or filename}。当前未生成 ASR 字幕或关键帧 OCR 文本。",
        parseEngine="video-metadata-fallback",
        confidence=0.3,
        sourceTitle=source_title,
        sourcePath=source_path,
        metadata=metadata,
    )


def tesseract_frame_text(image_bytes: bytes) -> tuple[str, str | None]:
    try:
        from io import BytesIO

        from PIL import Image
        import pytesseract

        image = Image.open(BytesIO(image_bytes))
        text = normalize_text(pytesseract.image_to_string(image, lang=os.getenv("OCR_LANG", "chi_sim+eng")))
        return text, None if text else "本地 OCR 未获得文本"
    except Exception as exc:
        return "", f"本地 OCR 不可用: {exc}"


def ffmpeg_executable() -> str | None:
    configured = os.getenv("FFMPEG_COMMAND")
    if configured:
        return configured
    discovered = shutil.which("ffmpeg")
    if discovered:
        return discovered
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def ffmpeg_timeout_seconds() -> int:
    return max(60, int(os.getenv("RAG_VIDEO_FFMPEG_TIMEOUT_SECONDS", "1800")))


def audio_overlap_seconds() -> int:
    return max(0, int(os.getenv("RAG_VIDEO_AUDIO_OVERLAP_SECONDS", "10")))


def prepend_video_url_header(transcript_text: str, source_path: str) -> str:
    if not is_public_url(source_path):
        return transcript_text
    first_lines = "\n".join(transcript_text.splitlines()[:8]).lower()
    if "videourl:" in first_lines or "playbackurl:" in first_lines:
        return transcript_text
    return f"videoUrl: {source_path}\n\n{transcript_text}"


def resolve_local_video_path(source_path: str) -> Path:
    """解析 Java 传来的本地视频路径，兼容 Python 从仓库根或 ai-python 目录启动。"""
    raw_path = Path(source_path)
    if raw_path.is_absolute():
        return raw_path.resolve()
    cwd = Path.cwd().resolve()
    candidates = [
        cwd / source_path,
        cwd.parent / source_path,
        cwd / "backend-java" / source_path,
        cwd.parent / "backend-java" / source_path,
    ]
    return next((candidate.resolve() for candidate in candidates if candidate.exists()), candidates[0].resolve())


def emit_model_progress(
    progress_reporter: RagProgressReporter | None,
    message: str,
    *,
    percent: int,
    detail: str,
    stage_code: str = "parse.video",
) -> None:
    """把用户关心的模型调用事件同步到资料进度。"""
    if progress_reporter is None:
        return
    progress_reporter.emit(
        stage_code,
        message,
        current_step=3,
        total_steps=8,
        percent=percent,
        detail=detail,
    )


def emit_video_progress(
    progress_reporter: RagProgressReporter | None,
    stage_code: str,
    message: str,
    *,
    percent: int,
    detail: str,
) -> None:
    """把视频解析子阶段同步到前端可见进度。"""
    if progress_reporter is None:
        return
    progress_reporter.emit(
        stage_code,
        message,
        current_step=3,
        total_steps=8,
        percent=percent,
        detail=detail,
    )


def emit_filetrans_progress(
    progress_reporter: RagProgressReporter | None,
    client: BailianAsrClient,
    event: dict[str, Any],
) -> None:
    """把百炼 filetrans 异步提交、轮询和重试状态同步到前端。"""
    if progress_reporter is None:
        return
    phase = event.get("phase")
    attempt = event.get("attempt")
    max_attempts = event.get("maxAttempts")
    poll_index = event.get("pollIndex")
    max_polls = event.get("maxPolls")
    task_status = event.get("taskStatus") or "UNKNOWN"
    if phase == "submit":
        message = f"第 {attempt}/{max_attempts} 次提交 {client.filetrans_model} 异步 ASR 任务"
    elif phase == "submitted":
        message = f"已提交 {client.filetrans_model} 异步 ASR 任务，等待百炼处理"
    elif phase == "poll":
        message = f"正在等待 {client.filetrans_model} 异步 ASR：轮询 {poll_index}/{max_polls}，状态 {task_status}"
    elif phase == "download":
        message = f"{client.filetrans_model} 异步 ASR 已完成，正在下载转写结果"
    elif phase == "retry":
        message = f"{client.filetrans_model} 异步 ASR 第 {attempt}/{max_attempts} 次失败，准备重试第 {event.get('nextAttempt')} 次"
    elif phase == "failed":
        message = f"{client.filetrans_model} 异步 ASR 已失败，准备降级到字幕或同步 ASR"
    else:
        message = f"{client.filetrans_model} 异步 ASR 状态更新"
    detail_parts = [
        f"phase={phase}",
        f"attempt={attempt}/{max_attempts}",
        f"poll={poll_index}/{max_polls}" if poll_index and max_polls else "",
        f"taskStatus={task_status}" if phase == "poll" else "",
        f"taskId={event.get('taskId')}" if event.get("taskId") else "",
        f"error={event.get('errorMessage')}" if event.get("errorMessage") else "",
    ]
    progress_reporter.emit(
        "parse.video.asr",
        message,
        current_step=3,
        total_steps=8,
        percent=17 if phase in {"poll", "submitted"} else 16,
        detail="; ".join(part for part in detail_parts if part),
    )


def emit_ocr_retry_progress(
    progress_reporter: RagProgressReporter | None,
    event: dict,
    *,
    frame_index: int,
    total_frames: int,
) -> None:
    """把 OCR 重试失败同步到用户可见进度，避免长时间等待无反馈。"""
    if progress_reporter is None:
        return
    attempt = event.get("attempt")
    max_attempts = event.get("maxAttempts")
    next_attempt = event.get("nextAttempt")
    filename = event.get("filename")
    if next_attempt:
        message = f"第 {frame_index}/{total_frames} 帧 OCR 第 {attempt}/{max_attempts} 次错误，重试第 {next_attempt} 次"
    else:
        message = f"第 {frame_index}/{total_frames} 帧 OCR 第 {attempt}/{max_attempts} 次错误，已达到最大重试次数，等待降级处理"
    progress_reporter.emit(
        "parse.video",
        message,
        current_step=3,
        total_steps=8,
        percent=21,
        detail=f"{filename}: {event.get('errorMessage')}",
    )


def normalize_video_file_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix if suffix in VIDEO_FILE_TYPES else "video"


def seconds_to_timestamp(seconds: int) -> str:
    safe_seconds = max(0, seconds)
    hours = safe_seconds // 3600
    minutes = (safe_seconds % 3600) // 60
    second = safe_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{second:02d}"


def timestamp_to_seconds(value: str | None) -> int:
    if not value:
        return 0
    try:
        parts = [int(part) for part in value.replace(",", ".").split(".", 1)[0].split(":")]
    except ValueError:
        return 0
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    if len(parts) >= 3:
        hours, minutes, seconds = parts[-3:]
        return hours * 3600 + minutes * 60 + seconds
    return 0


def seconds_to_srt_timestamp(seconds: float) -> str:
    milliseconds_total = max(0, round(seconds * 1000))
    hours = milliseconds_total // 3_600_000
    minutes = (milliseconds_total % 3_600_000) // 60_000
    second = (milliseconds_total % 60_000) // 1000
    milliseconds = milliseconds_total % 1000
    return f"{hours:02d}:{minutes:02d}:{second:02d},{milliseconds:03d}"


def is_public_url(value: str | None) -> bool:
    return bool(value and re.match(r"^https?://", value.strip(), re.IGNORECASE))


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def stage_warning(stage: str, message: str) -> str:
    return f"{stage}: {message}"
