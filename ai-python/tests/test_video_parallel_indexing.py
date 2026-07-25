"""视频分片并发解析与聚合测试。"""

from __future__ import annotations

import threading
import time

from app.schemas.rag import DocumentBlock, ParseQuality
from app.services import video_parallel_indexing
from rag.core.models import ParsedBlockDocument
from video.chunking.video_processing import video_frame_ocr_enabled


class ConcurrentVideoParser:
    """用短暂等待模拟视频解析，记录共享队列是否由多个 Worker 消费。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.worker_names: set[str] = set()

    def parse_video_source(self, **kwargs) -> ParsedBlockDocument:
        """为每个媒体片段返回带相对时间戳的字幕 evidence。"""

        segment_index = int(str(kwargs["document_id"]).rsplit("-", 1)[-1])
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.worker_names.add(threading.current_thread().name)
        try:
            time.sleep(0.04)
            block = DocumentBlock(
                documentId=str(kwargs["document_id"]),
                blockId=f"segment-{segment_index}",
                fileType="mp4",
                blockType="text",
                startTime="00:00:05",
                endTime="00:00:10",
                sectionTitle="00:00:05 - 00:00:10",
                contentText=f"片段 {segment_index} 的课程内容",
                parseEngine="fake-video-parser",
                confidence=0.95,
                sourceTitle=str(kwargs["title"]),
                sourcePath=str(kwargs["source_path"]),
                metadata={
                    "evidenceChannel": "subtitle",
                    "startTime": "00:00:05",
                    "endTime": "00:00:10",
                },
            )
            return ParsedBlockDocument(
                blocks=[block],
                parser="fake-video-parser",
                status="READY",
                parse_quality=ParseQuality(score=1.0, nativeTextChars=len(block.contentText)),
            )
        finally:
            with self._lock:
                self.active -= 1

    def _finalize(self, document_id, blocks, parser, quality, warnings) -> ParsedBlockDocument:
        """模拟正式解析器的最终归一化入口，保留测试可断言的聚合结果。"""

        return ParsedBlockDocument(
            blocks=blocks,
            parser=parser,
            status="READY",
            parse_quality=quality.model_copy(update={"messages": warnings}),
            document_summary="并发视频摘要",
            warnings=warnings,
        )


def test_video_segments_are_claimed_by_two_workers_and_merged_on_original_timeline(tmp_path, monkeypatch) -> None:
    """四个媒体片段应由共享队列并发消费，并聚合到同一个资料索引输入。"""

    source_path = tmp_path / "merged-video.mp4"
    source_path.write_bytes(b"merged-video-source")
    tasks = []
    for index in range(1, 5):
        segment_path = tmp_path / f"segment-{index:04d}.mp4"
        segment_path.write_bytes(b"segment")
        tasks.append(
            video_parallel_indexing.VideoSegmentTask(
                index=index,
                path=segment_path,
                start_seconds=(index - 1) * 60,
                end_seconds=index * 60,
            )
        )

    monkeypatch.setenv("RAG_VIDEO_PARALLEL_SEGMENTS_ENABLED", "true")
    monkeypatch.setenv("RAG_VIDEO_PARALLEL_WORKERS", "2")
    monkeypatch.setattr(video_parallel_indexing, "segment_target_bytes", lambda: 1)
    monkeypatch.setattr(video_parallel_indexing, "ffmpeg_executable", lambda: "fake-ffmpeg")
    monkeypatch.setattr(video_parallel_indexing, "probe_media_duration_strict", lambda _path: 240.0)
    monkeypatch.setattr(
        video_parallel_indexing,
        "split_video_for_parallel_parse",
        lambda **_kwargs: (tasks, None),
    )

    parser = ConcurrentVideoParser()
    parsed = video_parallel_indexing.parse_video_source_with_worker_pool(
        parser_router=parser,
        document_id="material-101-staging-v1",
        title="并发处理课程视频",
        document_type="mp4",
        source="upload",
        user_id="42",
        visibility_scope="private",
        source_path=str(source_path),
        filename=source_path.name,
        content_type="video/mp4",
        high_precision=False,
    )

    assert parsed is not None
    assert parsed.parser.startswith("video-parallel-worker-pool+")
    assert len(parsed.blocks) == 4
    assert parser.max_active >= 2
    assert len(parser.worker_names) >= 2
    assert {block.documentId for block in parsed.blocks} == {"material-101-staging-v1"}
    assert {block.sourcePath for block in parsed.blocks} == {str(source_path.resolve())}
    assert {block.metadata["videoMediaSegmentIndex"] for block in parsed.blocks} == {1, 2, 3, 4}
    assert parsed.blocks[0].startTime == "00:00:05"
    assert parsed.blocks[1].startTime == "00:01:05"
    assert parsed.blocks[1].metadata["startTime"] == "00:01:05"


def test_parallel_video_path_can_be_disabled_for_original_parser_fallback(tmp_path, monkeypatch) -> None:
    """显式关闭并发切片时必须返回 None，让调用方沿用整视频解析路径。"""

    source_path = tmp_path / "merged-video.mp4"
    source_path.write_bytes(b"merged-video-source")
    monkeypatch.setenv("RAG_VIDEO_PARALLEL_SEGMENTS_ENABLED", "false")

    assert video_parallel_indexing.parse_video_source_with_worker_pool(
        parser_router=ConcurrentVideoParser(),
        document_id="material-102-staging-v1",
        title="回落视频",
        document_type="mp4",
        source="upload",
        user_id="42",
        visibility_scope="private",
        source_path=str(source_path),
        filename=source_path.name,
        content_type="video/mp4",
        high_precision=False,
    ) is None


def test_sidecar_srt_is_split_next_to_video_segments(tmp_path) -> None:
    """原视频同名字幕应写到每个媒体片段旁，并转换为片段相对时间轴。"""

    source_path = tmp_path / "lesson.mp4"
    source_path.write_bytes(b"video")
    source_path.with_suffix(".srt").write_text(
        (
            "1\n"
            "00:00:05,000 --> 00:00:08,000\n"
            "第一段课程\n\n"
            "2\n"
            "00:01:05,000 --> 00:01:10,000\n"
            "第二段课程\n"
        ),
        encoding="utf-8",
    )
    tasks = [
        video_parallel_indexing.VideoSegmentTask(index=1, path=tmp_path / "segment-0000.mp4", start_seconds=0, end_seconds=60),
        video_parallel_indexing.VideoSegmentTask(index=2, path=tmp_path / "segment-0001.mp4", start_seconds=60, end_seconds=120),
    ]
    for task in tasks:
        task.path.write_bytes(b"segment")

    warning = video_parallel_indexing.write_segment_sidecar_subtitles(source_path, tasks)

    assert warning is None
    first = (tmp_path / "segment-0000.srt").read_text(encoding="utf-8")
    second = (tmp_path / "segment-0001.srt").read_text(encoding="utf-8")
    assert "00:00:05,000 --> 00:00:08,000" in first
    assert "第一段课程" in first
    assert "00:00:05,000 --> 00:00:10,000" in second
    assert "第二段课程" in second


def test_video_frame_ocr_can_be_disabled_for_subtitle_benchmark(monkeypatch) -> None:
    """可信字幕性能基准可关闭外部关键帧 OCR，默认仍保持开启。"""
    monkeypatch.delenv("RAG_VIDEO_FRAME_OCR_ENABLED", raising=False)
    assert video_frame_ocr_enabled() is True
    monkeypatch.setenv("RAG_VIDEO_FRAME_OCR_ENABLED", "false")
    assert video_frame_ocr_enabled() is False


def test_parallel_parser_name_stays_within_rag_document_varchar_limit() -> None:
    """不同媒体片段走不同解析组合时，汇总 parser 名称仍可写入 VARCHAR(80)。"""

    parser = video_parallel_indexing.build_parallel_parser_name(
        [
            "video+bailian-asr+keyframe-ocr+ppt-flip-detect",
            "video+sidecar-subtitle+keyframe-ocr+ppt-flip-detect",
            "video+bailian-asr+keyframe-ocr",
        ]
    )

    assert parser.startswith("video-parallel-worker-pool+")
    assert parser.endswith("+2more")
    assert len(parser) <= 80
