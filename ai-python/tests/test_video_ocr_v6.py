import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

from app.schemas.rag import DocumentBlock
from video.chunking import video_processing as vp
from video.chunking.video_dedup import dedupe_video_frame_blocks
from video.chunking.video_processing import (
    FrameOcrMicrobatchDispatcher,
    FrameImage,
    build_video_segment_summary_blocks,
    extract_keyframes,
    frames_between,
    ocr_video_frames,
    select_ppt_slide_frames,
)
from video.ocr.bailian_ocr import OcrResult


def make_frame(path: Path, text: str, *, fill: str = "white") -> Path:
    """生成带少量文字差异的测试图片，模拟 PPT/录屏帧。"""
    image = Image.new("RGB", (180, 100), fill)
    draw = ImageDraw.Draw(image)
    draw.text((10, 40), text, fill="black")
    image.save(path)
    return path


def frame_block(
    time_text: str,
    text: str,
    *,
    trigger: str = "interval",
    visual_group_id: str | None = None,
    visual_ranges: list[dict[str, str]] | None = None,
    visual_times: list[str] | None = None,
    confidence: float = 0.9,
) -> DocumentBlock:
    metadata = {
        "mediaType": "video",
        "evidenceChannel": "frame_ocr",
        "frameTrigger": trigger,
        "frameTime": time_text,
        "timeRanges": [{"startTime": time_text, "endTime": time_text}],
        "sourceFrameTimes": [time_text],
    }
    if visual_group_id:
        metadata["visualGroupId"] = visual_group_id
    if visual_ranges:
        metadata["visualTimeRanges"] = visual_ranges
    if visual_times:
        metadata["visualSourceFrameTimes"] = visual_times
    return DocumentBlock(
        documentId="doc-video-v6",
        blockId=f"doc-video-v6-frame-{time_text.replace(':', '')}",
        fileType="mp4",
        blockType="image",
        startTime=time_text,
        sectionTitle=f"视频画面 {time_text}",
        contentText=f"视频画面 {time_text}\n{text}",
        parseEngine="bailian-qwen-ocr",
        confidence=confidence,
        sourceTitle="V6 视频",
        sourcePath="uploads/rag/v6.mp4",
        metadata=metadata,
    )


