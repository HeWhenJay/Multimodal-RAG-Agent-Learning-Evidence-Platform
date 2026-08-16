"""当前项目主动同步 DSH 插件本地 v2 知识库的边界测试。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from app.api.auth import get_auth_service
from app.api.dsh_local_sync import get_dsh_local_sync_service
from app.auth.service import AuthBusinessError
from app.core.result import BusinessError
from app.dsh_local_sync import (
    DshLocalDocument,
    DshLocalDocumentFailure,
    DshLocalLibraryReader,
    DshLocalSyncRecord,
    DshLocalSyncService,
)
from app.main import app
from app.repositories.rag_control import IndexJobSchedule, MaterialRecord
from app.schemas.auth import AuthUserResponse
from app.schemas.dsh_local_sync import DshLocalSyncResult, DshLocalSyncStatus
from app.services.rag_control_service import RagControlService


# 按插件真实 v2 文件约定创建最小本地知识库。
def write_store(root: Path, documents: list[dict], *, index_lines: list[object] | None = None) -> Path:
    """写入 manifest、JSONL sidecar 与按 ID 哈希命名的单条资料。"""
    store = root / "knowledge.json"
    document_root = Path(f"{store}.documents")
    document_root.mkdir(parents=True)
    records: list[dict] = []
    for sequence, document in enumerate(documents, start=1):
        filename = f"{hashlib.sha256(document['id'].encode('utf-8')).hexdigest()}.json"
        (document_root / filename).write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        records.append(
            {
                "sequence": sequence,
                "id": document["id"],
                "title": document.get("title", "测试资料"),
                "source": document.get("source", "测试"),
                "contentFile": filename,
            }
        )
    visible_lines = records if index_lines is None else index_lines
    Path(f"{store}.index.jsonl").write_text(
        "".join(f"{json.dumps(item, ensure_ascii=False)}\n" for item in visible_lines),
        encoding="utf-8",
    )
    store.write_text(json.dumps({"version": 2, "documentCount": len(visible_lines)}), encoding="utf-8")
    return store


def sample_document(document_id: str = "doc-1", content: str = "# RAG\n\n递归切块保留标题结构。") -> dict:
    """构造同时包含 Markdown 摘要和插件分类审计字段的资料。"""
    return {
        "id": document_id,
        "title": "RAG 学习笔记",
        "source": "用户粘贴文本",
        "content": content,
        "summary": "## 知识点摘要\n\n- 递归切块保留结构。",
        "summarySource": "extractive",
        "systemCategory": "技术学习",
        "userCategory": "RAG",
        "createdAt": "2026-08-16T10:00:00.000Z",
    }


def test_reader_loads_v2_document_and_audit_metadata(tmp_path: Path) -> None:
    """reader 必须按哈希文件名读取原文，并保留插件元数据供独立审计。"""
    store = write_store(tmp_path, [sample_document()])

    documents = list(DshLocalLibraryReader(store).documents())

    assert len(documents) == 1
    document = documents[0]
    assert isinstance(document, DshLocalDocument)
    assert document.document_id == "doc-1"
    assert document.content_hash == hashlib.sha256(document.content.encode("utf-8")).hexdigest()
    assert document.summary == "## 知识点摘要\n\n- 递归切块保留结构。"
    assert document.system_category == "技术学习"
    assert document.user_category == "RAG"


def test_reader_rejects_broken_jsonl_before_returning_any_document(tmp_path: Path) -> None:
    """全局 JSONL 损坏必须在项目发生任何写入前整体拒绝。"""
    store = write_store(tmp_path, [sample_document()])
    Path(f"{store}.index.jsonl").write_text('{"id":"doc-1"}\n{broken\n', encoding="utf-8")
    store.write_text(json.dumps({"version": 2, "documentCount": 2}), encoding="utf-8")

    with pytest.raises(BusinessError, match="第 2 行损坏"):
        list(DshLocalLibraryReader(store).documents())


def test_reader_isolates_missing_or_oversize_content_file(tmp_path: Path, monkeypatch) -> None:
    """单条原文缺失或超限只生成 FAILED 条目，其余健康资料继续。"""
    store = write_store(tmp_path, [sample_document("doc-ok", "短正文"), sample_document("doc-large", "x" * 5000)])
    monkeypatch.setattr("app.dsh_local_sync.MAX_DOCUMENT_BYTES", 1000)

    documents = list(DshLocalLibraryReader(store).documents())

    assert isinstance(documents[0], DshLocalDocument)
    assert isinstance(documents[1], DshLocalDocumentFailure)
    assert documents[1].document_id == "doc-large"
    assert "10MB" in documents[1].message


def test_reader_rejects_invalid_content_filename_without_path_escape(tmp_path: Path) -> None:
    """索引不能借 contentFile 读取哈希命名规则之外的文件。"""
    record = {"sequence": 1, "id": "doc-1", "title": "越界资料", "contentFile": "../outside.json"}
    store = write_store(tmp_path, [], index_lines=[record])

    documents = list(DshLocalLibraryReader(store).documents())

    assert len(documents) == 1
    assert isinstance(documents[0], DshLocalDocumentFailure)
    assert "非法资料标识" in documents[0].message


class FakeSyncRepository:
    """在内存中模拟 owner 和 documentId 映射，验证服务编排。"""

    def __init__(self) -> None:
        self.owner: str | None = None
        self.records: dict[str, DshLocalSyncRecord] = {}
        self.metadata_refreshes = 0

    def claim_owner(self, user_id: str) -> None:
        if self.owner is None:
            self.owner = user_id
        if self.owner != user_id:
            raise BusinessError("DSH 本地同步已绑定到另一个项目账号")

    def owner_matches(self, user_id: str) -> bool:
        return self.owner is None or self.owner == user_id

    def list_records(self, user_id: str) -> dict[str, DshLocalSyncRecord]:
        assert self.owner == user_id
        return dict(self.records)

    def upsert(self, user_id: str, document: DshLocalDocument, material_id: int) -> None:
        assert self.owner == user_id
        self.records[document.document_id] = DshLocalSyncRecord(
            document_id=document.document_id,
            content_hash=document.content_hash,
            material_id=material_id,
            material_current=True,
            updated_at=datetime(2026, 8, 16, 10, 0),
        )

    def refresh_metadata(self, user_id: str, document: DshLocalDocument, material_id: int) -> None:
        self.metadata_refreshes += 1
        self.upsert(user_id, document, material_id)

    def status(self, user_id: str) -> tuple[int, datetime | None]:
        return len(self.records), None


class FakeReader:
    """提供固定 reader 结果，不访问真实 DSH 目录。"""

    def __init__(self, documents: list[DshLocalDocument | DshLocalDocumentFailure]) -> None:
        self.values = documents
        self.store_path = Path("C:/server-only/knowledge.json")

    def documents(self):
        yield from self.values

    def overview(self):
        return True, len(self.values), "DSH 插件本地知识库可读取"


class FakeRagSyncService:
    """记录 stable source 和认证用户，并返回固定项目资料 ID。"""

    def __init__(self) -> None:
        self.calls: list[tuple[object, str]] = []

    def sync_text_material(self, request, user_id: str):
        self.calls.append((request, user_id))
        return SimpleNamespace(id=91, status="PENDING")


def local_document(content: str = "正文 v1") -> DshLocalDocument:
    """构造服务层可直接同步的插件资料。"""
    return DshLocalDocument(
        document_id="doc-1",
        title="同步资料",
        source="插件来源",
        content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        summary="## 摘要\n\n- 审计信息",
        system_category="系统分类",
        user_category="用户分类",
    )


def test_sync_is_idempotent_refreshes_metadata_and_updates_changed_content() -> None:
    """相同正文不重复投递 durable job，变化正文复用稳定 source 更新。"""
    repository = FakeSyncRepository()
    reader = FakeReader([local_document()])
    rag_service = FakeRagSyncService()
    service = DshLocalSyncService(reader=reader, repository=repository, rag_service=rag_service)

    first = service.sync("42")
    second = service.sync("42")
    reader.values = [local_document("正文 v2")]
    third = service.sync("42")

    assert (first.createdCount, second.skippedCount, third.updatedCount) == (1, 1, 1)
    assert repository.metadata_refreshes == 1
    assert len(rag_service.calls) == 2
    assert all(call[0].source == "dsh-local:doc-1" and call[1] == "42" for call in rag_service.calls)


def test_sync_continues_after_one_document_failure_and_rejects_second_owner() -> None:
    """单条 reader 失败不阻断健康资料，唯一 owner 防止共享部署跨账号复制。"""
    repository = FakeSyncRepository()
    reader = FakeReader(
        [
            DshLocalDocumentFailure("broken", "损坏资料", "原文无法读取"),
            local_document(),
        ]
    )
    service = DshLocalSyncService(reader=reader, repository=repository, rag_service=FakeRagSyncService())

    result = service.sync("42")

    assert (result.scannedCount, result.createdCount, result.failedCount) == (2, 1, 1)
    with pytest.raises(BusinessError, match="另一个项目账号"):
        service.sync("84")


# 构造 RagControlService 所需的最小资料记录。
def material_record(*, material_id: int = 7, status: str = "READY") -> MaterialRecord:
    """返回手工文本资料快照。"""
    return MaterialRecord(
        id=material_id,
        title="同步资料",
        user_id="42",
        document_type="markdown",
        source="dsh-local:doc-1",
        status=status,
        parser="python-manual-text",
        document_summary=None,
        chunk_count=2,
        original_filename=None,
        original_file_path=None,
        storage_type="manual",
        object_key=None,
        public_url=None,
        active_index_job_id=None,
        index_request_version=1,
        created_at=None,
        updated_at=None,
    )


class FakeRagTransaction:
    """模拟 stable source 查找、hash 去重和 durable job 投递。"""

    def __init__(self, *, existing: MaterialRecord | None, existing_hash: str | None) -> None:
        self.material = existing
        self.existing_hash = existing_hash
        self.enqueue_count = 0
        self.insert_count = 0

    def find_material_by_source(self, source: str, user_id: str):
        assert source == "dsh-local:doc-1" and user_id == "42"
        return self.material

    def find_latest_index_content_hash(self, material_id: int, user_id: str):
        assert material_id == 7 and user_id == "42"
        return self.existing_hash

    def insert_material(self, **kwargs):
        self.insert_count += 1
        self.material = replace(material_record(status="PENDING"), title=kwargs["title"])
        return self.material

    def update_manual_material(self, material_id: int, user_id: str, *, title: str, source: str):
        assert material_id == 7 and user_id == "42"
        self.material = replace(self.material, title=title, source=source)
        return self.material

    def enqueue_index_job(self, **kwargs):
        self.enqueue_count += 1
        self.material = replace(kwargs["material"], status=kwargs["status"])
        return IndexJobSchedule(material=self.material, job_id="job-test", delivery_mode="LOCAL")

    def list_progress(self, material_id: int, limit: int):
        return []


class FakeRagRepository:
    """将单个内存事务交给 RagControlService。"""

    def __init__(self, transaction: FakeRagTransaction) -> None:
        self.value = transaction

    @contextmanager
    def transaction(self):
        yield self.value


def test_rag_sync_text_material_uses_content_hash_to_avoid_duplicate_job() -> None:
    """映射写入中断后，同内容重试只能补映射，不能生成第二个索引任务。"""
    content = "同一份正文"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    transaction = FakeRagTransaction(existing=material_record(), existing_hash=digest)
    service = RagControlService(
        repository=FakeRagRepository(transaction),
        store=object(),
        parser_router=object(),
        object_storage=object(),
        task_repository=object(),
    )

    response = service.sync_text_material(
        SimpleNamespace(title="同步资料", documentType="markdown", source="dsh-local:doc-1", content=content),
        "42",
    )

    assert response.id == 7
    assert transaction.enqueue_count == 0
    assert transaction.insert_count == 0


def test_rag_sync_text_material_enqueues_changed_content() -> None:
    """稳定来源的正文变化必须创建新 request version 的 durable INDEX_TEXT 任务。"""
    transaction = FakeRagTransaction(existing=material_record(), existing_hash="0" * 64)
    service = RagControlService(
        repository=FakeRagRepository(transaction),
        store=object(),
        parser_router=object(),
        object_storage=object(),
        task_repository=object(),
    )

    response = service.sync_text_material(
        SimpleNamespace(title="同步资料 v2", documentType="markdown", source="dsh-local:doc-1", content="变化正文"),
        "42",
    )

    assert response.id == 7
    assert response.status == "REINDEXING"
    assert transaction.enqueue_count == 1


class StaticAuthService:
    """为同步路由提供固定登录用户。"""

    def current_user(self, token: str | None) -> AuthUserResponse:
        if token != "test-token":
            raise AuthBusinessError("登录状态已失效")
        return AuthUserResponse(id=42, account="sync-user", displayName="同步用户", role="USER")


class StubRouteSyncService:
    """断言路由只传入认证用户，不接受浏览器伪造范围。"""

    def __init__(self) -> None:
        self.users: list[str] = []

    def status(self, user_id: str) -> DshLocalSyncStatus:
        self.users.append(user_id)
        return DshLocalSyncStatus(
            configured=True,
            readable=True,
            documentCount=2,
            syncedDocumentCount=1,
            pendingDocumentCount=1,
            message="可同步",
        )

    def sync(self, user_id: str) -> DshLocalSyncResult:
        self.users.append(user_id)
        return DshLocalSyncResult(
            scannedCount=0,
            createdCount=0,
            updatedCount=0,
            skippedCount=0,
            failedCount=0,
            items=[],
        )


def test_routes_require_login_and_do_not_expose_or_accept_store_path() -> None:
    """同步 API 只信任 Bearer 会话，响应不包含服务器路径，请求体不能改变范围。"""
    service = StubRouteSyncService()
    app.dependency_overrides[get_auth_service] = StaticAuthService
    app.dependency_overrides[get_dsh_local_sync_service] = lambda: service
    client = TestClient(app)
    try:
        anonymous = client.get("/api/dsh-local-sync/status")
        status = client.get("/api/dsh-local-sync/status", headers={"Authorization": "Bearer test-token"})
        synced = client.post(
            "/api/dsh-local-sync/sync",
            headers={"Authorization": "Bearer test-token"},
            json={"userId": "attacker", "storePath": "C:/outside/knowledge.json"},
        )

        assert anonymous.json() == {"code": 0, "msg": "登录状态已失效", "data": None}
        assert status.json()["code"] == 1
        assert "storePath" not in status.json()["data"]
        assert synced.json()["code"] == 1
        assert service.users == ["42", "42"]
    finally:
        app.dependency_overrides.clear()
