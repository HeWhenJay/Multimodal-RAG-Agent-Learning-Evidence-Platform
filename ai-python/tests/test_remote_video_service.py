"""公开视频链接控制服务测试。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest

from app.core.result import BusinessError
from app.repositories.rag_control import DatabaseRagControlTransaction, IndexJobSchedule, MaterialRecord
from app.schemas.rag_control import (
    RagIndexRemoteVideoBatchPublicRequest,
    RagIndexRemoteVideoPublicRequest,
    RagMaterialResponse,
)
from app.services.rag_control_service import RagControlService


def material_record(*, status: str = "PENDING", url: str = "https://www.bilibili.com/video/BV1xx411c7mD") -> MaterialRecord:
    """构造远程视频资料记录。"""
    return MaterialRecord(
        id=31,
        title="Bilibili 视频 BV1xx411c7mD",
        user_id="7",
        document_type="mp4",
        source="bilibili",
        status=status,
        parser=None,
        document_summary=None,
        chunk_count=0,
        original_filename="BV1xx411c7mD.mp4",
        original_file_path=None,
        storage_type="remote",
        object_key=None,
        public_url=url,
        active_index_job_id="job-remote",
        index_request_version=1,
        created_at=None,
        updated_at=None,
    )


class RemoteVideoTransaction:
    """记录链接导入建档和任务投递参数。"""

    def __init__(
        self,
        existing: MaterialRecord | None = None,
    ) -> None:
        self.current = existing
        self.inserted: dict | None = None
        self.enqueued: dict | None = None

    def find_material_by_public_url(self, public_url: str, user_id: str, platform: str = "bilibili"):
        assert public_url == "https://www.bilibili.com/video/BV1xx411c7mD"
        assert user_id == "7"
        assert platform == "bilibili"
        return self.current

    def insert_material(self, **kwargs):
        self.inserted = kwargs
        self.current = material_record()
        return self.current

    def enqueue_index_job(self, **kwargs):
        self.enqueued = kwargs
        self.current = replace(kwargs["material"], status=kwargs["status"])
        return IndexJobSchedule(self.current, "job-remote", "LOCAL")

    def find_material(self, material_id: int, user_id: str):
        assert material_id == 31 and user_id == "7"
        return self.current

    def list_progress(self, material_id: int, limit: int):
        return []


class RemoteVideoRepository:
    """每次事务复用同一内存状态。"""

    def __init__(self, transaction: RemoteVideoTransaction) -> None:
        self.value = transaction

    @contextmanager
    def transaction(self):
        yield self.value


class DouyinRemoteVideoTransaction(RemoteVideoTransaction):
    """验证抖音平台参数会进入幂等查询和资料建档。"""

    def find_material_by_public_url(self, public_url: str, user_id: str, platform: str = "bilibili"):
        assert public_url == "https://www.douyin.com/video/6961737553342991651"
        assert user_id == "7"
        assert platform == "douyin"
        return self.current

    def insert_material(self, **kwargs):
        self.inserted = kwargs
        self.current = replace(
            material_record(url=kwargs["public_url"]),
            title=kwargs["title"],
            source=kwargs["source"],
            original_filename=kwargs["original_filename"],
            public_url=kwargs["public_url"],
        )
        return self.current


def service_for(transaction: RemoteVideoTransaction) -> RagControlService:
    """注入最小依赖，避免测试连接数据库或模型。"""
    return RagControlService(
        repository=RemoteVideoRepository(transaction),
        store=object(),
        parser_router=object(),
        object_storage=object(),
        task_repository=object(),
    )


def test_remote_video_import_creates_one_durable_job() -> None:
    """公开链接只建档和投递任务，不在 API 请求中下载视频。"""
    transaction = RemoteVideoTransaction()
    response = service_for(transaction).import_remote_video(
        RagIndexRemoteVideoPublicRequest(
            url="https://www.bilibili.com/video/BV1xx411c7mD?spm_id_from=333.1",
            highPrecision=True,
            confirmedAuthorized=True,
        ),
        "7",
    )

    assert response.id == 31
    assert response.status == "PENDING"
    assert transaction.inserted and transaction.inserted["storage_type"] == "remote"
    assert transaction.enqueued and transaction.enqueued["operation"] == "INDEX_REMOTE_VIDEO"
    assert transaction.enqueued["source_ref"] == {
        "type": "REMOTE_VIDEO",
        "platform": "bilibili",
        "url": "https://www.bilibili.com/video/BV1xx411c7mD",
        "videoId": "BV1xx411c7mD",
    }
    assert transaction.enqueued["high_precision"] is True


def test_douyin_video_import_uses_platform_aware_idempotence_and_transcript_filename() -> None:
    """抖音资料建档使用 douyin 来源和 SRT 转写文件名。"""
    transaction = DouyinRemoteVideoTransaction()

    response = service_for(transaction).import_remote_video(
        RagIndexRemoteVideoPublicRequest(
            url="https://www.douyin.com/video/6961737553342991651",
            confirmedAuthorized=True,
        ),
        "7",
    )

    assert response.source == "douyin"
    assert transaction.inserted and transaction.inserted["original_filename"] == "6961737553342991651.srt"
    assert transaction.enqueued and transaction.enqueued["source_ref"]["platform"] == "douyin"


def test_remote_video_import_is_idempotent_for_active_material() -> None:
    """同一用户重复提交相同 URL 时复用现有资料。"""
    transaction = RemoteVideoTransaction(existing=material_record(status="READY"))

    response = service_for(transaction).import_remote_video(
        RagIndexRemoteVideoPublicRequest(
            url="https://www.bilibili.com/video/BV1xx411c7mD",
            confirmedAuthorized=True,
        ),
        "7",
    )

    assert response.id == 31
    assert response.status == "READY"
    assert transaction.inserted is None
    assert transaction.enqueued is None


def test_remote_video_import_requires_rights_confirmation() -> None:
    """未确认内容处理权时不能创建任务。"""
    with pytest.raises(BusinessError, match="有权处理"):
        service_for(RemoteVideoTransaction()).import_remote_video(
            RagIndexRemoteVideoPublicRequest(
                url="https://www.bilibili.com/video/BV1xx411c7mD",
                confirmedAuthorized=False,
            ),
            "7",
        )


def test_failed_remote_video_reuses_material_for_retry() -> None:
    """失败任务再次提交时复用资料并创建新索引任务，不重复建档。"""
    transaction = RemoteVideoTransaction(existing=material_record(status="FAILED"))

    response = service_for(transaction).import_remote_video(
        RagIndexRemoteVideoPublicRequest(
            url="https://www.bilibili.com/video/BV1xx411c7mD",
            confirmedAuthorized=True,
        ),
        "7",
    )

    assert response.status == "PENDING"
    assert transaction.inserted is None
    assert transaction.enqueued and transaction.enqueued["material"].id == 31


def test_remote_video_batch_accepts_more_than_two_urls_and_reports_partial_results(monkeypatch) -> None:
    """批量接入不受两条限制，重复和不支持链接只影响各自结果。"""
    service = service_for(RemoteVideoTransaction())
    enqueued_urls: list[str] = []

    def enqueue(remote, high_precision: bool, user_id: str):
        """记录规范化 URL，避免批量服务测试访问数据库。"""
        enqueued_urls.append(remote.canonical_url)
        material = RagMaterialResponse(
            id=len(enqueued_urls),
            title=remote.placeholder_title,
            userId=user_id,
            documentType="mp4",
            source=remote.platform,
            status="PENDING",
            storageType="remote",
            publicUrl=remote.canonical_url,
        )
        return material, True

    monkeypatch.setattr(service, "_enqueue_remote_video", enqueue)
    response = service.import_remote_videos(
        RagIndexRemoteVideoBatchPublicRequest(
            text=(
                "【Java 面试】https://www.bilibili.com/video/BV1yT411H7YK?p=32&vd_source=tracking\n"
                "https://www.bilibili.com/video/BV1xx411c7mD\n"
                "https://www.bilibili.com/video/BV1nx411u79K\n"
                "https://m.bilibili.com/video/BV1yT411H7YK?p=32&spm_id_from=duplicate\n"
                "https://www.douyin.com/video/6961737553342991651"
            ),
            highPrecision=True,
            confirmedAuthorized=True,
        ),
        "7",
    )

    assert len(enqueued_urls) == 4
    assert enqueued_urls[0] == "https://www.bilibili.com/video/BV1yT411H7YK?p=32"
    assert response.candidateCount == 5
    assert response.queuedCount == 4
    assert response.duplicateCount == 1
    assert response.rejectedCount == 0
    assert [item.status for item in response.items] == [
        "QUEUED",
        "QUEUED",
        "QUEUED",
        "DUPLICATE",
        "QUEUED",
    ]


def test_remote_video_batch_requires_an_extracted_url() -> None:
    """只有标题而没有链接的批次不能创建空任务。"""
    with pytest.raises(BusinessError, match="未识别到"):
        service_for(RemoteVideoTransaction()).import_remote_videos(
            RagIndexRemoteVideoBatchPublicRequest(text="这里只是一段标题", confirmedAuthorized=True),
            "7",
        )


def test_remote_video_lookup_uses_transaction_advisory_lock() -> None:
    """同一用户和 URL 的并发导入应在查询前获取事务级锁。"""

    class RecordingCursor:
        def __init__(self) -> None:
            self.executed: list[tuple[object, tuple]] = []

        def execute(self, statement, parameters) -> None:
            self.executed.append((statement, parameters))

        def fetchone(self):
            return None

    cursor = RecordingCursor()
    transaction = DatabaseRagControlTransaction(cursor, "learning_evidence")

    assert transaction.find_material_by_public_url(
        "https://www.bilibili.com/video/BV1xx411c7mD",
        "7",
    ) is None
    assert len(cursor.executed) == 2
    assert "pg_advisory_xact_lock" in str(cursor.executed[0][0])
    assert cursor.executed[0][1] == (
        "rag-remote-video:url:7:bilibili:https://www.bilibili.com/video/BV1xx411c7mD",
    )
    assert "source = %s" in str(cursor.executed[1][0])
    assert cursor.executed[1][1] == (
        "7",
        "bilibili",
        "https://www.bilibili.com/video/BV1xx411c7mD",
    )


def test_remote_video_reindex_reuses_canonical_url() -> None:
    """远程资料重建索引时重新创建下载任务，不要求本地原文件。"""
    transaction = RemoteVideoTransaction(existing=material_record(status="READY"))

    response = service_for(transaction).reindex_material(31, True, "7")

    assert response.status == "REINDEXING"
    assert transaction.enqueued and transaction.enqueued["operation"] == "INDEX_REMOTE_VIDEO"
    assert transaction.enqueued["source_ref"]["url"] == "https://www.bilibili.com/video/BV1xx411c7mD"


def test_active_remote_video_reindex_reuses_current_job() -> None:
    """远程视频已有活动任务时不重复投递重建任务。"""
    transaction = RemoteVideoTransaction(existing=material_record(status="REINDEXING"))

    response = service_for(transaction).reindex_material(31, True, "7")

    assert response.status == "REINDEXING"
    assert transaction.enqueued is None
