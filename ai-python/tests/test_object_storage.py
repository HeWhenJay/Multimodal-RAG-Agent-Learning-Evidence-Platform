"""Python 对象存储、OSS 用户隔离和流式上传测试。"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

from app.api.rag_control import stream_upload_to_temp
from app.core.result import BusinessError
from app.schemas.kafka import StorageSourceRef
from app.services.rag_control_service import merge_chunks
from app.storage.object_storage import (
    LocalRagObjectStorage,
    OssRagObjectStorage,
    build_rag_object_storage,
)
from rag.kafka.worker import open_storage_source


class FakeObjectResponse:
    """模拟 oss2 get_object 的可读响应。"""

    def __init__(self, content: bytes) -> None:
        self.content = content

    def read(self) -> bytes:
        """返回对象内容。"""
        return self.content


class FakeOssBucket:
    """只实现本测试需要的 OSS SDK 方法。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.multipart_uploads: dict[tuple[str, str], dict[int, tuple[str, bytes]]] = {}
        self.multipart_calls: list[tuple[str, str, int]] = []

    def put_object_from_file(self, key: str, filename: str, headers=None) -> None:
        """保存上传文件并记录调用。"""
        self.calls.append((key, filename))
        self.objects[key] = Path(filename).read_bytes()

    def put_object(self, key: str, content: bytes) -> None:
        """保存小文件对象。"""
        self.objects[key] = content

    def get_object(self, key: str) -> FakeObjectResponse:
        """返回内存对象响应。"""
        return FakeObjectResponse(self.objects[key])

    def get_object_to_file(self, key: str, filename: str) -> None:
        """把对象写入 worker 临时文件。"""
        Path(filename).write_bytes(self.objects[key])

    def delete_object(self, key: str) -> None:
        """删除对象。"""
        self.objects.pop(key, None)

    def init_multipart_upload(self, key: str, headers=None):  # noqa: ANN001
        """创建可由测试读取的 multipart 会话。"""
        upload_id = f"upload-{len(self.multipart_uploads) + 1}"
        self.multipart_uploads[(key, upload_id)] = {}
        return SimpleNamespace(upload_id=upload_id)

    def upload_part(self, key: str, upload_id: str, part_number: int, data):  # noqa: ANN001
        """接收文件流或 bytes，并返回稳定 ETag。"""
        content = data.read() if hasattr(data, "read") else bytes(data)
        etag = f"etag-{part_number}-{len(content)}"
        self.multipart_uploads[(key, upload_id)][part_number] = (etag, content)
        self.multipart_calls.append((key, upload_id, part_number))
        return SimpleNamespace(etag=etag)

    def complete_multipart_upload(self, key: str, upload_id: str, parts, headers=None):  # noqa: ANN001
        """按 SDK PartInfo 序号聚合 multipart 内容。"""
        uploaded = self.multipart_uploads[(key, upload_id)]
        self.objects[key] = b"".join(uploaded[item.part_number][1] for item in parts)


def test_provider_switches_between_local_and_oss(monkeypatch, tmp_path):
    """EVIDENCE_STORAGE_PROVIDER 应选择真实存储实现。"""
    monkeypatch.setenv("EVIDENCE_STORAGE_PROVIDER", "local")
    monkeypatch.setenv("EVIDENCE_UPLOAD_ROOT", str(tmp_path / "uploads"))
    assert isinstance(build_rag_object_storage(), LocalRagObjectStorage)

    monkeypatch.setenv("EVIDENCE_STORAGE_PROVIDER", "unsupported")
    with pytest.raises(BusinessError, match="仅支持 local 或 oss"):
        build_rag_object_storage()


def test_oss_key_is_user_scoped_and_stream_uploads(tmp_path):
    """OSS key 必须包含当前用户，上传调用使用文件路径而非 bytes。"""
    bucket = FakeOssBucket()
    storage = OssRagObjectStorage(bucket=bucket, bucket_name="evidence", object_prefix="learning-evidence")
    source = tmp_path / "large.bin"
    source.write_bytes(b"large content")

    stored = storage.store_file(source, "../notes/demo.md", "42", "markdown", "text/markdown")

    assert stored.storage_type == "oss"
    assert stored.object_key.startswith("learning-evidence/42/markdown/")
    assert ".." not in stored.object_key
    assert bucket.objects[stored.object_key] == b"large content"
    assert bucket.calls[0][1] == str(source)

    material = SimpleNamespace(storage_type="oss", object_key=stored.object_key, user_id="42")
    assert storage.load_bytes(material) == b"large content"
    with pytest.raises(BusinessError, match="不属于当前用户"):
        storage.validate_object_key(stored.object_key, "43")
    with pytest.raises(BusinessError):
        storage.validate_object_key("learning-evidence/42/markdown/../43/file.md", "42")


