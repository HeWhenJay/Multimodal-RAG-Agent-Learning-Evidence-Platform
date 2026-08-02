"""公开视频链接控制服务测试。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace

import pytest

from app.core.result import BusinessError
from app.repositories.rag_control import DatabaseRagControlTransaction, IndexJobSchedule, MaterialRecord
from app.schemas.rag_control import RagIndexRemoteVideoPublicRequest
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
        usage: tuple[int, int, int] = (0, 0, 0),
    ) -> None:
        self.current = existing
        self.usage = usage
        self.inserted: dict | None = None
        self.enqueued: dict | None = None

    def find_material_by_public_url(self, public_url: str, user_id: str):
        assert public_url == "https://www.bilibili.com/video/BV1xx411c7mD"
        assert user_id == "7"
        return self.current

    def remote_video_import_usage(self, user_id: str):
        assert user_id == "7"
        return self.usage

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


@pytest.mark.parametrize(
    ("usage", "message"),
    [
        ((10, 0, 0), "今日公开视频接入次数已达上限"),
        ((0, 2, 0), "正在处理的公开视频较多"),
        ((0, 0, 32), "公开视频处理队列繁忙"),
    ],
)
def test_remote_video_import_enforces_resource_quotas(usage, message) -> None:
    """用户日额度、用户并发和全局队列上限均在建档前执行。"""
    transaction = RemoteVideoTransaction(usage=usage)

    with pytest.raises(BusinessError, match=message):
        service_for(transaction).import_remote_video(
            RagIndexRemoteVideoPublicRequest(
                url="https://www.bilibili.com/video/BV1xx411c7mD",
                confirmedAuthorized=True,
            ),
            "7",
        )

    assert transaction.inserted is None
    assert transaction.enqueued is None


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
    assert len(cursor.executed) == 4
    assert "pg_advisory_xact_lock" in str(cursor.executed[0][0])
    assert cursor.executed[0][1] == ("rag-remote-video:admission",)
    assert cursor.executed[1][1] == ("rag-remote-video:user:7",)
    assert cursor.executed[2][1] == (
        "rag-remote-video:url:7:https://www.bilibili.com/video/BV1xx411c7mD",
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


def test_remote_video_reindex_enforces_daily_quota() -> None:
    """远程资料重建同样计入近 24 小时任务配额。"""
    transaction = RemoteVideoTransaction(
        existing=material_record(status="READY"),
        usage=(10, 0, 0),
    )

    with pytest.raises(BusinessError, match="今日公开视频接入次数已达上限"):
        service_for(transaction).reindex_material(31, True, "7")

    assert transaction.enqueued is None
