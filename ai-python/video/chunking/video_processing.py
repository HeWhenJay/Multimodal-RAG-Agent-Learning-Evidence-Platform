from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from video.asr.bailian_asr import BailianAsrClient
from video.ocr.bailian_ocr import BailianOcrClient
from app.schemas.rag import DocumentBlock
from rag.process_logger import logged_rag_method, process_event


VIDEO_FILE_TYPES = {"mp4", "mov", "m4v", "webm", "mkv", "avi"}


@dataclass(frozen=True)
class FrameImage:
    time_seconds: int
    path: Path
    trigger: str = "interval"
    diff_score: float | None = None
    slide_index: int | None = None


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
        )


@logged_rag_method("parse.video", "process_video_source", "处理已保存视频来源")
def process_video_source(
    *,
    source_path: str,
    filename: str,
    document_id: str,
    source_title: str,
    ocr_client: BailianOcrClient,
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
        )
    if source_path.startswith("oss://"):
        warning = stage_warning("video.source", "当前 sourcePath 是 oss:// 私有地址，Python 无法直接读取，请配置公开 OSS/CDN URL")
        return VideoProcessingResult(
            frame_blocks=[fallback_video_metadata_block(document_id, filename, source_title, source_path)],
            parser="video-metadata-fallback",
            warnings=[warning, stage_warning("video.fallback", "视频未生成可检索字幕或关键帧 OCR 文本")],
        )
    local_path = Path(source_path)
    if not local_path.is_absolute():
        candidates = [
            Path.cwd().resolve() / source_path,
            Path.cwd().resolve().parent / source_path,
        ]
        local_path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    local_path = local_path.resolve()
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
) -> VideoProcessingResult:
    """统一处理本地文件或 URL 视频输入，生成字幕、关键帧 OCR 和视频元数据。"""
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="rag-video-work-") as tmp:
        tmp_dir = Path(tmp)
        transcript_text, asr_warnings = transcribe_video_input(video_input, tmp_dir, source_path)
        warnings.extend(asr_warnings)

        frames, frame_warnings = extract_keyframes(video_input, tmp_dir)
        warnings.extend(frame_warnings)
        frame_blocks, ocr_warnings = ocr_video_frames(
            frames=frames,
            document_id=document_id,
            file_type=normalize_video_file_type(filename),
            source_title=source_title,
            source_path=source_path,
            ocr_client=ocr_client,
        )
        warnings.extend(ocr_warnings)

    if transcript_text and source_path:
        transcript_text = prepend_video_url_header(transcript_text, source_path)

    if not transcript_text and not frame_blocks:
        warnings.append(stage_warning("video.fallback", "视频未生成可检索字幕或关键帧 OCR 文本"))
        frame_blocks = [fallback_video_metadata_block(document_id, filename, source_title, source_path)]

    parser_parts = ["video"]
    if transcript_text:
        parser_parts.append("bailian-asr")
    if frame_blocks:
        parser_parts.append("keyframe-ocr")
    if any(block.metadata.get("frameTrigger") == "ppt_flip" for block in frame_blocks):
        parser_parts.append("ppt-flip-detect")
    return VideoProcessingResult(
        transcript_text=transcript_text,
        frame_blocks=frame_blocks,
        parser="+".join(parser_parts),
        warnings=warnings,
    )


