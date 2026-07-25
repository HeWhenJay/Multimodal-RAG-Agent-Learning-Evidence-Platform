"""本地真实视频 RAG 集成测试，默认不依赖或提交大视频文件。"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app.schemas.rag import QueryRequest
from rag.loaders.document_parsers import DocumentParserRouter
from rag.retrievers.retrieval import InMemoryRagStore
from video.chunking import video_processing


REAL_VIDEO_PATH_ENV = "RAG_REAL_VIDEO_TEST_PATH"
REAL_VIDEO_QUERY_ENV = "RAG_REAL_VIDEO_TEST_QUERY"


@pytest.fixture
def real_video_path() -> Path:
    """读取显式配置的本地真实视频，缺失时跳过以保持 CI 可移植。"""
    configured = os.getenv(REAL_VIDEO_PATH_ENV, "").strip()
    if not configured:
        pytest.skip(f"未设置 {REAL_VIDEO_PATH_ENV}，跳过本地真实视频集成测试")
    path = Path(configured)
    if not path.is_file():
        pytest.skip(f"真实视频文件不存在或不可读取: {path}")
    return path


def resolve_ffprobe(ffmpeg: str) -> str | None:
    """优先使用 FFmpeg 同目录的 ffprobe，兼容 Windows 本地安装。"""
    ffmpeg_path = Path(ffmpeg)
    sibling = ffmpeg_path.with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
    if sibling.is_file():
        return str(sibling)
    return shutil.which("ffprobe")


def test_real_video_sidecar_subtitle_indexes_timestamped_evidence(real_video_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """真实长视频应通过同名字幕进入递归切块和检索，并保留时间戳 evidence。"""
    ffmpeg = video_processing.ffmpeg_executable()
    if not ffmpeg:
        pytest.skip("未找到 FFmpeg，无法抽取真实视频的内嵌字幕")
    ffprobe = resolve_ffprobe(ffmpeg)
    if not ffprobe:
        pytest.skip("未找到 ffprobe，无法确认真实视频时长")

    monkeypatch.setenv("FFMPEG_COMMAND", ffmpeg)
    monkeypatch.setenv("FFPROBE_COMMAND", ffprobe)
    monkeypatch.setenv("RAG_ASR_PROVIDER", "local")
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "hash")
    monkeypatch.setenv("RAG_QUERY_EXPANSION_PROVIDER", "local")
    monkeypatch.setenv("RAG_CONSOLE_PROGRESS_ENABLED", "false")
    monkeypatch.setenv("RAG_CONSOLE_PROCESS_ENABLED", "false")

    duration = video_processing.probe_media_duration_strict(real_video_path)
    assert duration is not None and duration >= 30 * 60
    assert real_video_path.stat().st_size >= 64 * 1024 * 1024

    # 本集成测试聚焦内嵌字幕 RAG，关键帧 OCR 另由独立视频 OCR 测试覆盖。
    monkeypatch.setattr(video_processing, "extract_keyframes", lambda *_args, **_kwargs: ([], []))
    parser = DocumentParserRouter()
    parsed = parser.parse_video_source(
        document_id="real-video-finetuning-data",
        title="大模型微调数据准备真实视频",
        document_type=real_video_path.suffix.lstrip(".").lower(),
        source="local-video-integration-test",
        user_id="real-video-user",
        visibility_scope="private",
        source_path=str(real_video_path),
        filename=real_video_path.name,
    )

    subtitle_blocks = [block for block in parsed.blocks if block.metadata.get("evidenceChannel") == "subtitle"]
    assert parsed.status == "READY"
    assert "subtitle" in parsed.parser
    assert subtitle_blocks
    assert all(block.startTime and block.endTime for block in subtitle_blocks)
    assert all(block.sourcePath == str(real_video_path) for block in subtitle_blocks)

    store = InMemoryRagStore()
    indexed = store.index_blocks(
        document_id="real-video-finetuning-data",
        title="大模型微调数据准备真实视频",
        document_type=real_video_path.suffix.lstrip(".").lower(),
        source="local-video-integration-test",
        user_id="real-video-user",
        visibility_scope="private",
        language="zh-CN",
        parser=parsed.parser,
        blocks=parsed.blocks,
        parse_quality=parsed.parse_quality,
        status=parsed.status,
        source_path=str(real_video_path),
    )
    assert indexed.status == "READY"
    assert indexed.chunkCount > 0

    response = store.query(
        QueryRequest(
            question=os.getenv(REAL_VIDEO_QUERY_ENV, "大模型微调技术工作流").strip(),
            topK=3,
            metadataFilter={"userId": "real-video-user", "visibilityScope": "private"},
        )
    )
    assert response.answerStatus == "ANSWERED"
    assert response.evidences
    assert any(evidence.startTime and evidence.endTime for evidence in response.evidences)
    assert all(evidence.documentId == "real-video-finetuning-data" for evidence in response.evidences)