def test_full_scan_uses_dynamic_interval_and_reaches_video_tail(tmp_path, monkeypatch):
    captured = {}

    def fake_run(command, check, capture_output, text, encoding, errors, timeout):
        assert encoding == "utf-8"
        assert errors == "replace"
        captured["command"] = command
        pattern = Path(command[-1])
        pattern.parent.mkdir(parents=True, exist_ok=True)
        for index in range(1, 721):
            make_frame(pattern.parent / f"frame-{index:04d}.jpg", str(index))

    monkeypatch.setenv("RAG_VIDEO_FRAME_SCAN_MODE", "full")
    monkeypatch.setenv("RAG_VIDEO_FRAME_SAMPLE_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("RAG_VIDEO_FRAME_TARGET_CANDIDATES", "360")
    monkeypatch.setenv("RAG_VIDEO_FRAME_MAX_CANDIDATES", "720")
    monkeypatch.setenv("RAG_VIDEO_MAX_FRAMES", "20")
    monkeypatch.setattr(vp, "ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(vp, "probe_media_duration_strict", lambda _video: 10800.0)
    monkeypatch.setattr(vp.subprocess, "run", fake_run)
    monkeypatch.setattr(vp, "select_ppt_slide_frames", lambda candidates, keep_interval_seconds, max_frames: (candidates, []))

    frames, warnings = extract_keyframes("course.mp4", tmp_path)

    assert warnings == []
    assert "fps=1/30" in captured["command"]
    assert len(frames) == 720
    assert frames[-1].time_seconds == 719 * 30


def test_auto_scan_fallbacks_to_prefix_with_warning(tmp_path, monkeypatch):
    def fake_prefix(video_input, tmp_dir, *, ffmpeg, sample_interval, max_candidates):
        path = make_frame(tmp_dir / "prefix.jpg", "prefix")
        return [FrameImage(time_seconds=0, path=path)], []

    monkeypatch.setenv("RAG_VIDEO_FRAME_SCAN_MODE", "auto")
    monkeypatch.setattr(vp, "ffmpeg_executable", lambda: "ffmpeg")
    monkeypatch.setattr(vp, "probe_media_duration_strict", lambda _video: None)
    monkeypatch.setattr(vp, "extract_prefix_frame_candidates", fake_prefix)

    frames, warnings = extract_keyframes("course.mp4", tmp_path)

    assert frames
    assert any("降级为 prefix" in warning for warning in warnings)


def test_stage_b_selection_covers_tail_when_events_exceed_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_DEDUP_ENABLED", "false")
    monkeypatch.setenv("RAG_VIDEO_FRAME_MIN_INTERVAL_SECONDS", "0")
    frames = [FrameImage(time_seconds=index * 60, path=make_frame(tmp_path / f"frame-{index:04d}.jpg", str(index))) for index in range(30)]

    selected, warnings = select_ppt_slide_frames(frames, keep_interval_seconds=60, max_frames=5)

    assert warnings == []
    assert len(selected) == 5
    assert selected[0].time_seconds == 0
    assert selected[-1].time_seconds >= 25 * 60


def test_stage_b_default_has_no_twenty_frame_limit(tmp_path, monkeypatch):
    monkeypatch.delenv("RAG_VIDEO_MAX_FRAMES", raising=False)
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_DEDUP_ENABLED", "false")
    monkeypatch.setenv("RAG_VIDEO_FRAME_MIN_INTERVAL_SECONDS", "0")
    frames = [FrameImage(time_seconds=index * 60, path=make_frame(tmp_path / f"frame-no-limit-{index:04d}.jpg", str(index))) for index in range(30)]

    selected, warnings = select_ppt_slide_frames(frames, keep_interval_seconds=60, max_frames=None)

    assert warnings == []
    assert len(selected) == 30
    assert selected[-1].time_seconds == 29 * 60


def test_slide_detection_reports_frontend_progress(tmp_path, monkeypatch):
    from rag.observability.progress import RagProgressReporter

    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_DEDUP_ENABLED", "false")
    monkeypatch.setenv("RAG_VIDEO_FRAME_MIN_INTERVAL_SECONDS", "0")
    reporter = RagProgressReporter(document_id="doc-video-progress", persist=False)
    frames = [
        FrameImage(time_seconds=0, path=make_frame(tmp_path / "progress-1.jpg", "第一页")),
        FrameImage(time_seconds=60, path=make_frame(tmp_path / "progress-2.jpg", "第二页", fill="black")),
    ]

    selected, warnings = select_ppt_slide_frames(
        frames,
        keep_interval_seconds=30,
        max_frames=2,
        progress_reporter=reporter,
    )

    assert warnings == []
    assert selected
    assert any(event.stageCode == "parse.video.slide_detect" for event in reporter.events)
    assert "翻页命中" in reporter.events[-1].message


def test_visual_only_ranges_do_not_participate_in_frames_between():
    block = frame_block(
        "00:00:05",
        "代表页：RAG-Fusion",
        visual_ranges=[{"startTime": "00:01:20", "endTime": "00:01:20"}],
        visual_times=["00:01:20"],
    )

    assert frames_between([block], 80, 80) == []
    assert frames_between([block], 5, 5) == [block]


def test_verification_same_text_enters_time_ranges_and_segment_summary():
    blocks = [
        frame_block("00:00:05", "RAG-Fusion 使用 RRF 融合多路召回。", visual_group_id="visual-0001"),
        frame_block("00:01:20", "RAG-Fusion 使用 RRF 融合多路召回。", trigger="visual_verification", visual_group_id="visual-0001"),
    ]

    deduped, _stats = dedupe_video_frame_blocks(blocks, "doc-video-v6")
    merged = deduped[0]
    assert {"startTime": "00:01:20", "endTime": "00:01:20"} in merged.metadata["timeRanges"]

    transcript = DocumentBlock(
        documentId="doc-video-v6",
        blockId="doc-video-v6-subtitle",
        fileType="mp4",
        blockType="text",
        startTime="00:01:10",
        endTime="00:01:30",
        sectionTitle="00:01:10 - 00:01:30",
        contentText="这里继续讲 RAG-Fusion。",
        parseEngine="subtitle",
        sourceTitle="V6 视频",
        sourcePath="uploads/rag/v6.mp4",
        metadata={"mediaType": "video", "evidenceChannel": "subtitle"},
    )
    summaries, warnings = build_video_segment_summary_blocks(
        document_id="doc-video-v6",
        file_type="mp4",
        source_title="V6 视频",
        source_path="uploads/rag/v6.mp4",
        transcript_blocks=[transcript],
        frame_blocks=deduped,
    )

    assert warnings == []
    assert "画面线索" in summaries[0].contentText


def test_verification_different_text_stays_independent_and_original_not_extended():
    blocks = [
        frame_block("00:00:05", "实验结果 accuracy = 91.2%", visual_group_id="visual-0001"),
        frame_block("00:01:20", "实验结果 accuracy = 81.2%", trigger="visual_verification", visual_group_id="visual-0001"),
    ]

    deduped, stats = dedupe_video_frame_blocks(blocks, "doc-video-v6")

    assert stats["dedupRemovedCount"] == 0
    assert len(deduped) == 2
    early = next(block for block in deduped if block.startTime == "00:00:05")
    assert {"startTime": "00:01:20", "endTime": "00:01:20"} not in early.metadata["timeRanges"]


def test_low_hash_and_diff_changed_number_is_promoted_to_verification(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_DEDUP_ENABLED", "true")
    monkeypatch.setenv("RAG_VIDEO_FRAME_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_VERIFY_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_VERIFICATION_RATIO", "1")
    monkeypatch.setenv("RAG_VIDEO_FRAME_MAX_VERIFICATIONS_PER_VISUAL_GROUP", "2")
    frames = [
        FrameImage(time_seconds=0, path=make_frame(tmp_path / "frame-0001.jpg", "accuracy 91.2")),
        FrameImage(time_seconds=60, path=make_frame(tmp_path / "frame-0002.jpg", "accuracy 81.2")),
    ]
    monkeypatch.setattr(vp, "visual_hash_for_image", lambda _path: "0" * 16)
    monkeypatch.setattr(vp, "image_difference_score", lambda _left, _right: 0.001)

    selected, warnings = select_ppt_slide_frames(frames, keep_interval_seconds=600, max_frames=2)

    assert warnings == []
    assert [frame.trigger for frame in selected] == ["initial_slide", "visual_verification"]


def test_visual_verification_per_group_cap_records_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_DEDUP_ENABLED", "true")
    monkeypatch.setenv("RAG_VIDEO_FRAME_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_VERIFY_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_VERIFICATION_RATIO", "1")
    monkeypatch.setenv("RAG_VIDEO_FRAME_MAX_VERIFICATIONS_PER_VISUAL_GROUP", "2")
    frames = [FrameImage(time_seconds=index * 60, path=make_frame(tmp_path / f"frame-{index:04d}.jpg", f"value {index}")) for index in range(5)]
    monkeypatch.setattr(vp, "visual_hash_for_image", lambda _path: "0" * 16)
    monkeypatch.setattr(vp, "image_difference_score", lambda _left, _right: 0.001)

    selected, warnings = select_ppt_slide_frames(frames, keep_interval_seconds=600, max_frames=5)

    assert [frame.trigger for frame in selected].count("visual_verification") == 2
    assert any("visualVerificationPerGroupLimit=2" in warning for warning in warnings)
    assert selected[0].visual_source_frame_times


def test_visual_verification_global_budget_records_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_DEDUP_ENABLED", "true")
    monkeypatch.setenv("RAG_VIDEO_FRAME_MIN_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_VERIFY_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_VERIFICATION_RATIO", "0.25")
    monkeypatch.setenv("RAG_VIDEO_FRAME_MAX_VERIFICATIONS_PER_VISUAL_GROUP", "10")
    frames = [FrameImage(time_seconds=index * 60, path=make_frame(tmp_path / f"frame-budget-{index:04d}.jpg", f"value {index}")) for index in range(6)]
    monkeypatch.setattr(vp, "visual_hash_for_image", lambda _path: "0" * 16)
    monkeypatch.setattr(vp, "image_difference_score", lambda _left, _right: 0.001)

    selected, warnings = select_ppt_slide_frames(frames, keep_interval_seconds=600, max_frames=4)

    assert [frame.trigger for frame in selected].count("visual_verification") == 1
    assert any("visualVerificationBudget=1" in warning for warning in warnings)


def test_frames_between_sorts_more_than_three_matches_by_quality():
    blocks = [
        frame_block("00:01:00", "短", trigger="interval", confidence=0.7),
        frame_block("00:01:28", "靠近中心但普通触发", trigger="interval", confidence=0.7),
        frame_block("00:01:32", "靠近中心且翻页触发，文字更完整", trigger="ppt_flip", confidence=0.95),
        frame_block("00:02:00", "较远", trigger="initial_slide", confidence=0.9),
    ]

    matched = frames_between(blocks, 60, 120)

    assert len(matched) == 3
    assert matched[0].startTime == "00:01:32"
    assert "00:01:00" not in [block.startTime for block in matched]


def test_visual_dedup_disabled_falls_back_to_basic_v2(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_VIDEO_FRAME_VISUAL_DEDUP_ENABLED", "false")
    monkeypatch.setenv("RAG_VIDEO_FRAME_MIN_INTERVAL_SECONDS", "0")
    frames = [
        FrameImage(time_seconds=0, path=make_frame(tmp_path / "basic-1.jpg", "第一页")),
        FrameImage(time_seconds=60, path=make_frame(tmp_path / "basic-2.jpg", "第一页")),
    ]

    selected, warnings = select_ppt_slide_frames(frames, keep_interval_seconds=30, max_frames=2)

    assert warnings == []
    assert [frame.trigger for frame in selected] == ["initial_slide", "interval"]
    assert all(frame.visual_decision is None for frame in selected)


def test_visual_hash_and_diff_reuse_one_decoded_frame(tmp_path, monkeypatch):
    """同一候选帧同时参与 hash 和差异计算时只应解码一次。"""
    from PIL import Image

    frame_path = make_frame(tmp_path / "cached-frame.jpg", "缓存视觉特征")
    vp._cached_visual_image_features.cache_clear()
    original_open = Image.open
    open_count = 0

    def counting_open(*args, **kwargs):
        nonlocal open_count
        open_count += 1
        return original_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", counting_open)

    assert vp.visual_hash_for_image(frame_path)
    assert vp.image_difference_score(frame_path, frame_path) == 0.0
    assert open_count == 1


class TimedOcrClient:
    """记录并发起点的 OCR 替身，避免单测访问远程百炼服务。"""

    available = True
    enabled = True
    model = "timed-fake-ocr"

    def __init__(self, delay_seconds: float = 0.12) -> None:
        self.delay_seconds = delay_seconds
        self.starts: list[float] = []
        self.lock = threading.Lock()

    def recognize_image_bytes(self, *, image_bytes, filename, retry_callback=None):
        with self.lock:
            self.starts.append(time.monotonic())
        time.sleep(self.delay_seconds)
        return OcrResult(
            text=f"识别 {filename}",
            parser="timed-fake-ocr",
            confidence=0.9,
            metadata={"ocrModel": self.model},
        )


def test_ocr_microbatch_dispatches_four_frames_with_two_in_flight(tmp_path, monkeypatch):
    """验证满批立即派发、并发受限且每帧 evidence 仍完整保留。"""

    monkeypatch.setenv("RAG_VIDEO_OCR_BATCH_MAX_SIZE", "4")
    monkeypatch.setenv("RAG_VIDEO_OCR_BATCH_WAIT_MS", "800")
    monkeypatch.setenv("RAG_VIDEO_OCR_MAX_IN_FLIGHT", "2")
    client = TimedOcrClient()
    frames = [
        FrameImage(time_seconds=index * 30, path=make_frame(tmp_path / f"batch-{index}.jpg", str(index)))
        for index in range(4)
    ]

    started = time.monotonic()
    blocks, warnings = ocr_video_frames(
        frames=frames,
        document_id="doc-batch",
        file_type="mp4",
        source_title="批量视频",
        source_path="uploads/rag/batch.mp4",
        ocr_client=client,
    )
    elapsed = time.monotonic() - started

    assert warnings == []
    assert len(blocks) == 4
    assert elapsed < 0.38  # 串行四帧至少约 0.48s；2 个 in-flight 应为两波。
    assert max(client.starts[:2]) - min(client.starts[:2]) < 0.08
    assert {block.metadata["ocrBatchSize"] for block in blocks} == {4}
    assert {block.metadata["ocrBatchMaxSize"] for block in blocks} == {4}
    assert all(block.metadata["ocrBatchWaitMs"] == 800 for block in blocks)


def test_ocr_microbatch_fallback_uses_the_matching_frame_bytes(tmp_path, monkeypatch):
    """远端单帧失败时，本地 OCR 降级必须保留该帧而非最后一帧的字节。"""

    class PartiallyFailingOcrClient:
        available = True
        enabled = True
        model = "partial-fake-ocr"

        def recognize_image_bytes(self, *, image_bytes, filename, retry_callback=None):
            if filename == "first.jpg":
                return OcrResult(text="", parser="partial-fake-ocr")
            return OcrResult(text="第二帧远端文本", parser="partial-fake-ocr", confidence=0.9)

    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first-frame-local-fallback")
    second.write_bytes(b"second-frame")
    monkeypatch.setattr(vp, "tesseract_frame_text", lambda image_bytes: (image_bytes.decode("ascii"), None))

    blocks, warnings = ocr_video_frames(
        frames=[FrameImage(time_seconds=0, path=first), FrameImage(time_seconds=30, path=second)],
        document_id="doc-fallback",
        file_type="mp4",
        source_title="降级视频",
        source_path="uploads/rag/fallback.mp4",
        ocr_client=PartiallyFailingOcrClient(),
    )

    assert warnings == []
    assert blocks[0].contentText.endswith("first-frame-local-fallback")
    assert blocks[1].contentText.endswith("第二帧远端文本")


def test_ocr_microbatch_waits_for_partial_batch_before_dispatch(tmp_path):
    """验证未满批时按窗口等待，避免稀疏帧立即产生过多远程请求。"""

    client = TimedOcrClient(delay_seconds=0)
    frame = FrameImage(time_seconds=0, path=make_frame(tmp_path / "tail.jpg", "tail"))
    dispatcher = FrameOcrMicrobatchDispatcher(
        ocr_client=client,
        batch_max_size=4,
        batch_wait_ms=60,
        max_in_flight=1,
        progress_reporter=None,
        total_frames=1,
    )
    started = time.monotonic()
    result_future = dispatcher.submit(index=1, frame=frame, image_bytes=frame.path.read_bytes())
    result = result_future.result(timeout=1)
    elapsed = time.monotonic() - started
    dispatcher.close()

    assert result.text == "识别 tail.jpg"
    assert elapsed >= 0.04
    assert result.metadata["ocrBatchSize"] == 1