@logged_rag_method("parse.video.asr", "transcribe_video_input", "执行视频 ASR 转写")
def transcribe_video_input(video_input: str, tmp_dir: Path, source_path: str | None) -> tuple[str, list[str]]:
    """对视频输入执行 ASR；公开视频优先 filetrans，本地视频走重叠音频分段。"""
    client = BailianAsrClient()
    warnings: list[str] = []
    if is_public_url(source_path):
        transcript_text, filetrans_warnings = client.transcribe_source_url(str(source_path))
        warnings.extend(stage_warning("video.asr", warning) for warning in filetrans_warnings)
        if transcript_text:
            return transcript_text, warnings

    segments, segment_warnings = extract_audio_segments(video_input, tmp_dir)
    warnings.extend(segment_warnings)
    if not segments:
        return "", warnings

    cues: list[TranscriptCue] = []
    plain_parts: list[str] = []
    for segment_index, segment in enumerate(segments, start=1):
        segment_text, asr_warnings = client.transcribe_audio_file(segment.path)
        warnings.extend(stage_warning(f"video.asr.segment[{segment_index}]", warning) for warning in asr_warnings)
        if not segment_text:
            continue
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
        return cues_to_srt(merged), warnings
    if plain_parts:
        duration = probe_media_duration(video_input)
        return estimate_srt_from_transcript(" ".join(plain_parts), duration), warnings
    return "", warnings


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
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=ffmpeg_timeout_seconds())
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
def extract_keyframes(video_input: str, tmp_dir: Path) -> tuple[list[FrameImage], list[str]]:
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return [], [stage_warning("video.frame.extract", "未找到 FFmpeg，跳过视频关键帧抽取")]
    sample_interval = max(1, int(os.getenv("RAG_VIDEO_FRAME_SAMPLE_INTERVAL_SECONDS", "5")))
    keep_interval = max(sample_interval, int(os.getenv("RAG_VIDEO_FRAME_INTERVAL_SECONDS", "30")))
    max_frames = max(1, int(os.getenv("RAG_VIDEO_MAX_FRAMES", "20")))
    max_candidates = max(max_frames, int(os.getenv("RAG_VIDEO_FRAME_MAX_CANDIDATES", str(max_frames * 6))))
    frame_dir = tmp_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
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
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=ffmpeg_timeout_seconds())
    except Exception as exc:
        return [], [stage_warning("video.frame.extract", f"FFmpeg 抽取关键帧失败: {exc}")]
    candidates = []
    for index, path in enumerate(sorted(frame_dir.glob("frame-*.jpg"))):
        candidates.append(FrameImage(time_seconds=index * sample_interval, path=path, trigger="candidate"))
    if not candidates:
        return [], [stage_warning("video.frame.extract", "FFmpeg 未生成关键帧图片")]
    return select_ppt_slide_frames(
        candidates,
        keep_interval_seconds=keep_interval,
        max_frames=max_frames,
    )


@logged_rag_method("parse.video.frame", "select_ppt_slide_frames", "识别 PPT 翻页关键帧")
def select_ppt_slide_frames(
    candidates: list[FrameImage],
    *,
    keep_interval_seconds: int,
    max_frames: int,
) -> tuple[list[FrameImage], list[str]]:
    """根据画面差异筛选 PPT 翻页关键帧，同时保留固定间隔兜底帧。"""
    if not candidates:
        return [], []
    warnings: list[str] = []
    threshold = float(os.getenv("RAG_VIDEO_PPT_FLIP_DIFF_THRESHOLD", "0.08"))
    selected = [
        FrameImage(
            time_seconds=candidates[0].time_seconds,
            path=candidates[0].path,
            trigger="initial_slide",
            diff_score=0.0,
            slide_index=1,
        )
    ]
    last_selected = candidates[0]
    last_slide_index = 1
    for candidate in candidates[1:]:
        if len(selected) >= max_frames:
            break
        diff_score = None
        try:
            diff_score = image_difference_score(last_selected.path, candidate.path)
        except Exception as exc:
            warnings.append(stage_warning("video.slide_detect", f"{candidate.path.name} 画面差异计算失败: {exc}"))
        is_flip = diff_score is not None and diff_score >= threshold
        is_interval = candidate.time_seconds - selected[-1].time_seconds >= keep_interval_seconds
        if is_flip or is_interval:
            last_slide_index += 1 if is_flip else 0
            selected.append(
                FrameImage(
                    time_seconds=candidate.time_seconds,
                    path=candidate.path,
                    trigger="ppt_flip" if is_flip else "interval",
                    diff_score=diff_score,
                    slide_index=last_slide_index,
                )
            )
            last_selected = candidate
    return selected, warnings


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
    ffprobe = os.getenv("FFPROBE_COMMAND") or shutil.which("ffprobe")
    if not ffprobe:
        return 60.0
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
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
        return max(1.0, float(result.stdout.strip()))
    except Exception:
        return 60.0


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
) -> tuple[list[DocumentBlock], list[str]]:
    blocks: list[DocumentBlock] = []
    warnings: list[str] = []
    video_url = source_path if is_public_url(source_path) else None
    for index, frame in enumerate(frames, start=1):
        image_bytes = frame.path.read_bytes()
        try:
            ocr_result = ocr_client.recognize_image_bytes(image_bytes=image_bytes, filename=frame.path.name)
        except Exception as exc:
            warnings.append(stage_warning(f"video.frame_ocr[{index}]", f"百炼 OCR 调用异常: {exc}"))
            ocr_result = None
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
            **(ocr_result.metadata if ocr_result else {}),
        }
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
        block_start = timestamp_to_seconds(block.startTime)
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
        end_time = group[-1].startTime or start_time
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
    result = []
    for block in frame_blocks:
        seconds = timestamp_to_seconds(block.startTime)
        if start_seconds <= seconds <= end_seconds:
            result.append(block)
    return result[:3]


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
    return shutil.which("ffmpeg")


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
