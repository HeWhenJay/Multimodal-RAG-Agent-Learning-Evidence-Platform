"""受限的公开视频链接校验与临时下载。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import itertools
import logging
import mimetypes
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from types import MethodType
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from app.core.result import BusinessError


logger = logging.getLogger(__name__)
BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com"}
DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", "v.douyin.com", "iesdouyin.com"}
BILIBILI_VIDEO_PATTERN = re.compile(r"^(?:BV[0-9A-Za-z]{8,20}|av[0-9]+)$", re.IGNORECASE)
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
REMOTE_VIDEO_PRIVATE_MESSAGE = "远程视频解析异常，临时文件信息已隐藏"


@dataclass(frozen=True)
class RemoteVideoUrl:
    """校验后的平台链接和稳定视频标识。"""

    platform: str
    canonical_url: str
    video_id: str
    page: int | None

    @property
    def placeholder_title(self) -> str:
        """在后台元数据尚未获取时生成资料标题。"""
        page_suffix = f" P{self.page}" if self.page and self.page > 1 else ""
        return f"Bilibili 视频 {self.video_id}{page_suffix}"


@dataclass
class OpenedRemoteVideo:
    """worker 当前任务持有的临时视频及公开来源元数据。"""

    path: Path
    filename: str
    title: str
    content_type: str
    source_url: str
    duration_seconds: float | None
    _temp_directory: Any

    def cleanup(self) -> None:
        """删除当前任务下载的视频、字幕和分片。"""
        self._temp_directory.cleanup()


class RemoteVideoError(RuntimeError):
    """只携带可公开展示的远程视频错误。"""

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


class RemoteVideoPermanentError(RemoteVideoError):
    """访问范围、版权限制或资源上限等无需自动重试的错误。"""


class RemoteVideoTransientError(RemoteVideoError):
    """网络超时或平台临时故障等可由耐久任务重试的错误。"""


class RemoteVideoTaskTimeoutError(RemoteVideoTransientError):
    """单次远程资源获取超过总墙钟时限，可由耐久任务重试。"""


class RemoteVideoTaskDeadline:
    """为元数据、下载和 yt-dlp 后处理共享一个可取消的绝对截止时间。"""

    def __init__(
        self,
        timeout_seconds: float,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self._clock = clock or time.monotonic
        self._expires_at = self._clock() + self.timeout_seconds
        self._cancelled = threading.Event()

    @classmethod
    def from_environment(cls) -> "RemoteVideoTaskDeadline":
        """按生产配置创建单次远程资源获取截止时间。"""
        return cls(remote_video_task_timeout_seconds())

    def remaining_seconds(self) -> float:
        """返回当前任务还能使用的墙钟秒数。"""
        return max(0.0, self._expires_at - self._clock())

    def cancel(self) -> None:
        """由 watchdog 或调用方请求协作取消当前任务。"""
        self._cancelled.set()

    def raise_if_expired(self) -> None:
        """在阶段边界和 hook 中统一抛出不含第三方正文的中文超时错误。"""
        if self.remaining_seconds() <= 0:
            self._cancelled.set()
        if self._cancelled.is_set():
            raise RemoteVideoTaskTimeoutError("Bilibili 视频处理超过任务总时限")

    @contextmanager
    def interrupt_downloader_on_expiry(self, downloader: Any) -> Iterator[None]:
        """到期时关闭 yt-dlp 网络资源，并由当前执行线程收敛为受控超时。"""
        self.raise_if_expired()

        def interrupt() -> None:
            self.cancel()
            close = getattr(downloader, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    # watchdog 线程只负责触发取消，原执行线程负责异常收敛和清理。
                    return

        timer = threading.Timer(self.remaining_seconds(), interrupt)
        timer.name = "rag-remote-video-deadline"
        timer.daemon = True
        timer.start()
        try:
            yield
        finally:
            timer.cancel()
        self.raise_if_expired()


def validate_remote_video_url(value: str) -> RemoteVideoUrl:
    """仅接受 Bilibili 完整公开视频 URL，拒绝任意网络目标。"""
    raw = str(value or "").strip()
    if not raw:
        raise BusinessError("视频链接不能为空")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise BusinessError("当前仅支持 Bilibili 完整公开视频链接") from exc
    host = (parsed.hostname or "").rstrip(".").lower()
    if host in DOUYIN_HOSTS:
        raise BusinessError("抖音链接暂不支持：平台要求动态 Cookie 和挑战签名，本系统不绕过访问限制")
    if host == "b23.tv" or host.endswith(".b23.tv"):
        raise BusinessError("请粘贴展开后的 Bilibili 完整视频链接")
    if (
        parsed.scheme.lower() != "https"
        or host not in BILIBILI_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise BusinessError("当前仅支持 Bilibili 完整公开视频链接")
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) != 2 or path_parts[0].lower() != "video" or not BILIBILI_VIDEO_PATTERN.fullmatch(path_parts[1]):
        raise BusinessError("当前仅支持 Bilibili 完整公开视频链接")
    video_id = normalize_bilibili_video_id(path_parts[1])
    page = parse_bilibili_page(parsed.query)
    query = urlencode({"p": page}) if page and page > 1 else ""
    canonical = urlunsplit(("https", "www.bilibili.com", f"/video/{video_id}", query, ""))
    return RemoteVideoUrl("bilibili", canonical, video_id, page)


def download_bilibili_video(
    value: str,
    *,
    on_progress: Callable[[int | None], None] | None = None,
    task_deadline: RemoteVideoTaskDeadline | None = None,
) -> OpenedRemoteVideo:
    """无 Cookie 下载单个 Bilibili 公热视频到受控临时目录。"""
    remote = validate_remote_video_url(value)
    deadline = task_deadline or RemoteVideoTaskDeadline.from_environment()
    deadline.raise_if_expired()
    try:
        import yt_dlp
        from yt_dlp.utils import DownloadError
    except ImportError as exc:
        raise RemoteVideoPermanentError("服务端远程视频组件不可用") from exc

    root = remote_video_temp_root()
    temp_directory = tempfile.TemporaryDirectory(prefix="rag-remote-video-", dir=root)
    target_root = Path(temp_directory.name)
    try:
        metadata = extract_public_metadata(yt_dlp, remote.canonical_url, task_deadline=deadline)
        deadline.raise_if_expired()
        validate_public_metadata(metadata)
        options = download_options(target_root, on_progress, task_deadline=deadline)
        with yt_dlp.YoutubeDL(options) as downloader:
            install_ytdlp_postprocessor_deadline(downloader, deadline)
            with deadline.interrupt_downloader_on_expiry(downloader):
                downloader.extract_info(remote.canonical_url, download=True)
        deadline.raise_if_expired()
        video_path = select_downloaded_video(target_root)
        actual_duration = probe_downloaded_duration(video_path, timeout_seconds=deadline.remaining_seconds())
        deadline.raise_if_expired()
        if actual_duration is None:
            raise RemoteVideoPermanentError("服务端无法校验远程视频时长")
        if actual_duration > remote_video_max_duration_seconds():
            raise RemoteVideoPermanentError("Bilibili 视频超过远程接入时长限制")
        deadline.raise_if_expired()
        normalize_downloaded_subtitle(video_path, target_root, task_deadline=deadline)
        deadline.raise_if_expired()
        if video_path.stat().st_size > remote_video_max_bytes() or directory_size_bytes(
            target_root,
            task_deadline=deadline,
        ) > remote_video_max_bytes():
            raise RemoteVideoPermanentError("Bilibili 视频超过远程接入大小限制")
        deadline.raise_if_expired()
        title = compact_title(metadata.get("title"), remote.placeholder_title)
        content_type = mimetypes.guess_type(video_path.name)[0] or "video/mp4"
        return OpenedRemoteVideo(
            path=video_path,
            filename=video_path.name,
            title=title,
            content_type=content_type,
            source_url=remote.canonical_url,
            duration_seconds=actual_duration,
            _temp_directory=temp_directory,
        )
    except RemoteVideoError:
        temp_directory.cleanup()
        raise
    except DownloadError as exc:
        temp_directory.cleanup()
        deadline.raise_if_expired()
        if is_transient_download_failure(str(exc)):
            raise RemoteVideoTransientError("Bilibili 视频下载暂时失败") from exc
        raise RemoteVideoPermanentError("Bilibili 视频无法公开访问") from exc
    except Exception as exc:
        temp_directory.cleanup()
        deadline.raise_if_expired()
        logger.warning("Bilibili 远程视频处理失败，errorType=%s", exc.__class__.__name__)
        raise RemoteVideoTransientError("Bilibili 视频下载暂时失败") from exc


def extract_public_metadata(
    yt_dlp_module: Any,
    url: str,
    *,
    task_deadline: RemoteVideoTaskDeadline | None = None,
) -> dict[str, Any]:
    """先读取公开元数据，下载前拒绝直播、长视频和非 Bilibili 提取器。"""
    if task_deadline is not None:
        task_deadline.raise_if_expired()

    def metadata_filter(_info: dict[str, Any], *, incomplete: bool = False) -> None:
        del incomplete
        if task_deadline is not None:
            task_deadline.raise_if_expired()
        return None

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": remote_video_socket_timeout(),
        "retries": remote_video_retries(),
        **anonymous_access_options(),
        "logger": _YtDlpLogger(),
    }
    if task_deadline is not None:
        options["match_filter"] = metadata_filter
    with yt_dlp_module.YoutubeDL(options) as downloader:
        if task_deadline is None:
            result = downloader.extract_info(url, download=False)
        else:
            with task_deadline.interrupt_downloader_on_expiry(downloader):
                result = downloader.extract_info(url, download=False)
    if task_deadline is not None:
        task_deadline.raise_if_expired()
    if not isinstance(result, dict):
        raise RemoteVideoPermanentError("Bilibili 视频无法公开访问")
    return result


def validate_public_metadata(metadata: dict[str, Any]) -> None:
    """根据公开元数据执行平台、直播、时长和预估大小限制。"""
    extractor = str(metadata.get("extractor_key") or metadata.get("extractor") or "")
    if extractor.lower() != "bilibili":
        raise RemoteVideoPermanentError("当前仅支持 Bilibili 完整公开视频链接")
    if metadata.get("is_drm"):
        raise RemoteVideoPermanentError("不支持 DRM 保护的视频")
    if str(metadata.get("availability") or "").lower() in {
        "private",
        "premium_only",
        "subscriber_only",
        "needs_auth",
    }:
        raise RemoteVideoPermanentError("Bilibili 视频无法公开访问")
    if metadata.get("is_live") or str(metadata.get("live_status") or "").lower() in {"is_live", "is_upcoming"}:
        raise RemoteVideoPermanentError("暂不支持直播或预约直播")
    duration = positive_float(metadata.get("duration"))
    if duration is None:
        raise RemoteVideoPermanentError("无法确认 Bilibili 视频时长")
    if duration > remote_video_max_duration_seconds():
        raise RemoteVideoPermanentError("Bilibili 视频超过远程接入时长限制")
    estimated_size = max(
        positive_int(metadata.get("filesize")),
        positive_int(metadata.get("filesize_approx")),
    )
    if estimated_size > remote_video_max_bytes():
        raise RemoteVideoPermanentError("Bilibili 视频超过远程接入大小限制")


def download_options(
    target_root: Path,
    on_progress: Callable[[int | None], None] | None,
    *,
    task_deadline: RemoteVideoTaskDeadline | None = None,
) -> dict[str, Any]:
    """生成不会读取配置文件或浏览器 Cookie 的 yt-dlp 参数。"""
    last_bucket = -1
    max_bytes = remote_video_max_bytes()
    downloaded_by_item: dict[str, int] = {}

    def progress_hook(payload: dict[str, Any]) -> None:
        nonlocal last_bucket
        if task_deadline is not None:
            task_deadline.raise_if_expired()
        if payload.get("status") not in {"downloading", "finished"}:
            return
        total = positive_int(payload.get("total_bytes")) or positive_int(payload.get("total_bytes_estimate"))
        downloaded = positive_int(payload.get("downloaded_bytes"))
        item_key = str(payload.get("filename") or payload.get("ctx_id") or "default")
        downloaded_by_item[item_key] = max(downloaded_by_item.get(item_key, 0), downloaded)
        if sum(downloaded_by_item.values()) > max_bytes:
            raise RemoteVideoPermanentError("Bilibili 视频超过远程接入大小限制")
        if on_progress is None:
            return
        percent = 100 if payload.get("status") == "finished" else int(downloaded * 100 / total) if total else None
        bucket = 10 if percent == 100 else int(percent / 10) if percent is not None else 0
        if bucket != last_bucket:
            last_bucket = bucket
            on_progress(percent)

    def postprocessor_hook(_payload: dict[str, Any]) -> None:
        """在 DASH 合并和字幕等后处理阶段继续执行同一任务截止检查。"""
        if task_deadline is not None:
            task_deadline.raise_if_expired()

    options: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "outtmpl": str(target_root / "source.%(ext)s"),
        "format": "bestvideo*[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "max_filesize": max_bytes,
        "socket_timeout": remote_video_socket_timeout(),
        "retries": remote_video_retries(),
        "fragment_retries": remote_video_retries(),
        "concurrent_fragment_downloads": 1,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["zh-Hans", "zh-CN", "zh", "en"],
        **anonymous_access_options(),
        "logger": _YtDlpLogger(),
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
    }
    ffmpeg = resolve_ffmpeg_location()
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg
    return options


def install_ytdlp_postprocessor_deadline(
    downloader: Any,
    task_deadline: RemoteVideoTaskDeadline,
) -> None:
    """仅包装当前 downloader 的 FFmpeg 后处理，确保子进程受剩余墙钟时间约束。"""
    original_run_pp = getattr(downloader, "run_pp", None)
    if not callable(original_run_pp):
        return

    def run_pp_with_deadline(_downloader: Any, postprocessor: Any, info: dict[str, Any]) -> Any:
        task_deadline.raise_if_expired()
        original_real_run = getattr(postprocessor, "real_run_ffmpeg", None)
        real_run_function = getattr(original_real_run, "__func__", original_real_run)
        should_wrap = callable(original_real_run) and str(
            getattr(real_run_function, "__module__", "")
        ).startswith("yt_dlp.postprocessor")
        had_instance_override = "real_run_ffmpeg" in getattr(postprocessor, "__dict__", {})
        previous_override = getattr(postprocessor, "__dict__", {}).get("real_run_ffmpeg")
        if should_wrap:
            postprocessor.real_run_ffmpeg = MethodType(
                lambda current, inputs, outputs, *, expected_retcodes=(0,): run_ytdlp_ffmpeg_with_deadline(
                    current,
                    inputs,
                    outputs,
                    expected_retcodes=expected_retcodes,
                    task_deadline=task_deadline,
                ),
                postprocessor,
            )
        try:
            result = original_run_pp(postprocessor, info)
            task_deadline.raise_if_expired()
            return result
        finally:
            if should_wrap:
                if had_instance_override:
                    postprocessor.real_run_ffmpeg = previous_override
                else:
                    delattr(postprocessor, "real_run_ffmpeg")

    downloader.run_pp = MethodType(run_pp_with_deadline, downloader)


def run_ytdlp_ffmpeg_with_deadline(
    postprocessor: Any,
    input_path_options: list[tuple[str, list[str]]],
    output_path_options: list[tuple[str, list[str]]],
    *,
    expected_retcodes: tuple[int, ...] = (0,),
    task_deadline: RemoteVideoTaskDeadline,
) -> str:
    """复用 yt-dlp 命令构造方式，并给 FFmpeg 子进程设置任务剩余墙钟超时。"""
    from yt_dlp.postprocessor import ffmpeg as yt_dlp_ffmpeg

    task_deadline.raise_if_expired()
    postprocessor.check_version()
    oldest_mtime = min(os.stat(path).st_mtime for path, _options in input_path_options if path)
    command = [postprocessor.executable, yt_dlp_ffmpeg.encodeArgument("-y")]
    if postprocessor.basename == "ffmpeg":
        command += [yt_dlp_ffmpeg.encodeArgument("-loglevel"), yt_dlp_ffmpeg.encodeArgument("repeat+info")]

    def make_args(path: str, args: list[str], name: str, number: int) -> list[str]:
        keys = [f"_{name}{number}", f"_{name}"]
        if name == "o":
            args += ["-movflags", "+faststart"]
            if number == 1:
                keys.append("")
        args += postprocessor._configuration_args(postprocessor.basename, keys)
        if name == "i":
            args.append("-i")
        return [yt_dlp_ffmpeg.encodeArgument(arg) for arg in args] + [
            postprocessor._ffmpeg_filename_argument(path)
        ]

    for argument_type, path_options in (("i", input_path_options), ("o", output_path_options)):
        command += itertools.chain.from_iterable(
            make_args(path, list(options), argument_type, index + 1)
            for index, (path, options) in enumerate(path_options)
            if path
        )
    postprocessor.write_debug(f"ffmpeg command line: {yt_dlp_ffmpeg.shell_quote(command)}")
    try:
        _stdout, stderr, return_code = yt_dlp_ffmpeg.Popen.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            timeout=max(0.001, task_deadline.remaining_seconds()),
        )
    except subprocess.TimeoutExpired as exc:
        task_deadline.cancel()
        raise RemoteVideoTaskTimeoutError("Bilibili 视频处理超过任务总时限") from exc
    task_deadline.raise_if_expired()
    if return_code not in yt_dlp_ffmpeg.variadic(expected_retcodes):
        postprocessor.write_debug(stderr)
        raise yt_dlp_ffmpeg.FFmpegPostProcessorError(stderr.strip().splitlines()[-1])
    for output_path, _options in output_path_options:
        if output_path:
            postprocessor.try_utime(output_path, oldest_mtime, oldest_mtime)
    return stderr


def select_downloaded_video(root: Path) -> Path:
    """优先选择合并后的 source 文件，拒绝残留分片或纯音频。"""
    candidates = [
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES and not path.name.endswith(".part")
    ]
    if not candidates:
        raise RemoteVideoPermanentError("Bilibili 视频没有可处理的公开媒体流")
    candidates.sort(key=lambda path: (path.stem == "source", path.stat().st_size), reverse=True)
    return candidates[0]


def normalize_downloaded_subtitle(
    video_path: Path,
    root: Path,
    *,
    task_deadline: RemoteVideoTaskDeadline | None = None,
) -> None:
    """把 yt-dlp 的语言后缀字幕复制为现有视频解析器可识别的同名侧车字幕。"""
    if task_deadline is not None:
        task_deadline.raise_if_expired()
    priorities = ("zh-hans", "zh-cn", ".zh.", ".en.")
    subtitles = [path for path in root.iterdir() if path.is_file() and path.suffix.lower() in {".srt", ".vtt"}]
    subtitles.sort(
        key=lambda path: next((index for index, marker in enumerate(priorities) if marker in path.name.lower()), len(priorities))
    )
    if not subtitles:
        return
    target = video_path.with_suffix(subtitles[0].suffix.lower())
    if subtitles[0] != target:
        shutil.copy2(subtitles[0], target)


def directory_size_bytes(
    root: Path,
    *,
    task_deadline: RemoteVideoTaskDeadline | None = None,
) -> int:
    """计算远程任务临时目录实际占用，包含字幕和合并残留。"""
    total = 0
    for path in root.rglob("*"):
        if task_deadline is not None:
            task_deadline.raise_if_expired()
        if path.is_file():
            total += path.stat().st_size
    return total


def probe_downloaded_duration(path: Path, *, timeout_seconds: float | None = None) -> float | None:
    """使用现有 FFprobe/FFmpeg 能力校验下载文件的真实时长。"""
    from video.chunking.video_processing import probe_media_duration_strict

    return probe_media_duration_strict(path, timeout_seconds=timeout_seconds)


def resolve_ffmpeg_location() -> str | None:
    """让 yt-dlp 与现有视频解析链路复用同一 FFmpeg 发现策略。"""
    configured = os.getenv("FFMPEG_COMMAND", "").strip()
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


def parse_bilibili_page(query: str) -> int | None:
    """只保留合法分 P 参数，丢弃追踪参数。"""
    values = parse_qs(query).get("p", [])
    if not values:
        return None
    try:
        page = int(values[0])
    except (TypeError, ValueError) as exc:
        raise BusinessError("Bilibili 分 P 参数必须是正整数") from exc
    if page < 1 or page > 999:
        raise BusinessError("Bilibili 分 P 参数必须是正整数")
    return page


def normalize_bilibili_video_id(value: str) -> str:
    """规范化 BV/av 前缀，保留平台标识主体。"""
    return "BV" + value[2:] if value[:2].lower() == "bv" else "av" + value[2:]


def remote_video_temp_root() -> Path:
    """创建服务端受控临时下载根目录。"""
    root = Path(os.getenv("RAG_REMOTE_VIDEO_TEMP_ROOT", tempfile.gettempdir())).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    cleanup_stale_remote_video_directories(root)
    return root


def cleanup_stale_remote_video_directories(root: Path, *, now: float | None = None) -> None:
    """清理由进程崩溃遗留且超过 TTL 的远程视频临时目录。"""
    cutoff = (now if now is not None else time.time()) - remote_video_temp_ttl_seconds()
    for path in root.iterdir():
        if not path.name.startswith("rag-remote-video-") or not path.is_dir() or path.is_symlink():
            continue
        try:
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path)
        except OSError:
            logger.warning("清理过期远程视频临时目录失败，directory=%s", path.name)


def remote_video_max_bytes() -> int:
    return positive_env("RAG_REMOTE_VIDEO_MAX_BYTES", 512 * 1024 * 1024, minimum=1024 * 1024)


def remote_video_max_duration_seconds() -> int:
    return positive_env("RAG_REMOTE_VIDEO_MAX_DURATION_SECONDS", 4 * 60 * 60, minimum=60)


def remote_video_socket_timeout() -> int:
    return positive_env("RAG_REMOTE_VIDEO_SOCKET_TIMEOUT_SECONDS", 20, minimum=5, maximum=120)


def remote_video_retries() -> int:
    return positive_env("RAG_REMOTE_VIDEO_RETRIES", 2, minimum=0, maximum=5)


def remote_video_task_timeout_seconds() -> int:
    """读取单次远程资源获取总墙钟时限，默认比允许的视频时长多一小时。"""
    default = max(60 * 60, remote_video_max_duration_seconds() + 60 * 60)
    return positive_env("RAG_REMOTE_VIDEO_TASK_TIMEOUT_SECONDS", default, minimum=60, maximum=24 * 60 * 60)


def remote_video_temp_ttl_seconds() -> int:
    configured = positive_env("RAG_REMOTE_VIDEO_TEMP_TTL_SECONDS", 48 * 60 * 60, minimum=24 * 60 * 60)
    return max(configured, remote_video_max_duration_seconds() + 12 * 60 * 60)


def remote_video_user_daily_limit() -> int:
    return positive_env("RAG_REMOTE_VIDEO_USER_DAILY_LIMIT", 10, minimum=1, maximum=100)


def remote_video_user_active_limit() -> int:
    return positive_env("RAG_REMOTE_VIDEO_USER_ACTIVE_LIMIT", 2, minimum=1, maximum=10)


def remote_video_global_active_limit() -> int:
    return positive_env("RAG_REMOTE_VIDEO_GLOBAL_ACTIVE_LIMIT", 32, minimum=1, maximum=500)


def positive_env(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    """读取有界正整数配置。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = max(minimum, value)
    return min(value, maximum) if maximum is not None else value


