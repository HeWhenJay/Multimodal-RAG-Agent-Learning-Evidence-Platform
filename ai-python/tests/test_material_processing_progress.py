"""资料处理进度快照聚合测试。"""

from __future__ import annotations

from datetime import datetime

from app.schemas.rag import ProgressEvent
from app.services.material_processing_progress import build_material_processing_progress


def test_processing_progress_maps_raw_events_to_stable_phases() -> None:
    """细粒度事件应映射为六个稳定阶段，并保留真实切块和耗时。"""
    events = [
        progress_event(
            "embedding.chunk",
            "生成 embedding",
            "第 12/18 块：动态批处理向量已生成",
            status="RUNNING",
            step=7,
            current_chunk=12,
            total_chunks=18,
            percent=67,
            created_at="2026-08-01T10:02:14",
        ),
        progress_event(
            "chunk.recursive",
            "递归切块",
            "当前文件被切分为 18 块",
            status="COMPLETED",
            step=5,
            percent=35,
            created_at="2026-08-01T10:00:45",
        ),
        progress_event(
            "parse.completed",
            "解析完成",
            "解析完成，得到 18 个 DocumentBlock",
            status="COMPLETED",
            step=3,
            percent=25,
            created_at="2026-08-01T10:00:42",
        ),
        progress_event(
            "index.request",
            "接收索引请求",
            "已接收上传文件索引请求",
            status="RUNNING",
            step=1,
            percent=5,
            created_at="2026-08-01T10:00:08",
        ),
    ]

    progress = build_material_processing_progress(
        material_status="PARSING",
        events=events,
        created_at="2026-08-01T10:00:00",
        updated_at="2026-08-01T10:00:02",
        now=datetime(2026, 8, 1, 10, 2, 20),
    )

    assert progress.currentPhaseCode == "EMBEDDING"
    assert progress.currentStageCode == "embedding.chunk"
    assert progress.currentChunk == 12
    assert progress.totalChunks == 18
    assert progress.percent == 67
    assert progress.completedPhaseCount == 3
    assert progress.elapsedSeconds == 140
    assert progress.lastUpdatedAt == "2026-08-01T10:02:14"
    assert [phase.status for phase in progress.phases] == [
        "COMPLETED",
        "COMPLETED",
        "COMPLETED",
        "RUNNING",
        "PENDING",
        "PENDING",
    ]


def test_failed_progress_hides_private_paths_and_returns_recovery_action() -> None:
    """失败快照不得暴露本地路径或私有 OSS 地址，并应给出恢复动作。"""
    events = [
        progress_event(
            "parse.video",
            "处理视频",
            r"视频解析失败：C:\Users\demo\AppData\Local\Temp\rag-oss-video.mp4",
            status="FAILED",
            step=3,
            percent=15,
            created_at="2026-08-01T10:00:30",
        )
    ]

    progress = build_material_processing_progress(
        material_status="FAILED",
        events=events,
        created_at="2026-08-01T10:00:00",
        updated_at="2026-08-01T10:00:30",
        failure_summary="对象下载失败：oss://private-bucket/user/video.mp4",
    )

    assert progress.isTerminal is True
    assert progress.isProcessing is False
    assert progress.currentPhaseCode == "PARSE"
    assert progress.phases[1].status == "FAILED"
    assert progress.failureMessage == "对象下载失败：[私有对象地址已隐藏]"
    assert "oss://" not in progress.failureMessage
    assert "C:\\Users" not in progress.phases[1].message
    assert "[本地路径已隐藏]" in progress.phases[1].message
    assert "重建索引" in progress.nextAction


def test_progress_without_events_returns_visible_queue_fallback() -> None:
    """日志尚未写入时也应返回可轮询的排队状态，而不是空进度。"""
    progress = build_material_processing_progress(
        material_status="PENDING",
        events=[],
        created_at="2026-08-01T10:00:00",
        updated_at="2026-08-01T10:00:00",
        now=datetime(2026, 8, 1, 10, 0, 30),
    )

    assert progress.isProcessing is True
    assert progress.currentPhaseCode == "UPLOAD"
    assert progress.currentStageCode == "upload.pending"
    assert progress.phases[0].status == "RUNNING"
    assert progress.message == "任务已创建，正在等待后台 worker 接收"
    assert progress.elapsedSeconds == 30


def progress_event(
    stage_code: str,
    stage_label: str,
    message: str,
    *,
    status: str,
    step: int,
    percent: int,
    created_at: str,
    current_chunk: int | None = None,
    total_chunks: int | None = None,
) -> ProgressEvent:
    """构造带八步流程字段的测试进度事件。"""
    return ProgressEvent(
        stageCode=stage_code,
        stageLabel=stage_label,
        message=message,
        status=status,
        currentStep=step,
        totalSteps=8,
        currentChunk=current_chunk,
        totalChunks=total_chunks,
        percent=percent,
        createdAt=created_at,
    )
