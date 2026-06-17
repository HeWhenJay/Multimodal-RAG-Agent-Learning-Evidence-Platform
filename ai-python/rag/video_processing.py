from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from rag.bailian_asr import BailianAsrClient
from rag.bailian_ocr import BailianOcrClient
from schemas.rag import DocumentBlock


VIDEO_FILE_TYPES = {"mp4", "mov", "m4v", "webm", "mkv", "avi"}


@dataclass(frozen=True)
class FrameImage:
    time_seconds: int
    path: Path


@dataclass(frozen=True)
class VideoProcessingResult:
    transcript_text: str = ""
    frame_blocks: list[DocumentBlock] = field(default_factory=list)
    parser: str = "video-processor"
    warnings: list[str] = field(default_factory=list)


def process_video_bytes(
    *,
    content: bytes,
    filename: str,
    document_id: str,
    source_title: str,
    source_path: str | None,
    ocr_client: BailianOcrClient,
) -> VideoProcessingResult:
    """处理原始视频：抽音频做 ASR，抽关键帧做 OCR，并保留统一视频定位元数据。"""
    warnings: list[str] = []
    suffix = Path(filename).suffix or ".mp4"
    with tempfile.TemporaryDirectory(prefix="rag-video-") as tmp:
        tmp_dir = Path(tmp)
        video_path = tmp_dir / f"input{suffix}"
        video_path.write_bytes(content)

        audio_path, audio_warnings = extract_audio_track(video_path, tmp_dir)
        warnings.extend(audio_warnings)
        transcript_text = ""
        if audio_path:
            transcript_text, asr_warnings = BailianAsrClient().transcribe_audio_file(audio_path, source_url=source_path)
            warnings.extend(asr_warnings)
            if transcript_text and not transcript_has_timestamps(transcript_text):
                transcript_text = estimate_srt_from_transcript(transcript_text, probe_media_duration(video_path))
                warnings.append("百炼同步 ASR 未返回时间戳，已按视频时长生成估算字幕时间段")

        frames, frame_warnings = extract_keyframes(video_path, tmp_dir)
        warnings.extend(frame_warnings)
        frame_blocks = ocr_video_frames(
            frames=frames,
            document_id=document_id,
            file_type=normalize_video_file_type(filename),
            source_title=source_title,
            source_path=source_path,
            ocr_client=ocr_client,
        )

    if transcript_text and source_path:
        transcript_text = prepend_video_url_header(transcript_text, source_path)

    if not transcript_text and not frame_blocks:
        warnings.append("视频未生成可检索字幕或关键帧 OCR 文本")
        frame_blocks = [fallback_video_metadata_block(document_id, filename, source_title, source_path)]

    parser_parts = ["video"]
    if transcript_text:
        parser_parts.append("bailian-asr")
    if frame_blocks:
        parser_parts.append("keyframe-ocr")
    return VideoProcessingResult(
        transcript_text=transcript_text,
        frame_blocks=frame_blocks,
        parser="+".join(parser_parts),
        warnings=warnings,
    )


def extract_audio_track(video_path: Path, tmp_dir: Path) -> tuple[Path | None, list[str]]:
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return None, ["未找到 FFmpeg，跳过视频音频轨提取"]
    audio_path = tmp_dir / "audio.wav"
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        str(audio_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
    except Exception as exc:
        return None, [f"FFmpeg 提取音频失败: {exc}"]
    if not audio_path.exists() or audio_path.stat().st_size == 0:
        return None, ["FFmpeg 未生成可用音频文件"]
    return audio_path, []


def extract_keyframes(video_path: Path, tmp_dir: Path) -> tuple[list[FrameImage], list[str]]:
    ffmpeg = ffmpeg_executable()
    if not ffmpeg:
        return [], ["未找到 FFmpeg，跳过视频关键帧抽取"]
    interval = max(1, int(os.getenv("RAG_VIDEO_FRAME_INTERVAL_SECONDS", "30")))
    max_frames = max(1, int(os.getenv("RAG_VIDEO_MAX_FRAMES", "20")))
    frame_dir = tmp_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frame_pattern = frame_dir / "frame-%04d.jpg"
    command = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"fps=1/{interval}",
        "-frames:v",
        str(max_frames),
        str(frame_pattern),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=180)
    except Exception as exc:
        return [], [f"FFmpeg 抽取关键帧失败: {exc}"]
    frames = []
    for index, path in enumerate(sorted(frame_dir.glob("frame-*.jpg"))):
        frames.append(FrameImage(time_seconds=index * interval, path=path))
    if not frames:
        return [], ["FFmpeg 未生成关键帧图片"]
    return frames, []


def probe_media_duration(video_path: Path) -> float:
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
        str(video_path),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
        return max(1.0, float(result.stdout.strip()))
    except Exception:
        return 60.0


def transcript_has_timestamps(text: str) -> bool:
    return "-->" in text or bool(re.search(r"\d{1,2}:\d{2}(?::\d{2})?", text))


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


def ocr_video_frames(
    *,
    frames: list[FrameImage],
    document_id: str,
    file_type: str,
    source_title: str,
    source_path: str | None,
    ocr_client: BailianOcrClient,
) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    video_url = source_path if is_public_url(source_path) else None
    for index, frame in enumerate(frames, start=1):
        image_bytes = frame.path.read_bytes()
        ocr_result = ocr_client.recognize_image_bytes(image_bytes=image_bytes, filename=frame.path.name)
        text = normalize_text(ocr_result.text) or tesseract_frame_text(image_bytes)
        if not text:
            continue
        start_time = seconds_to_timestamp(frame.time_seconds)
        metadata = {
            "frameIndex": index,
            "frameTime": start_time,
            "startTime": start_time,
            "mediaType": "video",
            "evidenceChannel": "frame_ocr",
            **ocr_result.metadata,
        }
        if video_url:
            metadata["videoUrl"] = video_url
        blocks.append(
            DocumentBlock(
                documentId=document_id,
                blockId=f"{document_id}-frame-{index}",
                fileType=file_type,
                blockType="image",
                startTime=start_time,
                sectionTitle=f"视频画面 {start_time}",
                contentText=f"视频画面 {start_time}\n{text}",
                assetPath=source_path,
                parseEngine=ocr_result.parser or "video-frame-ocr",
                confidence=max(ocr_result.confidence, 0.72),
                sourceTitle=source_title,
                sourcePath=source_path,
                metadata=metadata,
            )
        )
    return blocks


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


def tesseract_frame_text(image_bytes: bytes) -> str:
    try:
        from io import BytesIO

        from PIL import Image
        import pytesseract

        image = Image.open(BytesIO(image_bytes))
        return normalize_text(pytesseract.image_to_string(image, lang=os.getenv("OCR_LANG", "chi_sim+eng")))
    except Exception:
        return ""


def ffmpeg_executable() -> str | None:
    configured = os.getenv("FFMPEG_COMMAND")
    if configured:
        return configured
    return shutil.which("ffmpeg")


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
