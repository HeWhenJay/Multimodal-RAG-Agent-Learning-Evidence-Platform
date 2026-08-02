"""公开视频链接校验与临时下载测试。"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from types import ModuleType

import pytest
from pydantic import ValidationError

from app.core.result import BusinessError
from app.schemas.kafka import RemoteVideoSourceRef
from app.services.remote_video_import import (
    RemoteVideoPermanentError,
    RemoteVideoTaskDeadline,
    RemoteVideoTaskTimeoutError,
    cleanup_stale_remote_video_directories,
    download_bilibili_video,
    download_options,
    extract_remote_video_urls,
    extract_public_metadata,
    is_transient_download_failure,
    run_ytdlp_ffmpeg_with_deadline,
    validate_public_metadata,
    validate_remote_video_url,
)


def test_bilibili_url_is_canonicalized_without_tracking_parameters() -> None:
    """只保留视频标识和合法分 P，避免把追踪参数写入任务。"""
    remote = validate_remote_video_url(
        "https://m.bilibili.com/video/bv1xx411c7mD?p=2&spm_id_from=333.1007"
    )

    assert remote.platform == "bilibili"
    assert remote.video_id == "BV1xx411c7mD"
    assert remote.page == 2
    assert remote.canonical_url == "https://www.bilibili.com/video/BV1xx411c7mD?p=2"


def test_share_text_extracts_bilibili_url_and_preserves_page() -> None:
    """平台复制出的中文标题和追踪参数不能妨碍提取正确的分 P 链接。"""
    text = (
        "【新版Java面试专题视频教程，java八股文面试全套真题+深度详解（含大厂高频面试真题）】"
        "https://www.bilibili.com/video/BV1yT411H7YK?p=32&vd_source=2fdd0f5bf8a8fb91092ac355f273d485"
    )

    extracted = extract_remote_video_urls(text)
    remote = validate_remote_video_url(extracted[0].value)

    assert extracted[0].line_number == 1
    assert extracted[0].value.endswith("vd_source=2fdd0f5bf8a8fb91092ac355f273d485")
    assert remote.canonical_url == "https://www.bilibili.com/video/BV1yT411H7YK?p=32"


def test_url_extraction_handles_markdown_chinese_punctuation_and_multiple_lines() -> None:
    """批量提取应清理 Markdown/中文尾标点，并保留每条候选的原始行号。"""
    extracted = extract_remote_video_urls(
        "第一条 [课程](https://www.bilibili.com/video/BV1xx411c7mD)。\n"
        "第二条https://m.bilibili.com/video/BV1nx411u79K?p=2，后续说明"
    )

    assert [(item.line_number, item.value) for item in extracted] == [
        (1, "https://www.bilibili.com/video/BV1xx411c7mD"),
        (2, "https://m.bilibili.com/video/BV1nx411u79K?p=2"),
    ]


def test_remote_video_url_rejects_an_oversized_candidate() -> None:
    """批量文本不限制 URL 条数，但单条异常超长候选必须在访问网络前拒绝。"""
    with pytest.raises(BusinessError, match="2048"):
        validate_remote_video_url("https://www.bilibili.com/video/BV1xx411c7mD?x=" + "a" * 2048)


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("http://www.bilibili.com/video/BV1xx411c7mD", "当前仅支持"),
        ("https://www.bilibili.com:444/video/BV1xx411c7mD", "当前仅支持"),
        ("https://www.bilibili.com.evil.example/video/BV1xx411c7mD", "当前仅支持"),
        ("https://127.0.0.1/video/BV1xx411c7mD", "当前仅支持"),
        ("https://user:pass@www.bilibili.com/video/BV1xx411c7mD", "当前仅支持"),
        ("https://b23.tv/example", "展开后的"),
        ("https://www.douyin.com/video/6961737553342991651", "抖音链接暂不支持"),
    ],
)
def test_remote_video_url_rejects_untrusted_or_unsupported_targets(url: str, message: str) -> None:
    """任意网络目标、短链接和抖音挑战链路必须在 API 阶段拒绝。"""
    with pytest.raises(BusinessError, match=message):
        validate_remote_video_url(url)


def test_remote_video_kafka_reference_requires_matching_canonical_id() -> None:
    """内部消息不能用合法 URL 搭配另一个视频 ID，或重新带回追踪参数。"""
    with pytest.raises(ValidationError, match="规范化 Bilibili 地址"):
        RemoteVideoSourceRef(
            url="https://www.bilibili.com/video/BV1xx411c7mD",
            videoId="BV1nx411u79K",
        )
    with pytest.raises(ValidationError, match="规范化 Bilibili 地址"):
        RemoteVideoSourceRef(
            url="https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333.1",
            videoId="BV1xx411c7mD",
        )


def test_public_metadata_rejects_live_duration_and_wrong_extractor(monkeypatch) -> None:
    """下载前拒绝直播、超长资源和非 Bilibili 提取器。"""
    monkeypatch.setenv("RAG_REMOTE_VIDEO_MAX_DURATION_SECONDS", "60")

    with pytest.raises(RemoteVideoPermanentError, match="直播"):
        validate_public_metadata({"extractor_key": "BiliBili", "is_live": True})
    with pytest.raises(RemoteVideoPermanentError, match="时长"):
        validate_public_metadata({"extractor_key": "BiliBili", "duration": 61})
    with pytest.raises(RemoteVideoPermanentError, match="无法确认"):
        validate_public_metadata({"extractor_key": "BiliBili"})
    with pytest.raises(RemoteVideoPermanentError, match="仅支持 Bilibili"):
        validate_public_metadata({"extractor_key": "Generic", "duration": 30})
    with pytest.raises(RemoteVideoPermanentError, match="仅支持 Bilibili"):
        validate_public_metadata({"extractor_key": "BiliBiliBangumi", "duration": 30})
    with pytest.raises(RemoteVideoPermanentError, match="DRM"):
        validate_public_metadata({"extractor_key": "BiliBili", "duration": 30, "is_drm": True})
    with pytest.raises(RemoteVideoPermanentError, match="无法公开访问"):
        validate_public_metadata({"extractor_key": "BiliBili", "duration": 30, "availability": "needs_auth"})


def test_download_progress_enforces_hard_size_limit_without_ui_callback(monkeypatch, tmp_path) -> None:
    """即使没有进度回调，也要在下载过程中阻止文件持续超过硬上限。"""
    monkeypatch.setenv("RAG_REMOTE_VIDEO_MAX_BYTES", str(1024 * 1024))
    options = download_options(tmp_path, None)

    with pytest.raises(RemoteVideoPermanentError, match="大小限制"):
        options["progress_hooks"][0]({
            "status": "downloading",
            "downloaded_bytes": 1024 * 1024 + 1,
            "total_bytes": 0,
        })


def test_download_progress_accumulates_separate_audio_and_video_streams(monkeypatch, tmp_path) -> None:
    """DASH 音视频分流必须按任务累计，不能分别绕过总字节上限。"""
    monkeypatch.setenv("RAG_REMOTE_VIDEO_MAX_BYTES", str(1024 * 1024))
    hook = download_options(tmp_path, None)["progress_hooks"][0]
    hook({"status": "downloading", "ctx_id": "video", "downloaded_bytes": 700_000})

    with pytest.raises(RemoteVideoPermanentError, match="大小限制"):
        hook({"status": "downloading", "ctx_id": "audio", "downloaded_bytes": 400_000})


def test_bilibili_rate_limit_wording_is_a_transient_failure() -> None:
    """Bilibili 提取器的非 HTTP 限流文案也必须进入耐久重试。"""
    assert is_transient_download_failure("ERROR: exceeded the rate limit. Try again later") is True


def test_task_deadline_covers_download_and_postprocessor_hooks(tmp_path) -> None:
    """下载和 DASH/字幕后处理必须共享同一个绝对截止时间。"""
    now = [100.0]
    deadline = RemoteVideoTaskDeadline(10, clock=lambda: now[0])
    options = download_options(tmp_path, None, task_deadline=deadline)
    now[0] = 111.0

    with pytest.raises(RemoteVideoTaskTimeoutError, match="任务总时限"):
        options["progress_hooks"][0]({"status": "downloading", "downloaded_bytes": 1})
    with pytest.raises(RemoteVideoTaskTimeoutError, match="任务总时限"):
        options["postprocessor_hooks"][0]({"status": "started"})


def test_metadata_extraction_uses_the_same_task_deadline() -> None:
    """元数据请求返回时若已越过截止时间，必须收敛为可重试中文超时。"""
    now = [10.0]
    deadline = RemoteVideoTaskDeadline(5, clock=lambda: now[0])

    class FakeYoutubeDL:
        def __init__(self, _options: dict) -> None:
            return None

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def extract_info(self, _url: str, *, download: bool):
            assert download is False
            now[0] = 16.0
            return {"extractor_key": "BiliBili", "duration": 30}

    fake_module = ModuleType("yt_dlp")
    fake_module.YoutubeDL = FakeYoutubeDL

    with pytest.raises(RemoteVideoTaskTimeoutError, match="任务总时限"):
        extract_public_metadata(fake_module, "https://www.bilibili.com/video/BV1xx411c7mD", task_deadline=deadline)


def test_task_deadline_watchdog_closes_the_active_downloader() -> None:
    """总时限到期时 watchdog 必须主动关闭当前 yt-dlp downloader。"""
    closed = threading.Event()

    class FakeDownloader:
        def close(self) -> None:
            closed.set()

    deadline = RemoteVideoTaskDeadline(0.05)
    with pytest.raises(RemoteVideoTaskTimeoutError, match="任务总时限"):
        with deadline.interrupt_downloader_on_expiry(FakeDownloader()):
            assert closed.wait(1.0)


def test_ytdlp_ffmpeg_postprocessor_uses_remaining_task_deadline(monkeypatch, tmp_path) -> None:
    """DASH 合并进程必须使用任务剩余时间，并在子进程超时后取消任务。"""
    from yt_dlp.postprocessor import ffmpeg as yt_dlp_ffmpeg

    input_path = tmp_path / "video.mp4"
    input_path.write_bytes(b"video")
    seen_timeouts: list[float] = []

    def fake_run(_command, **kwargs):
        seen_timeouts.append(float(kwargs["timeout"]))
        raise subprocess.TimeoutExpired("ffmpeg", kwargs["timeout"])

    monkeypatch.setattr(yt_dlp_ffmpeg.Popen, "run", staticmethod(fake_run))

    class FakePostprocessor:
        executable = "ffmpeg"
        basename = "ffmpeg"

        def check_version(self) -> None:
            return None

        def _configuration_args(self, _basename: str, _keys: list[str]) -> list[str]:
            return []

        def _ffmpeg_filename_argument(self, value: str) -> str:
            return value

        def write_debug(self, _message: str) -> None:
            return None

        def try_utime(self, *_args) -> None:
            return None

    deadline = RemoteVideoTaskDeadline(5)
    with pytest.raises(RemoteVideoTaskTimeoutError, match="任务总时限"):
        run_ytdlp_ffmpeg_with_deadline(
            FakePostprocessor(),
            [(str(input_path), [])],
            [(str(tmp_path / "merged.mp4"), [])],
            task_deadline=deadline,
        )

    assert len(seen_timeouts) == 1
    assert 0 < seen_timeouts[0] <= 5
    with pytest.raises(RemoteVideoTaskTimeoutError, match="任务总时限"):
        deadline.raise_if_expired()


def test_downloader_uses_no_cookies_and_cleans_temporary_files(monkeypatch, tmp_path) -> None:
    """yt-dlp 不得读取浏览器 Cookie，且任务结束后删除视频和字幕。"""
    created_options: list[dict] = []

    class FakeDownloadError(Exception):
        pass

    class FakeYoutubeDL:
        """模拟元数据读取、视频下载和中文字幕输出。"""

        def __init__(self, options: dict) -> None:
            self.options = options
            created_options.append(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def extract_info(self, _url: str, *, download: bool):
            if download:
                output = self.options["outtmpl"].replace("%(ext)s", "mp4")
                with open(output, "wb") as handle:
                    handle.write(b"video-bytes")
                with open(output.replace("source.mp4", "source.zh-Hans.srt"), "w", encoding="utf-8") as handle:
                    handle.write("1\n00:00:00,000 --> 00:00:01,000\nKafka 高可用\n")
                for hook in self.options["progress_hooks"]:
                    hook({"status": "finished", "downloaded_bytes": 11, "total_bytes": 11})
            return {
                "extractor_key": "BiliBili",
                "title": "Kafka 高可用课程",
                "duration": 30,
                "filesize": 11,
            }

    fake_module = ModuleType("yt_dlp")
    fake_module.YoutubeDL = FakeYoutubeDL
    fake_utils = ModuleType("yt_dlp.utils")
    fake_utils.DownloadError = FakeDownloadError
    monkeypatch.setitem(sys.modules, "yt_dlp", fake_module)
    monkeypatch.setitem(sys.modules, "yt_dlp.utils", fake_utils)
    monkeypatch.setenv("RAG_REMOTE_VIDEO_TEMP_ROOT", str(tmp_path))
    monkeypatch.setattr(
        "app.services.remote_video_import.probe_downloaded_duration",
        lambda _path, **_kwargs: 30.0,
    )
    progress: list[int | None] = []

    opened = download_bilibili_video(
        "https://www.bilibili.com/video/BV1xx411c7mD",
        on_progress=progress.append,
    )
    temporary_directory = opened.path.parent

    assert opened.title == "Kafka 高可用课程"
    assert opened.path.read_bytes() == b"video-bytes"
    assert opened.path.with_suffix(".srt").exists()
    assert progress == [100]
    assert all(options["cookiefile"] is None and options["cookiesfrombrowser"] is None for options in created_options)
    assert all(options["usenetrc"] is False and options["username"] is None for options in created_options)
    assert created_options[-1]["noplaylist"] is True
    assert "max_downloads" not in created_options[-1]

    opened.cleanup()
    assert not temporary_directory.exists()


def test_stale_remote_video_directories_are_cleaned_without_touching_active_tasks(monkeypatch, tmp_path) -> None:
    """崩溃遗留目录超过 TTL 后清理，近期任务目录保持不变。"""
    monkeypatch.setenv("RAG_REMOTE_VIDEO_TEMP_TTL_SECONDS", str(24 * 60 * 60))
    stale = tmp_path / "rag-remote-video-stale"
    active = tmp_path / "rag-remote-video-active"
    unrelated = tmp_path / "other-directory"
    for path in (stale, active, unrelated):
        path.mkdir()
    now = 2_000_000_000.0
    os.utime(stale, (now - 25 * 60 * 60, now - 25 * 60 * 60))
    os.utime(active, (now - 60, now - 60))
    os.utime(unrelated, (now - 25 * 60 * 60, now - 25 * 60 * 60))

    cleanup_stale_remote_video_directories(tmp_path, now=now)

    assert not stale.exists()
    assert active.exists()
    assert unrelated.exists()