def test_oss_multipart_streams_file_parts_and_completes(tmp_path):
    """长视频分片应直接写入 OSS multipart，不需要本地合并文件。"""
    bucket = FakeOssBucket()
    storage = OssRagObjectStorage(bucket=bucket, bucket_name="evidence", object_prefix="learning-evidence")
    first = tmp_path / "part-1.bin"
    second = tmp_path / "part-2.bin"
    first.write_bytes(b"abc")
    second.write_bytes(b"def")

    session = storage.start_multipart_upload(
        filename="lesson.mp4",
        user_id="42",
        document_type="mp4",
        content_type="video/mp4",
    )
    part_one = storage.upload_multipart_part_file(
        object_key=session.object_key,
        upload_id=session.upload_id,
        part_number=1,
        source=first,
    )
    part_two = storage.upload_multipart_part_file(
        object_key=session.object_key,
        upload_id=session.upload_id,
        part_number=2,
        source=second,
    )
    stored = storage.complete_multipart_upload(
        object_key=session.object_key,
        upload_id=session.upload_id,
        parts=[part_one, part_two],
    )

    assert stored.object_key == session.object_key
    assert bucket.multipart_calls == [
        (session.object_key, session.upload_id, 1),
        (session.object_key, session.upload_id, 2),
    ]
    assert bucket.objects[session.object_key] == b"abcdef"


def test_kafka_oss_source_downloads_and_cleans_temp_file(monkeypatch, tmp_path):
    """Kafka OSS source 必须下载到临时文件，并在 cleanup 后删除。"""
    bucket = FakeOssBucket()
    storage = OssRagObjectStorage(
        bucket=bucket,
        bucket_name="evidence",
        object_prefix="learning-evidence",
    )
    source = tmp_path / "source.pdf"
    source.write_bytes(b"pdf bytes")
    stored = storage.store_file(source, "source.pdf", "42", "pdf")
    monkeypatch.setenv("EVIDENCE_UPLOAD_TEMP_ROOT", str(tmp_path / "temp"))

    opened = open_storage_source(
        StorageSourceRef(
            storageType="oss",
            objectKey=stored.object_key,
            filename="source.pdf",
            contentType="application/pdf",
        ),
        user_id="42",
        object_storage=storage,
    )
    downloaded = opened.path
    assert downloaded.read_bytes() == b"pdf bytes"
    opened.cleanup()
    assert not downloaded.exists()


def test_kafka_oss_video_source_downloads_same_key_sidecar(monkeypatch, tmp_path):
    """OSS 视频与同名侧车字幕应一起下载，并在 worker 清理时一起删除。"""
    bucket = FakeOssBucket()
    storage = OssRagObjectStorage(
        bucket=bucket,
        bucket_name="evidence",
        object_prefix="learning-evidence",
    )
    video = tmp_path / "course.mp4"
    subtitle = tmp_path / "course.srt"
    video.write_bytes(b"video bytes")
    subtitle.write_text("1\n00:00:00,000 --> 00:00:01,000\n课程字幕\n", encoding="utf-8")
    stored = storage.store_file(video, "course.mp4", "42", "mp4", "video/mp4")
    sidecar_key = storage.store_video_sidecar_file(subtitle, stored.object_key or "", "42")
    monkeypatch.setenv("EVIDENCE_UPLOAD_TEMP_ROOT", str(tmp_path / "temp"))

    opened = open_storage_source(
        StorageSourceRef(
            storageType="oss",
            objectKey=stored.object_key,
            filename="course.mp4",
            contentType="video/mp4",
        ),
        user_id="42",
        object_storage=storage,
    )
    downloaded = opened.path
    downloaded_subtitle = downloaded.with_suffix(".srt")
    assert sidecar_key.endswith(".srt")
    assert downloaded_subtitle.read_text(encoding="utf-8") == subtitle.read_text(encoding="utf-8")
    opened.cleanup()
    assert not downloaded.exists()
    assert not downloaded_subtitle.exists()


@pytest.mark.anyio
async def test_stream_upload_reads_bounded_blocks(monkeypatch, tmp_path):
    """HTTP 上传每次只读取固定大小块，并清理返回的临时路径。"""
    monkeypatch.setenv("EVIDENCE_UPLOAD_TEMP_ROOT", str(tmp_path))
    upload = UploadFile(file=BytesIO(b"x" * (1024 * 1024 + 17)), filename="large.bin")
    read_sizes: list[int] = []
    original_read = upload.read

    async def recording_read(size: int = -1) -> bytes:
        """记录读取大小后继续执行 Starlette 文件读取。"""
        read_sizes.append(size)
        return await original_read(size)

    upload.read = recording_read  # type: ignore[method-assign]
    path = await stream_upload_to_temp(upload)
    try:
        assert path.stat().st_size == 1024 * 1024 + 17
        assert read_sizes
        assert max(read_sizes) <= 1024 * 1024
    finally:
        path.unlink(missing_ok=True)


def test_merge_chunks_does_not_require_read_bytes(monkeypatch, tmp_path):
    """分片合并通过流式复制，不依赖 merged.read_bytes。"""
    directory = tmp_path / "chunks"
    directory.mkdir()
    (directory / "chunk-00000.part").write_bytes(b"abc")
    (directory / "chunk-00001.part").write_bytes(b"def")
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(AssertionError("不应读取整文件")))
    merged = merge_chunks(directory, "demo.mp4", 2, 6)
    assert merged.stat().st_size == 6