def positive_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def positive_float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def compact_title(value: object, fallback: str) -> str:
    """清理平台标题并限制数据库展示长度。"""
    text = " ".join(str(value or "").split()).strip()
    return (text or fallback)[:240]


def is_transient_download_failure(message: str) -> bool:
    """仅把明确的网络和平台 5xx/429 故障交给耐久重试。"""
    normalized = message.lower()
    return any(
        marker in normalized
        for marker in (
            "timed out",
            "timeout",
            "connection reset",
            "connection refused",
            "temporary failure",
            "http error 429",
            "http error 500",
            "http error 502",
            "http error 503",
            "http error 504",
            "exceeded the rate limit",
            "rate limit exceeded",
            "too many requests",
            "try again later",
        )
    )


def sanitize_remote_video_parse_result(parsed: Any) -> Any:
    """在索引边界移除解析 warning 中可能遗留的远程临时目录。"""
    warnings = sanitize_remote_video_messages(getattr(parsed, "warnings", []))
    quality = getattr(parsed, "parse_quality", None)
    if quality is not None:
        quality = quality.model_copy(
            update={"messages": sanitize_remote_video_messages(getattr(quality, "messages", []))}
        )
    return replace(parsed, warnings=warnings, parse_quality=quality)


def sanitize_remote_video_messages(messages: list[str]) -> list[str]:
    """把远程任务私有路径替换为固定摘要，并保持消息顺序去重。"""
    sanitized = [
        REMOTE_VIDEO_PRIVATE_MESSAGE if "rag-remote-video-" in str(message).lower() else str(message)
        for message in messages
    ]
    return list(dict.fromkeys(sanitized))


def anonymous_access_options() -> dict[str, Any]:
    """显式禁用 Cookie、浏览器会话、netrc 和账号口令读取。"""
    return {
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "usenetrc": False,
        "netrc_cmd": None,
        "username": None,
        "password": None,
        "videopassword": None,
    }


class _YtDlpLogger:
    """抑制第三方响应和签名地址进入应用日志。"""

    def debug(self, _message: str) -> None:
        return None

    def warning(self, _message: str) -> None:
        return None

    def error(self, _message: str) -> None:
        return None
