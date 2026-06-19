from app.schemas.rag import DocumentBlock
from video.chunking.video_dedup import dedupe_video_frame_blocks


def frame_block(
    time_text: str,
    text: str,
    *,
    slide_index: int | None = 1,
    trigger: str = "interval",
    confidence: float = 0.9,
    metadata_overrides: dict | None = None,
) -> DocumentBlock:
    metadata = {
        "mediaType": "video",
        "evidenceChannel": "frame_ocr",
        "frameTrigger": trigger,
        "frameTime": time_text,
    }
    if slide_index is not None:
        metadata["detectedSlideIndex"] = slide_index
    if metadata_overrides:
        metadata.update(metadata_overrides)
    return DocumentBlock(
        documentId="doc-video",
        blockId=f"doc-video-frame-{time_text.replace(':', '')}",
        fileType="mp4",
        blockType="image",
        slideIndex=slide_index,
        startTime=time_text,
        sectionTitle=f"视频画面 {time_text}",
        contentText=f"视频画面 {time_text}\n{text}",
        parseEngine="bailian-qwen-ocr",
        confidence=confidence,
        sourceTitle="RAG 课程视频",
        sourcePath="uploads/rag/course.mp4",
        metadata=metadata,
    )


def test_video_frame_ocr_dedup_merges_similar_frames(monkeypatch):
    monkeypatch.setenv("RAG_VIDEO_OCR_DEDUP_TEXT_THRESHOLD", "0.80")
    blocks = [
        frame_block("00:06:00", "10_rag_fusion.py fused_scores = {} for docs in results doc_str = dumps(doc)"),
        frame_block("00:08:30", "10_rag_fusion.py fused_scores = {} for docs in results doc_str = dumps(doc)"),
        frame_block("00:09:00", "10_rag_fusion.py fused_scores = {} for docs in results doc_str = dumps(doc)"),
    ]

    deduped, stats = dedupe_video_frame_blocks(blocks, "doc-video")

    assert stats["dedupRemovedCount"] == 2
    assert len(deduped) == 1
    merged = deduped[0]
    assert merged.startTime == "00:06:00"
    assert merged.endTime == "00:09:00"
    assert merged.metadata["mergedFrameCount"] == 3
    assert merged.metadata["sourceFrameTimes"] == ["00:06:00", "00:08:30", "00:09:00"]
    assert merged.metadata["duplicateGroupId"]
    assert "重复出现时间" in merged.contentText


def test_video_frame_ocr_dedup_keeps_different_slides(monkeypatch):
    monkeypatch.setenv("RAG_VIDEO_OCR_DEDUP_TEXT_THRESHOLD", "0.80")
    blocks = [
        frame_block("00:06:00", "RAG-Fusion 使用 RRF 融合多个查询结果", slide_index=1),
        frame_block("00:06:30", "RAG-Fusion 使用 RRF 融合多个查询结果", slide_index=2),
    ]

    deduped, stats = dedupe_video_frame_blocks(blocks, "doc-video")

    assert stats["dedupRemovedCount"] == 0
    assert len(deduped) == 2


def test_video_frame_ocr_dedup_keeps_far_apart_frames(monkeypatch):
    monkeypatch.setenv("RAG_VIDEO_OCR_DEDUP_TEXT_THRESHOLD", "0.80")
    monkeypatch.setenv("RAG_VIDEO_OCR_DEDUP_MAX_GAP_SECONDS", "60")
    blocks = [
        frame_block("00:01:00", "RAG-Fusion 使用 RRF 融合多个查询结果", slide_index=1),
        frame_block("00:04:00", "RAG-Fusion 使用 RRF 融合多个查询结果", slide_index=1),
    ]

    deduped, stats = dedupe_video_frame_blocks(blocks, "doc-video")

    assert stats["dedupRemovedCount"] == 0
    assert len(deduped) == 2


def test_video_frame_ocr_dedup_short_text_requires_scope_match(monkeypatch):
    monkeypatch.setenv("RAG_VIDEO_OCR_DEDUP_MIN_TEXT_CHARS", "30")
    blocks = [
        frame_block("00:01:00", "目录", slide_index=1),
        frame_block("00:01:20", "目录", slide_index=2),
    ]

    deduped, stats = dedupe_video_frame_blocks(blocks, "doc-video")

    assert stats["dedupRemovedCount"] == 0
    assert len(deduped) == 2


def test_video_frame_ocr_dedup_prefers_ppt_flip_representative(monkeypatch):
    monkeypatch.setenv("RAG_VIDEO_OCR_DEDUP_TEXT_THRESHOLD", "0.80")
    blocks = [
        frame_block("00:06:00", "RAG-Fusion 使用 RRF 融合多个查询结果", trigger="interval", confidence=0.95),
        frame_block("00:06:30", "RAG-Fusion 使用 RRF 融合多个查询结果", trigger="ppt_flip", confidence=0.80),
    ]

    deduped, _stats = dedupe_video_frame_blocks(blocks, "doc-video")

    assert len(deduped) == 1
    assert deduped[0].metadata["representativeTime"] == "00:06:30"
    assert deduped[0].metadata["frameTrigger"] == "ppt_flip"


def test_video_frame_ocr_dedup_preserves_ocr_confirmed_and_visual_only_metadata(monkeypatch):
    monkeypatch.setenv("RAG_VIDEO_OCR_DEDUP_TEXT_THRESHOLD", "0.80")
    blocks = [
        frame_block(
            "00:00:05",
            "RAG-Fusion 使用 RRF 融合多个查询结果",
            metadata_overrides={
                "visualGroupId": "visual-0001",
                "timeRanges": [{"startTime": "00:00:05", "endTime": "00:00:05"}],
                "sourceFrameTimes": ["00:00:05"],
                "visualTimeRanges": [{"startTime": "00:01:20", "endTime": "00:01:20"}],
                "visualSourceFrameTimes": ["00:01:20"],
            },
        ),
        frame_block(
            "00:02:10",
            "RAG-Fusion 使用 RRF 融合多个查询结果",
            trigger="visual_verification",
            metadata_overrides={
                "visualGroupId": "visual-0001",
                "timeRanges": [{"startTime": "00:02:10", "endTime": "00:02:10"}],
                "sourceFrameTimes": ["00:02:10"],
                "visualTimeRanges": [{"startTime": "00:03:20", "endTime": "00:03:20"}],
                "visualSourceFrameTimes": ["00:03:20"],
            },
        ),
    ]

    deduped, stats = dedupe_video_frame_blocks(blocks, "doc-video")

    assert stats["dedupRemovedCount"] == 1
    merged = deduped[0]
    assert merged.metadata["sourceFrameTimes"] == ["00:00:05", "00:02:10"]
    assert {"startTime": "00:01:20", "endTime": "00:01:20"} not in merged.metadata["timeRanges"]
    assert merged.metadata["visualSourceFrameTimes"] == ["00:01:20", "00:03:20"]
    assert {"startTime": "00:03:20", "endTime": "00:03:20"} in merged.metadata["visualTimeRanges"]
