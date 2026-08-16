"""当前项目主动读取 DSH 插件本地 v2 知识库并幂等导入。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from itertools import chain
from pathlib import Path
import re
from typing import Any, Iterable

from app.auth.repository import connect_postgres
from app.core.result import BusinessError
from app.schemas.dsh_local_sync import DshLocalSyncItem, DshLocalSyncResult, DshLocalSyncStatus
from app.schemas.rag_control import RagIndexTextPublicRequest
from app.services.rag_control_service import RagControlService


CONTENT_FILE_PATTERN = re.compile(r"^[0-9a-f]{64}\.json$")
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_SYNC_DOCUMENTS = 5000


@dataclass(frozen=True)
class DshLocalDocument:
    """从插件本地库读取的一条受控资料。"""

    document_id: str
    title: str
    source: str
    content: str
    content_hash: str
    summary: str | None
    system_category: str | None
    user_category: str | None


@dataclass(frozen=True)
class DshLocalDocumentFailure:
    """一条无法读取但不会阻断其余资料的插件资料。"""

    document_id: str
    title: str
    message: str


@dataclass(frozen=True)
class DshLocalSyncRecord:
    """项目数据库中的一条同步映射。"""

    document_id: str
    content_hash: str
    material_id: int
    material_current: bool
    updated_at: datetime | None


class DshLocalLibraryReader:
    """只读解析插件 v2 manifest、JSONL sidecar 和单条原文文件。"""

    def __init__(self, store_path: str | Path | None = None) -> None:
        configured = str(store_path or os.getenv("DSH_KNOWLEDGE_STORE_PATH") or default_dsh_store_path()).strip()
        self.store_path = Path(os.path.abspath(Path(configured).expanduser()))
        self.index_path = Path(f"{self.store_path}.index.jsonl")
        self.documents_path = Path(f"{self.store_path}.documents")

    def overview(self) -> tuple[bool, int, str]:
        """读取常数大小 manifest；不可读时返回稳定中文原因。"""
        try:
            manifest = self._manifest()
            return True, int(manifest["documentCount"]), "DSH 插件本地知识库可读取"
        except Exception as exc:  # noqa: BLE001 - 状态接口要稳定返回不可读原因。
            return False, 0, public_reader_error(exc)

    def documents(self) -> Iterable[DshLocalDocument | DshLocalDocumentFailure]:
        """先校验轻量索引完整性，再逐条读取原文并隔离单条资料错误。"""
        records = self._index_records()
        for record in records:
            try:
                yield self._document(record)
            except BusinessError as exc:
                yield DshLocalDocumentFailure(
                    document_id=clean_text(record.get("id"), 160) or "unknown",
                    title=clean_text(record.get("title"), 255) or "无法读取的 DSH 资料",
                    message=str(exc),
                )

    def _index_records(self) -> list[dict[str, Any]]:
        """在写入项目之前完整校验 JSONL 结构和 manifest 计数。"""
        manifest = self._manifest()
        self._reject_symlink(self.index_path, "DSH 本地知识库索引不允许使用符号链接")
        self._reject_symlink(self.documents_path, "DSH 本地知识库资料目录不允许使用符号链接")
        if not self.index_path.is_file() or not self.documents_path.is_dir():
            raise BusinessError("DSH 本地知识库索引或资料目录不存在")
        records: list[dict[str, Any]] = []
        try:
            with self.index_path.open("r", encoding="utf-8-sig") as handle:
                for line_number, line in enumerate(handle, start=1):
                    if not line.strip():
                        continue
                    if len(records) >= MAX_SYNC_DOCUMENTS:
                        raise BusinessError(f"单次同步最多读取 {MAX_SYNC_DOCUMENTS} 份资料")
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise BusinessError(f"DSH 本地知识库索引第 {line_number} 行损坏") from exc
                    if not isinstance(record, dict):
                        raise BusinessError(f"DSH 本地知识库索引第 {line_number} 行不是资料对象")
                    records.append(record)
        except BusinessError:
            raise
        except OSError as exc:
            raise BusinessError("DSH 本地知识库索引无法读取") from exc
        if len(records) != int(manifest["documentCount"]):
            raise BusinessError("DSH 本地知识库清单与索引数量不一致，请先在 DSH 中重新打开知识库完成恢复")
        return records

    def _manifest(self) -> dict[str, Any]:
        self._reject_symlink(self.store_path, "DSH 本地知识库清单不允许使用符号链接")
        if not self.store_path.is_file():
            raise BusinessError("尚未找到 DSH 插件本地知识库")
        try:
            manifest = json.loads(self.store_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BusinessError("DSH 本地知识库清单无法读取") from exc
        if not isinstance(manifest, dict):
            raise BusinessError("DSH 本地知识库不是受支持的 v2 格式")
        document_count = manifest.get("documentCount")
        if (
            manifest.get("version") != 2
            or not isinstance(document_count, int)
            or isinstance(document_count, bool)
            or document_count < 0
            or document_count > MAX_SYNC_DOCUMENTS
        ):
            raise BusinessError("DSH 本地知识库不是受支持的 v2 格式")
        return manifest

    def _document(self, record: dict[str, Any]) -> DshLocalDocument:
        document_id = clean_text(record.get("id"), 160)
        content_file = clean_text(record.get("contentFile"), 80)
        if not document_id or not CONTENT_FILE_PATTERN.fullmatch(content_file):
            raise BusinessError("DSH 本地知识库索引包含非法资料标识")
        expected_file = f"{hashlib.sha256(document_id.encode('utf-8')).hexdigest()}.json"
        if content_file != expected_file:
            raise BusinessError("DSH 本地知识库原文文件名校验失败")
        target = self.documents_path / content_file
        if target.parent != self.documents_path:
            raise BusinessError("DSH 本地知识库原文路径越界")
        self._reject_symlink(target, "DSH 本地资料原文不允许使用符号链接")
        try:
            if target.stat().st_size > MAX_DOCUMENT_BYTES:
                raise BusinessError("单份 DSH 资料超过项目同步大小限制 10MB")
            payload = json.loads(target.read_text(encoding="utf-8-sig"))
        except BusinessError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise BusinessError("DSH 本地资料原文无法读取") from exc
        if not isinstance(payload, dict):
            raise BusinessError("DSH 本地资料原文不是受支持的资料对象")
        if clean_text(payload.get("id"), 160) != document_id:
            raise BusinessError("DSH 本地资料 ID 校验失败")
        title = clean_text(payload.get("title"), 255)
        content = str(payload.get("content") or "").strip()
        if not title or not content:
            raise BusinessError("DSH 本地资料标题或正文为空")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return DshLocalDocument(
            document_id=document_id,
            title=title,
            source=clean_text(payload.get("source"), 200) or "DSH 本地知识库",
            content=content,
            content_hash=digest,
            summary=optional_text(payload.get("summary"), 5000),
            system_category=optional_text(payload.get("systemCategory"), 80),
            user_category=optional_text(payload.get("userCategory"), 80),
        )

    @staticmethod
    def _reject_symlink(path: Path, message: str) -> None:
        """拒绝插件 store 中的符号链接，防止越过服务端固定路径边界。"""
        try:
            if path.is_symlink():
                raise BusinessError(message)
        except OSError as exc:
            raise BusinessError("DSH 本地知识库路径无法检查") from exc


class DshLocalSyncRepository:
    """保存项目用户、插件 documentId 与项目 materialId 的幂等映射。"""

    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = database_url or os.getenv("RAG_DATABASE_URL") or os.getenv("DATABASE_URL", "")
        if not self.database_url:
            raise RuntimeError("未配置 RAG_DATABASE_URL 或 DATABASE_URL")

    def claim_owner(self, user_id: str) -> None:
        """原子锁定唯一项目账号，防止共享部署复制同一 OS 用户的 DSH 资料。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO learning_evidence.dsh_local_sync_owner (singleton_key, project_user_id)
                    VALUES (1, %s)
                    ON CONFLICT (singleton_key) DO NOTHING
                    """,
                    (user_id,),
                )
                cursor.execute(
                    """
                    SELECT project_user_id
                    FROM learning_evidence.dsh_local_sync_owner
                    WHERE singleton_key = 1
                    FOR SHARE
                    """
                )
                row = cursor.fetchone() or {}
                if str(row.get("project_user_id") or "") != user_id:
                    raise BusinessError("DSH 本地同步已绑定到另一个项目账号")

    def owner_matches(self, user_id: str) -> bool:
        """查询当前项目账号是否已拥有本地同步适配器。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT project_user_id
                    FROM learning_evidence.dsh_local_sync_owner
                    WHERE singleton_key = 1
                    """
                )
                row = cursor.fetchone()
        return row is None or str(row.get("project_user_id") or "") == user_id

    def list_records(self, user_id: str) -> dict[str, DshLocalSyncRecord]:
        """按认证用户读取全部同步映射。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT sync.dsh_document_id,
                           sync.content_hash,
                           sync.project_material_id,
                           sync.updated_at,
                           (
                               material.id IS NOT NULL
                               AND material.storage_type = 'manual'
                               AND material.source = 'dsh-local:' || sync.dsh_document_id
                           ) AS material_current
                    FROM learning_evidence.dsh_local_material_sync sync
                    LEFT JOIN learning_evidence.learning_material material
                      ON material.id = sync.project_material_id
                     AND material.user_id = sync.project_user_id
                    WHERE sync.project_user_id = %s
                    """,
                    (user_id,),
                )
                rows = cursor.fetchall()
        return {
            str(row["dsh_document_id"]): DshLocalSyncRecord(
                document_id=str(row["dsh_document_id"]),
                content_hash=str(row["content_hash"]),
                material_id=int(row["project_material_id"]),
                material_current=bool(row.get("material_current")),
                updated_at=row.get("updated_at"),
            )
            for row in rows
        }

    def upsert(self, user_id: str, document: DshLocalDocument, material_id: int) -> None:
        """资料 durable job 创建后提交映射；失败可由下次同步按稳定 source 补偿。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO learning_evidence.dsh_local_material_sync (
                        project_user_id, dsh_document_id, content_hash, project_material_id,
                        plugin_summary, plugin_system_category, plugin_user_category
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (project_user_id, dsh_document_id) DO UPDATE
                    SET content_hash = EXCLUDED.content_hash,
                        project_material_id = EXCLUDED.project_material_id,
                        plugin_summary = EXCLUDED.plugin_summary,
                        plugin_system_category = EXCLUDED.plugin_system_category,
                        plugin_user_category = EXCLUDED.plugin_user_category,
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    (
                        user_id,
                        document.document_id,
                        document.content_hash,
                        material_id,
                        document.summary,
                        document.system_category,
                        document.user_category,
                    ),
                )

    def refresh_metadata(self, user_id: str, document: DshLocalDocument, material_id: int) -> None:
        """正文未变时只刷新插件审计元数据，不重复投递项目索引任务。"""
        self.upsert(user_id, document, material_id)

    def status(self, user_id: str) -> tuple[int, datetime | None]:
        """返回当前用户已同步映射数与最后时间。"""
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT COUNT(*) AS total, MAX(updated_at) AS last_synced_at
                    FROM learning_evidence.dsh_local_material_sync
                    WHERE project_user_id = %s
                    """,
                    (user_id,),
                )
                row = cursor.fetchone() or {}
        return int(row.get("total") or 0), row.get("last_synced_at")

    def _connect(self):
        from psycopg.rows import dict_row
        return connect_postgres(self.database_url, row_factory=dict_row, purpose="DSH 本地同步仓储")


class DshLocalSyncService:
    """协调本地库 reader、映射仓储与项目现有 RAG durable 索引链。"""

    def __init__(
        self,
        reader: DshLocalLibraryReader | None = None,
        repository: DshLocalSyncRepository | None = None,
        rag_service: RagControlService | None = None,
    ) -> None:
        self.reader = reader or DshLocalLibraryReader()
        self.repository = repository or DshLocalSyncRepository()
        self.rag_service = rag_service or RagControlService()

    def status(self, user_id: str) -> DshLocalSyncStatus:
        """读取插件库与当前用户项目映射统计。"""
        enabled = dsh_local_sync_enabled()
        owner_matches = self.repository.owner_matches(user_id)
        readable, document_count, message = self.reader.overview() if enabled and owner_matches else (False, 0, "")
        if not enabled:
            message = "DSH 本地同步已由服务端关闭"
        elif not owner_matches:
            message = "DSH 本地同步已绑定到另一个项目账号"
        synced_count, last_synced_at = self.repository.status(user_id)
        return DshLocalSyncStatus(
            configured=enabled,
            readable=readable,
            documentCount=document_count,
            syncedDocumentCount=synced_count,
            pendingDocumentCount=max(0, document_count - synced_count),
            lastSyncedAt=last_synced_at,
            message=message,
        )

    def sync(self, user_id: str) -> DshLocalSyncResult:
        """按内容哈希幂等同步全部插件资料，单条失败不阻断其他资料。"""
        if not dsh_local_sync_enabled():
            raise BusinessError("DSH 本地同步已由服务端关闭")
        source_documents = iter(self.reader.documents())
        try:
            first_document = next(source_documents)
        except StopIteration:
            first_document = None
        self.repository.claim_owner(user_id)
        records = self.repository.list_records(user_id)
        items: list[DshLocalSyncItem] = []
        created = updated = skipped = failed = 0
        documents = source_documents if first_document is None else chain((first_document,), source_documents)
        for document in documents:
            if isinstance(document, DshLocalDocumentFailure):
                failed += 1
                items.append(
                    DshLocalSyncItem(
                        documentId=document.document_id,
                        title=document.title,
                        action="FAILED",
                        status="FAILED",
                        message=document.message,
                    )
                )
                continue
            record = records.get(document.document_id)
            try:
                if (
                    record
                    and record.content_hash == document.content_hash
                    and record.material_current
                ):
                    self.repository.refresh_metadata(user_id, document, record.material_id)
                    skipped += 1
                    items.append(
                        DshLocalSyncItem(
                            documentId=document.document_id,
                            materialId=record.material_id,
                            title=document.title,
                            action="SKIPPED",
                            status="UNCHANGED",
                            message="正文未变化，已刷新插件审计元数据",
                        )
                    )
                    continue
                result = self.rag_service.sync_text_material(
                    RagIndexTextPublicRequest(
                        title=document.title,
                        documentType="markdown",
                        source=f"dsh-local:{document.document_id}",
                        content=document.content,
                    ),
                    user_id,
                )
                self.repository.upsert(user_id, document, result.id)
                action = "UPDATED" if record else "CREATED"
                if record:
                    updated += 1
                else:
                    created += 1
                items.append(
                    DshLocalSyncItem(
                        documentId=document.document_id,
                        materialId=result.id,
                        title=document.title,
                        action=action,
                        status=result.status,
                        message="已提交项目索引与复习卡片生成链",
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 单条失败必须继续其他资料且只返回安全消息。
                failed += 1
                items.append(
                    DshLocalSyncItem(
                        documentId=document.document_id,
                        materialId=record.material_id if record else None,
                        title=document.title,
                        action="FAILED",
                        status="FAILED",
                        message=safe_sync_error(exc),
                    )
                )
        return DshLocalSyncResult(
            scannedCount=len(items),
            createdCount=created,
            updatedCount=updated,
            skippedCount=skipped,
            failedCount=failed,
            items=items,
        )


def default_dsh_store_path() -> Path:
    """默认读取当前系统用户的公开插件本地资料库。"""
    dsh_home = Path(os.getenv("DSH_HOME") or Path.home() / ".dsh")
    return dsh_home / "project-knowledge-review" / "knowledge.json"


def dsh_local_sync_enabled() -> bool:
    """服务端可显式关闭本机个人适配器，浏览器不能改变此边界。"""
    return os.getenv("DSH_LOCAL_SYNC_ENABLED", "true").strip().lower() in {"1", "true", "yes", "y", "on"}


def clean_text(value: object, maximum: int) -> str:
    """压缩不可信元数据空白并按数据库字段上限截断。"""
    return re.sub(r"\s+", " ", str(value or "")).strip()[:maximum]


def optional_text(value: object, maximum: int) -> str | None:
    """保留可选 Markdown 换行并限制审计字段大小。"""
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:maximum] if text else None


def public_reader_error(exc: Exception) -> str:
    """只向前端返回受控 reader 错误，不泄露系统异常详情。"""
    return str(exc) if isinstance(exc, BusinessError) else "DSH 本地知识库当前不可读取"


def safe_sync_error(exc: Exception) -> str:
    """将单条同步异常收敛为有限、无正文的中文提示。"""
    return str(exc)[:300] if isinstance(exc, BusinessError) else f"同步失败（{type(exc).__name__}）"
