"""视频分片上传并发收尾幂等测试。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import threading
import time
from typing import Iterator

from app.repositories.rag_control import MaterialRecord
from app.schemas.rag_control import RagMaterialResponse
from app.services.rag_control_service import RagControlService, chunk_filename
from app.storage.object_storage import OssRagObjectStorage, StoredObject


class InMemoryMaterialRepository:
    """只实现分片收尾测试需要的资料读取和进度读取。"""

    def __init__(self) -> None:
        self.material: MaterialRecord | None = None

    @contextmanager
    def transaction(self) -> Iterator["InMemoryMaterialRepository"]:
        """模拟仓储事务上下文。"""

        yield self

    def find_material(self, material_id: int, user_id: str) -> MaterialRecord | None:
        """按资料 ID 和用户 ID 返回 fake 资料。"""

        if self.material and self.material.id == material_id and self.material.user_id == user_id:
            return self.material
        return None

    def list_progress(self, material_id: int, limit: int):  # noqa: ANN001
        """本测试不关注进度事件。"""

        return []


class CountingChunkUploadService(RagControlService):
    """统计 upload_material 调用次数，模拟真实建档耗时。"""

    def __init__(self, repository: InMemoryMaterialRepository) -> None:
        super().__init__(
            repository=repository,
            store=object(),
            parser_router=object(),
            object_storage=object(),
            task_repository=object(),
        )
        self.repository = repository
        self.upload_calls = 0
        self._lock = threading.Lock()

    def upload_material(self, **kwargs) -> RagMaterialResponse:  # noqa: ANN003
        """模拟上传完成后只创建一条资料记录。"""

        with self._lock:
            self.upload_calls += 1
        time.sleep(0.05)
        material = MaterialRecord(
            id=101,
            title=str(kwargs["filename"]),
            user_id=str(kwargs["user_id"]),
            document_type="mp4",
            source="upload",
            status="PARSING",
            parser=None,
            document_summary=None,
            chunk_count=0,
            original_filename=str(kwargs["filename"]),
            original_file_path=str(kwargs["source_path"]),
            storage_type="local",
            object_key=None,
            public_url=None,
            active_index_job_id="job-101",
            index_request_version=1,
            created_at=datetime(2026, 7, 24),
            updated_at=datetime(2026, 7, 24),
        )
        self.repository.material = material
        return RagMaterialResponse(
            id=material.id,
            title=material.title,
            userId=material.user_id,
            documentType=material.document_type,
            source=material.source,
            status=material.status,
            parser=material.parser,
            documentSummary=material.document_summary,
            chunkCount=material.chunk_count,
            originalFilename=material.original_filename,
            originalFilePath=material.original_file_path,
            storageType=material.storage_type,
            objectKey=material.object_key,
            publicUrl=material.public_url,
            createdAt=material.created_at,
            updatedAt=material.updated_at,
        )


class MultipartFakeBucket:
    """模拟 OSS multipart，校验服务层不创建本地合并视频。"""

    def __init__(self) -> None:
        self.uploads: dict[tuple[str, str], dict[int, tuple[str, bytes]]] = {}
        self.objects: dict[str, bytes] = {}
        self.part_calls: list[int] = []

    def init_multipart_upload(self, key: str, headers=None):  # noqa: ANN001
        """创建稳定的测试会话。"""
        upload_id = f"upload-{len(self.uploads) + 1}"
        self.uploads[(key, upload_id)] = {}
        return type("InitResult", (), {"upload_id": upload_id})()

    def upload_part(self, key: str, upload_id: str, part_number: int, data):  # noqa: ANN001
        """读取当前 part 文件流，保留 ETag。"""
        content = data.read() if hasattr(data, "read") else bytes(data)
        etag = f"etag-{part_number}-{len(content)}"
        self.uploads[(key, upload_id)][part_number] = (etag, content)
        self.part_calls.append(part_number)
        return type("PartResult", (), {"etag": etag})()

    def complete_multipart_upload(self, key: str, upload_id: str, parts, headers=None):  # noqa: ANN001
        """按 OSS SDK PartInfo 的序号聚合测试对象。"""
        uploaded = self.uploads[(key, upload_id)]
        self.objects[key] = b"".join(uploaded[part.part_number][1] for part in parts)

    def delete_object(self, key: str) -> None:
        """兼容资料建档失败时的清理接口。"""
        self.objects.pop(key, None)


class CountingOssChunkUploadService(CountingChunkUploadService):
    """将已完成的 OSS 对象映射为测试资料，避免连接真实数据库。"""

    def __init__(self, repository: InMemoryMaterialRepository, storage: OssRagObjectStorage) -> None:
        super().__init__(repository)
        self.object_storage = storage

    def _create_material_from_stored(  # noqa: PLR0913
        self,
        *,
        stored: StoredObject,
        source_path,
        filename: str,
        document_type: str,
        content_type: str | None,
        high_precision: bool,
        user_id: str,
    ) -> RagMaterialResponse:
        """模拟 OSS 对象已建档，统计是否重复创建资料。"""
        with self._lock:
            self.upload_calls += 1
        material = MaterialRecord(
            id=202,
            title=filename,
            user_id=user_id,
            document_type=document_type,
            source="upload",
            status="PARSING",
            parser=None,
            document_summary=None,
            chunk_count=0,
            original_filename=filename,
            original_file_path=stored.source_path,
            storage_type="oss",
            object_key=stored.object_key,
            public_url=stored.public_url,
            active_index_job_id="job-202",
            index_request_version=1,
            created_at=datetime(2026, 7, 25),
            updated_at=datetime(2026, 7, 25),
        )
        self.repository.material = material
        return RagMaterialResponse(
            id=material.id,
            title=material.title,
            userId=material.user_id,
            documentType=material.document_type,
            source=material.source,
            status=material.status,
            parser=material.parser,
            documentSummary=material.document_summary,
            chunkCount=material.chunk_count,
            originalFilename=material.original_filename,
            originalFilePath=material.original_file_path,
            storageType=material.storage_type,
            objectKey=material.object_key,
            publicUrl=material.public_url,
            createdAt=material.created_at,
            updatedAt=material.updated_at,
        )


def test_concurrent_final_chunks_create_only_one_material(tmp_path) -> None:
    """两个最后分片同时收尾时，只允许一个请求执行 merge 和资料建档。"""

    directory = tmp_path / "chunks" / "user-1" / "upload-1"
    directory.mkdir(parents=True)
    (directory / chunk_filename(0)).write_bytes(b"abc")
    (directory / chunk_filename(1)).write_bytes(b"def")
    repository = InMemoryMaterialRepository()
    service = CountingChunkUploadService(repository)
    responses = []

    def finish(chunk_index: int) -> None:
        response = service._finish_chunk_upload(
            directory=directory,
            filename="lesson.mp4",
            upload_id="upload-1",
            chunk_index=chunk_index,
            total_chunks=2,
            total_size=6,
            content_type="video/mp4",
            high_precision=False,
            user_id="user-1",
        )
        responses.append(response)

    threads = [threading.Thread(target=finish, args=(index,)) for index in (0, 1)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert service.upload_calls == 1
    assert len(responses) == 2
    assert all(response.completed for response in responses)
    assert {response.material.id for response in responses if response.material} == {101}


def test_oss_chunk_upload_keeps_only_metadata_locally(tmp_path) -> None:
    """OSS 模式逐片直传，不在 EVIDENCE_UPLOAD_CHUNK_ROOT 产生完整视频。"""
    repository = InMemoryMaterialRepository()
    bucket = MultipartFakeBucket()
    storage = OssRagObjectStorage(bucket=bucket, bucket_name="evidence", object_prefix="learning-evidence")
    service = CountingOssChunkUploadService(repository, storage)
    service._chunk_root = lambda: tmp_path / "chunks"  # type: ignore[method-assign]
    first = tmp_path / "first.part"
    second = tmp_path / "second.part"
    first.write_bytes(b"abc")
    second.write_bytes(b"def")

    first_response = service.upload_chunk_file(
        source_path=first,
        filename="lesson.mp4",
        upload_id="oss-direct-unique",
        chunk_index=0,
        total_chunks=2,
        total_size=6,
        content_type="video/mp4",
        high_precision=False,
        user_id="user-1",
    )
    retry_response = service.upload_chunk_file(
        source_path=first,
        filename="lesson.mp4",
        upload_id="oss-direct-unique",
        chunk_index=0,
        total_chunks=2,
        total_size=6,
        content_type="video/mp4",
        high_precision=False,
        user_id="user-1",
    )
    completed_response = service.upload_chunk_file(
        source_path=second,
        filename="lesson.mp4",
        upload_id="oss-direct-unique",
        chunk_index=1,
        total_chunks=2,
        total_size=6,
        content_type="video/mp4",
        high_precision=False,
        user_id="user-1",
    )

    directory = tmp_path / "chunks" / "user-1" / "oss-direct-unique"
    assert not first_response.completed
    assert not retry_response.completed
    assert completed_response.completed
    assert service.upload_calls == 1
    assert bucket.part_calls == [1, 2]
    assert len(bucket.objects) == 1
    assert next(iter(bucket.objects.values())) == b"abcdef"
    assert (directory / "oss-multipart.json").is_file()
    assert (directory / "material.id").read_text(encoding="utf-8") == "202"
    assert not list(directory.glob("chunk-*.part"))
    assert not list(directory.glob("merged-*"))
