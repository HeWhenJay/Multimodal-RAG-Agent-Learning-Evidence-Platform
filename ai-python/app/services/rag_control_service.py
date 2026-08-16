"""Python 公开 RAG 资料、检索和查询历史控制服务。"""

from __future__ import annotations

import asyncio
import base64
from datetime import date, datetime, timedelta
import json
import logging
import mimetypes
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any
from uuid import uuid4

from app.core.result import BusinessError
from app.repositories.rag_control import (
    MaterialRecord,
    ProgressLogRecord,
    QueryHistoryRecord,
    RagControlRepository,
    RagControlRepositoryProtocol,
)
from app.repositories.rag_task import RagQueryTaskRepositoryProtocol, build_query_task_repository
from app.schemas.rag import Evidence, IndexTextRequest, ProgressEvent, QueryRequest, QueryResponse, QueryTaskResponse
from app.schemas.rag_control import (
    MaterialPreviewResponse,
    MaterialUploadChunkResponse,
    RagIndexRemoteVideoBatchPublicRequest,
    RagIndexRemoteVideoPublicRequest,
    RagIndexTextPublicRequest,
    DshPluginMaterialPageResponse,
    DshPluginMaterialResponse,
    RagMaterialResponse,
    RagOverviewPublicResponse,
    RagQueryHistoryResponse,
    RagQueryPublicRequest,
    RagRemoteVideoBatchItemResponse,
    RagRemoteVideoBatchResponse,
)
from app.storage.object_storage import (
    LocalRagObjectStorage,
    OssMultipartPart,
    OssRagObjectStorage,
    RagObjectStorage,
    StoredObject,
    build_rag_object_storage,
)
from app.services.video_parallel_indexing import parse_video_source_with_worker_pool
from app.services.remote_video_import import RemoteVideoUrl, extract_remote_video_urls, validate_remote_video_url
from app.services.material_processing_progress import build_material_processing_progress, public_progress_text
from rag.loaders.document_parsers import DocumentParserRouter
from rag.loaders.mineru_loader import MineruDocumentLoader
from rag.generation.bailian_llm import generate_grounded_answer_async
from rag.observability.progress import RagProgressReporter
from rag.retrievers.retrieval import create_rag_store


logger = logging.getLogger(__name__)

TEXT_PREVIEW_TYPES = {"markdown", "md", "txt", "text", "srt", "vtt"}
VIDEO_TYPES = {"mp4", "mov", "m4v", "webm", "mkv", "avi"}
VIDEO_SIDECAR_SUFFIXES = (".srt", ".vtt")
DOCUMENT_TYPES = {
    ".md": "markdown",
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".ppt": "ppt",
    ".doc": "doc",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".txt": "txt",
    ".srt": "srt",
    ".vtt": "vtt",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".webp": "webp",
    ".mp4": "mp4",
    ".mov": "mov",
    ".m4v": "m4v",
    ".webm": "webm",
    ".mkv": "mkv",
    ".avi": "avi",
}
BUSINESS_METADATA_FILTER_KEYS = {
    "documentId",
    "documentType",
    "source",
    "parser",
    "mediaType",
    "evidenceChannel",
    "blockType",
    "sectionName",
    "sectionKeyword",
    "pageIndex",
    "slideIndex",
}
_CHUNK_UPLOAD_LOCKS: dict[str, threading.Lock] = {}
_CHUNK_UPLOAD_COMPLETED_MATERIALS: dict[str, int] = {}
_CHUNK_UPLOAD_LOCKS_GUARD = threading.Lock()


class RagControlService:
    """承接 `/api/rag` 的业务状态、用户边界和 Python RAG 调用。"""

    def __init__(
        self,
        *,
        repository: RagControlRepositoryProtocol | None = None,
        store: Any | None = None,
        parser_router: DocumentParserRouter | None = None,
        object_storage: RagObjectStorage | None = None,
        task_repository: RagQueryTaskRepositoryProtocol | None = None,
        executor: object | None = None,
    ) -> None:
        self.repository = repository or RagControlRepository()
        self.store = store or create_rag_store()
        self.parser_router = parser_router or DocumentParserRouter(MineruDocumentLoader())
        self.object_storage = object_storage or build_rag_object_storage()
        self.task_repository = task_repository or build_query_task_repository()
        # 兼容已有测试或外部构造参数；耐久任务不再提交进程内 executor。
        self._legacy_executor = executor

    def overview(self, user_id: str) -> RagOverviewPublicResponse:
        """返回严格限定于当前用户的资料概览。"""
        with self.repository.transaction() as transaction:
            material_count, chunk_count, last_title = transaction.overview(user_id)
        return RagOverviewPublicResponse(
            materialCount=material_count,
            chunkCount=chunk_count,
            evidenceCount=chunk_count,
            lastIndexedTitle=last_title,
        )

    def list_materials(self, user_id: str) -> list[RagMaterialResponse]:
        """读取当前用户最近二十条资料。"""
        with self.repository.transaction() as transaction:
            records = transaction.list_materials(user_id, 20)
            return [self._material_response(transaction, record) for record in records]

    def list_dsh_materials(
        self,
        user_id: str,
        *,
        cursor: str | None = None,
        limit: int = 30,
        query: str | None = None,
    ) -> DshPluginMaterialPageResponse:
        """为 DSH 插件提供服务端游标分页的最小资料列表。"""
        safe_limit = clamp(limit, 1, 100, 30)
        cursor_updated_at, cursor_id = decode_material_cursor(cursor)
        with self.repository.transaction() as transaction:
            try:
                records, total = transaction.list_materials_page(
                    user_id,
                    limit=safe_limit + 1,
                    query=query.strip() if query and query.strip() else None,
                    cursor_updated_at=cursor_updated_at,
                    cursor_id=cursor_id,
                )
            except AttributeError:
                # 兼容尚未升级分页协议的测试替身，仍限制返回数量，避免一次加载全部资料。
                records = transaction.list_materials(user_id, safe_limit + 1)
                total = len(records)
        has_more = len(records) > safe_limit
        visible = records[:safe_limit]
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            if last.updated_at is not None:
                next_cursor = encode_material_cursor(last.updated_at, last.id)
        return DshPluginMaterialPageResponse(
            items=[
                DshPluginMaterialResponse(
                    id=record.id,
                    title=record.title,
                    documentType=record.document_type,
                    source=record.source,
                    status=record.status,
                    parser=record.parser,
                    chunkCount=record.chunk_count,
                    createdAt=record.created_at,
                    updatedAt=record.updated_at,
                )
                for record in visible
            ],
            total=total,
            hasMore=has_more,
            nextCursor=next_cursor,
        )

    def get_material(self, material_id: int, user_id: str) -> RagMaterialResponse:
        """读取当前用户拥有的一条资料。"""
        with self.repository.transaction() as transaction:
            material = self._require_material(transaction.find_material(material_id, user_id))
            return self._material_response(transaction, material)

    def list_evidences(self, material_id: int, user_id: str, limit: int) -> list[Evidence]:
        """验证资料所有权后从 canonical pgvector 索引读取 evidence。"""
        safe_limit = clamp(limit, 1, 100, 20)
        with self.repository.transaction() as transaction:
            material = self._require_material(transaction.find_material(material_id, user_id))
        return self.store.list_evidences(f"material-{material.id}", safe_limit)

    def preview_material(self, material_id: int, source: str | None, user_id: str) -> MaterialPreviewResponse:
        """优先预览原始文本；其他格式回退为当前资料的受控 RAG 提取视图。"""
        with self.repository.transaction() as transaction:
            material = self._require_material(transaction.find_material(material_id, user_id))
        validate_preview_source(material, source)
        if material.document_type.lower() in TEXT_PREVIEW_TYPES:
            try:
                content = self.object_storage.load_bytes(material)
            except BusinessError:
                # 手工文本和已清理原文件仍可通过已入库 RAG chunks 定位。
                logger.info("原始文本不可读取，回退 RAG 提取视图，materialId=%s", material.id)
            else:
                text = content.decode("utf-8-sig", errors="replace")
                return MaterialPreviewResponse(
                    materialId=material.id,
                    title=material.original_filename or material.title,
                    documentType=material.document_type,
                    source=source or material.public_url or material.original_file_path,
                    contentType=preview_content_type(material.document_type),
                    content=text,
                )
        return self._indexed_preview(material)

    def _indexed_preview(self, material: MaterialRecord) -> MaterialPreviewResponse:
        """从当前用户资料的 RAG chunks 生成带章节定位的安全提取视图。"""
        try:
            evidences = self.store.list_evidences(f"material-{material.id}", 1000)
        except Exception as exc:
            logger.exception("读取资料 RAG 提取视图失败，materialId=%s", material.id)
            raise BusinessError("读取资料原文失败") from exc
        if not evidences:
            raise BusinessError("当前资料没有可预览的 RAG 原文")
        return MaterialPreviewResponse(
            materialId=material.id,
            title=material.original_filename or material.title,
            documentType=material.document_type,
            source=None,
            contentType="text/markdown; charset=UTF-8",
            content=build_indexed_preview_content(material.title, evidences),
        )

    def index_text(self, request: RagIndexTextPublicRequest, user_id: str) -> RagMaterialResponse:
        """创建手工文本资料并同事务投递可恢复的 Python 索引任务。"""
        with self.repository.transaction() as transaction:
            material = transaction.insert_material(
                title=request.title,
                user_id=user_id,
                document_type=normalized_document_type(request.documentType, "markdown"),
                source=non_blank(request.source, "manual"),
                status="PENDING",
                original_filename=None,
                original_file_path=None,
                storage_type="manual",
                object_key=None,
                public_url=None,
            )
            schedule = transaction.enqueue_index_job(
                material=material,
                operation="INDEX_TEXT",
                status="PENDING",
                high_precision=False,
                source_ref={"type": "INLINE_TEXT", "parser": "python-manual-text"},
                text=request.content,
            )
            material = schedule.material
        return self._material_response_for_user(material, user_id)

    def import_remote_video(
        self,
        request: RagIndexRemoteVideoPublicRequest,
        user_id: str,
    ) -> RagMaterialResponse:
        """创建 Bilibili 多模态或抖音语音转写耐久索引任务。"""
        if not request.confirmedAuthorized:
            raise BusinessError("请先确认你有权处理该视频内容")
        remote = validate_remote_video_url(request.url)
        material, _queued = self._enqueue_remote_video(remote, request.highPrecision, user_id)
        return material

    def import_remote_videos(
        self,
        request: RagIndexRemoteVideoBatchPublicRequest,
        user_id: str,
    ) -> RagRemoteVideoBatchResponse:
        """从批量分享原文提取 URL，并将合法且不重复的资料逐条写入耐久队列。"""
        if not request.confirmedAuthorized:
            raise BusinessError("请先确认你有权处理该视频内容")
        candidates = extract_remote_video_urls(request.text)
        if not candidates:
            raise BusinessError("未识别到 HTTP(S) 视频链接")

        items: list[RagRemoteVideoBatchItemResponse] = []
        seen_urls: set[str] = set()
        for candidate in candidates:
            try:
                remote = validate_remote_video_url(candidate.value)
            except BusinessError as exc:
                items.append(
                    RagRemoteVideoBatchItemResponse(
                        lineNumber=candidate.line_number,
                        url=candidate.value,
                        status="REJECTED",
                        message=exc.message,
                    )
                )
                continue
            if remote.canonical_url in seen_urls:
                items.append(
                    RagRemoteVideoBatchItemResponse(
                        lineNumber=candidate.line_number,
                        url=candidate.value,
                        canonicalUrl=remote.canonical_url,
                        status="DUPLICATE",
                        message="批次内链接重复，已跳过",
                    )
                )
                continue
            seen_urls.add(remote.canonical_url)
            try:
                material, queued = self._enqueue_remote_video(remote, request.highPrecision, user_id)
            except BusinessError as exc:
                items.append(
                    RagRemoteVideoBatchItemResponse(
                        lineNumber=candidate.line_number,
                        url=candidate.value,
                        canonicalUrl=remote.canonical_url,
                        status="REJECTED",
                        message=exc.message,
                    )
                )
                continue
            items.append(
                RagRemoteVideoBatchItemResponse(
                    lineNumber=candidate.line_number,
                    url=candidate.value,
                    canonicalUrl=remote.canonical_url,
                    status="QUEUED" if queued else "REUSED",
                    message="已加入 RAG 处理队列" if queued else "资料已存在，已复用当前状态",
                    material=material,
                )
            )

        return RagRemoteVideoBatchResponse(
            candidateCount=len(candidates),
            queuedCount=sum(item.status == "QUEUED" for item in items),
            reusedCount=sum(item.status == "REUSED" for item in items),
            duplicateCount=sum(item.status == "DUPLICATE" for item in items),
            rejectedCount=sum(item.status == "REJECTED" for item in items),
            items=items,
        )

    def _enqueue_remote_video(
        self,
        remote: RemoteVideoUrl,
        high_precision: bool,
        user_id: str,
    ) -> tuple[RagMaterialResponse, bool]:
        """幂等创建单条远程资料任务，返回资料及本次是否真正投递新任务。"""
        source_filename = f"{remote.video_id}.srt" if remote.platform == "douyin" else f"{remote.video_id}.mp4"
        with self.repository.transaction() as transaction:
            existing = transaction.find_material_by_public_url(remote.canonical_url, user_id, remote.platform)
            if existing is not None and existing.status != "FAILED":
                return self._material_response(transaction, existing), False
            material = existing or transaction.insert_material(
                title=remote.placeholder_title,
                user_id=user_id,
                document_type="mp4",
                source=remote.platform,
                status="PENDING",
                original_filename=source_filename,
                original_file_path=None,
                storage_type="remote",
                object_key=None,
                public_url=remote.canonical_url,
            )
            schedule = transaction.enqueue_index_job(
                material=material,
                operation="INDEX_REMOTE_VIDEO",
                status="PENDING",
                high_precision=high_precision,
                source_ref={
                    "type": "REMOTE_VIDEO",
                    "platform": remote.platform,
                    "url": remote.canonical_url,
                    "videoId": remote.video_id,
                },
                text=None,
            )
            material = schedule.material
            return self._material_response(transaction, material), True

    def upload_material(
        self,
        *,
        filename: str | None,
        content: bytes | None = None,
        source_path: str | Path | None = None,
        content_type: str | None,
        high_precision: bool,
        user_id: str,
    ) -> RagMaterialResponse:
        """保存单文件、创建资料记录并投递 Python 耐久索引任务。

        公开 HTTP 上传使用 `source_path`，文件从请求临时目录流式复制到对象存储；
        `content` 仅保留给旧的内部小文件调用，避免兼容性改动迫使调用方一次性读大文件。
        """
        safe_filename = non_blank(filename, "未命名资料")
        if source_path is None:
            validate_upload_content(content)
        else:
            validate_upload_file(source_path)
        document_type = detect_document_type(safe_filename)
        if source_path is not None:
            stored = self.object_storage.store_file(
                source_path,
                safe_filename,
                user_id,
                document_type,
                content_type,
            )
        else:
            stored = self.object_storage.store_bytes(content or b"", safe_filename, user_id, document_type)
        return self._create_material_from_stored(
            stored=stored,
            source_path=source_path,
            filename=safe_filename,
            document_type=document_type,
            content_type=content_type,
            high_precision=high_precision,
            user_id=user_id,
        )

    def _create_material_from_stored(
        self,
        *,
        stored: StoredObject,
        source_path: str | Path | None,
        filename: str,
        document_type: str,
        content_type: str | None,
        high_precision: bool,
        user_id: str,
    ) -> RagMaterialResponse:
        """为已写入 local 或 OSS 的原始对象建档并投递耐久索引任务。"""
        copy_video_sidecars_to_storage(
            stored=stored,
            object_storage=self.object_storage,
            source_path=source_path,
            filename=filename,
            document_type=document_type,
            user_id=user_id,
        )
        try:
            with self.repository.transaction() as transaction:
                material = transaction.insert_material(
                    title=filename,
                    user_id=user_id,
                    document_type=document_type,
                    source="upload",
                    status="PARSING",
                    original_filename=filename,
                    original_file_path=stored.source_path,
                    storage_type=stored.storage_type,
                    object_key=stored.object_key,
                    public_url=stored.public_url,
                )
                schedule = transaction.enqueue_index_job(
                    material=material,
                    operation="INDEX_UPLOAD",
                    status="PENDING",
                    high_precision=high_precision,
                    source_ref={
                        "type": "STORAGE",
                        "filename": filename,
                        "contentType": content_type,
                        "storageType": stored.storage_type,
                        "sourcePath": stored.source_path,
                        "objectKey": stored.object_key,
                        "publicUrl": stored.public_url,
                    },
                    text=None,
                )
                material = schedule.material
        except Exception:
            self._delete_stored_object(stored)
            raise
        return self._material_response_for_user(material, user_id)

    def upload_chunk(
        self,
        *,
        content: bytes | None = None,
        source_path: str | Path | None = None,
        filename: str,
        upload_id: str | None,
        chunk_index: int,
        total_chunks: int,
        total_size: int | None,
        content_type: str | None,
        high_precision: bool,
        user_id: str,
    ) -> MaterialUploadChunkResponse:
        """按 0-based 序号保存分片，收齐后同步合并并创建受控资料。"""
        if source_path is None:
            validate_chunk_request(content, filename, chunk_index, total_chunks, total_size)
        else:
            validate_upload_file(source_path)
            validate_chunk_request(
                None,
                filename,
                chunk_index,
                total_chunks,
                total_size,
                chunk_size=Path(source_path).stat().st_size,
            )
        safe_upload_id = sanitize_upload_id(upload_id) or uuid4().hex
        if isinstance(self.object_storage, OssRagObjectStorage):
            return self._upload_chunk_to_oss(
                content=content,
                source_path=source_path,
                filename=filename,
                upload_id=safe_upload_id,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                total_size=total_size,
                content_type=content_type,
                high_precision=high_precision,
                user_id=user_id,
            )
        directory = self._chunk_directory(user_id, safe_upload_id)
        directory.mkdir(parents=True, exist_ok=True)
        chunk_path = directory / chunk_filename(chunk_index)
        temp_path = directory / f"{chunk_filename(chunk_index)}.tmp"
        if source_path is not None:
            _copy_file(Path(source_path), temp_path)
        else:
            temp_path.write_bytes(content or b"")
        temp_path.replace(chunk_path)

        return self._finish_chunk_upload(
            directory=directory,
            filename=filename,
            upload_id=safe_upload_id,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            total_size=total_size,
            content_type=content_type,
            high_precision=high_precision,
            user_id=user_id,
        )

    def upload_chunk_file(
        self,
        *,
        source_path: str | Path,
        filename: str,
        upload_id: str | None,
        chunk_index: int,
        total_chunks: int,
        total_size: int | None,
        content_type: str | None,
        high_precision: bool,
        user_id: str,
    ) -> MaterialUploadChunkResponse:
        """流式保存 multipart 分片，避免 `UploadFile.read()` 返回整片 bytes。"""
        validate_upload_file(source_path)
        validate_chunk_request(
            None,
            filename,
            chunk_index,
            total_chunks,
            total_size,
            chunk_size=Path(source_path).stat().st_size,
        )
        safe_upload_id = sanitize_upload_id(upload_id) or uuid4().hex
        if isinstance(self.object_storage, OssRagObjectStorage):
            return self._upload_chunk_to_oss(
                content=None,
                source_path=source_path,
                filename=filename,
                upload_id=safe_upload_id,
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                total_size=total_size,
                content_type=content_type,
                high_precision=high_precision,
                user_id=user_id,
            )
        directory = self._chunk_directory(user_id, safe_upload_id)
        directory.mkdir(parents=True, exist_ok=True)
        chunk_path = directory / chunk_filename(chunk_index)
        temp_path = directory / f"{chunk_filename(chunk_index)}.tmp"
        _copy_file(Path(source_path), temp_path)
        temp_path.replace(chunk_path)

        return self._finish_chunk_upload(
            directory=directory,
            filename=filename,
            upload_id=safe_upload_id,
            chunk_index=chunk_index,
            total_chunks=total_chunks,
            total_size=total_size,
            content_type=content_type,
            high_precision=high_precision,
            user_id=user_id,
        )

    def _upload_chunk_to_oss(
        self,
        *,
        content: bytes | None,
        source_path: str | Path | None,
        filename: str,
        upload_id: str,
        chunk_index: int,
        total_chunks: int,
        total_size: int | None,
        content_type: str | None,
        high_precision: bool,
        user_id: str,
    ) -> MaterialUploadChunkResponse:
        """把浏览器分片直接上传 OSS multipart，避免本机合并完整长视频。"""
        storage = self.object_storage
        if not isinstance(storage, OssRagObjectStorage):
            raise BusinessError("当前对象存储不支持 OSS 分片上传")

        safe_name = safe_filename(filename)
        directory = self._chunk_directory(user_id, upload_id)
        directory.mkdir(parents=True, exist_ok=True)
        upload_key = chunk_upload_completion_key(
            directory=directory,
            user_id=user_id,
            upload_id=upload_id,
            filename=safe_name,
            total_chunks=total_chunks,
            total_size=total_size,
        )
        with chunk_upload_lock(upload_key):
            completed = self._completed_chunk_material(upload_key, user_id)
            if completed is not None:
                return completed_chunk_response(
                    upload_id=upload_id,
                    filename=safe_name,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    material=self._material_response_for_user(completed, user_id),
                )

            marker = directory / "material.id"
            existing = self._chunk_marker_material(marker, user_id)
            if existing is not None:
                remember_completed_chunk_material(upload_key, existing.id)
                return completed_chunk_response(
                    upload_id=upload_id,
                    filename=safe_name,
                    chunk_index=chunk_index,
                    total_chunks=total_chunks,
                    material=self._material_response_for_user(existing, user_id),
                )

            state_path = oss_multipart_state_path(directory)
            state = load_oss_multipart_state(state_path)
            if state is None:
                session = storage.start_multipart_upload(
                    filename=safe_name,
                    user_id=user_id,
                    document_type=detect_document_type(safe_name),
                    content_type=content_type,
                )
                state = {
                    "version": 1,
                    "objectKey": session.object_key,
                    "ossUploadId": session.upload_id,
                    "filename": safe_name,
                    "totalChunks": total_chunks,
                    "totalSize": total_size,
                    "contentType": content_type,
                    "parts": {},
                }
                save_oss_multipart_state(state_path, state)
            validate_oss_multipart_state(
                state,
                filename=safe_name,
                total_chunks=total_chunks,
                total_size=total_size,
            )

            stored_material_id = safe_int_or_none(state.get("materialId"))
            if stored_material_id is not None:
                with self.repository.transaction() as transaction:
                    stored_material = transaction.find_material(stored_material_id, user_id)
                if stored_material is not None:
                    remember_completed_chunk_material(upload_key, stored_material.id)
                    marker.write_text(str(stored_material.id), encoding="utf-8")
                    return completed_chunk_response(
                        upload_id=upload_id,
                        filename=safe_name,
                        chunk_index=chunk_index,
                        total_chunks=total_chunks,
                        material=self._material_response_for_user(stored_material, user_id),
                    )

            parts = oss_multipart_parts(state, total_chunks)
            if chunk_index not in parts:
                object_key = str(state["objectKey"])
                oss_upload_id = str(state["ossUploadId"])
                if source_path is not None:
                    uploaded_part = storage.upload_multipart_part_file(
                        object_key=object_key,
                        upload_id=oss_upload_id,
                        part_number=chunk_index + 1,
                        source=source_path,
                    )
                else:
                    uploaded_part = storage.upload_multipart_part_bytes(
                        object_key=object_key,
                        upload_id=oss_upload_id,
                        part_number=chunk_index + 1,
                        content=content or b"",
                    )
                raw_parts = state.setdefault("parts", {})
                if not isinstance(raw_parts, dict):
                    raise BusinessError("OSS 分片上传会话状态不合法")
                raw_parts[str(chunk_index)] = {
                    "etag": uploaded_part.etag,
                    "size": uploaded_part.size,
                }
                save_oss_multipart_state(state_path, state)
                parts = oss_multipart_parts(state, total_chunks)

            received_chunks = len(parts)
            next_chunk_index = next_missing_oss_chunk_index(parts, total_chunks)
            if received_chunks < total_chunks:
                return MaterialUploadChunkResponse(
                    uploadId=upload_id,
                    filename=safe_name,
                    chunkIndex=chunk_index,
                    totalChunks=total_chunks,
                    receivedChunks=received_chunks,
                    nextChunkIndex=next_chunk_index,
                    status="UPLOADING",
                    message=f"已直传 OSS 视频分片：{received_chunks}/{total_chunks}，下次从第 {next_chunk_index + 1} 片继续",
                    completed=False,
                )

            actual_size = sum(part.size for part in parts.values())
            if total_size is not None and actual_size != total_size:
                raise BusinessError("OSS 已上传分片大小与前端声明不一致")
            stored = storage.complete_multipart_upload(
                object_key=str(state["objectKey"]),
                upload_id=str(state["ossUploadId"]),
                parts=[parts[index] for index in sorted(parts)],
            )
            material = self._create_material_from_stored(
                stored=stored,
                source_path=None,
                filename=safe_name,
                document_type=detect_document_type(safe_name),
                content_type=content_type,
                high_precision=high_precision,
                user_id=user_id,
            )
            state["materialId"] = material.id
            save_oss_multipart_state(state_path, state)
            marker.write_text(str(material.id), encoding="utf-8")
            remember_completed_chunk_material(upload_key, material.id)
            return MaterialUploadChunkResponse(
                uploadId=upload_id,
                filename=safe_name,
                chunkIndex=chunk_index,
                totalChunks=total_chunks,
                receivedChunks=total_chunks,
                nextChunkIndex=total_chunks,
                status="PROCESSING",
                message="视频分片已直传 OSS 并完成合并，已触发索引",
                completed=True,
                material=material,
            )

    def _finish_chunk_upload(
        self,
        *,
        directory: Path,
        filename: str,
        upload_id: str,
        chunk_index: int,
        total_chunks: int,
        total_size: int | None,
        content_type: str | None,
        high_precision: bool,
        user_id: str,
    ) -> MaterialUploadChunkResponse:
        """检查分片收齐状态，并把合并文件按路径交给对象存储。"""
        upload_key = chunk_upload_completion_key(
            directory=directory,
            user_id=user_id,
            upload_id=upload_id,
            filename=filename,
            total_chunks=total_chunks,
            total_size=total_size,
        )
        completed = self._completed_chunk_material(upload_key, user_id)
        if completed is not None:
            return MaterialUploadChunkResponse(
                uploadId=upload_id,
                filename=filename,
                chunkIndex=chunk_index,
                totalChunks=total_chunks,
                receivedChunks=total_chunks,
                nextChunkIndex=total_chunks,
                status="PROCESSING",
                message="视频分片已收齐，继续沿用已有资料记录",
                completed=True,
                material=self._material_response_for_user(completed, user_id),
            )

        received_chunks = count_received_chunks(directory)
        next_chunk_index = next_missing_chunk_index(directory, total_chunks)
        if received_chunks < total_chunks:
            return MaterialUploadChunkResponse(
                uploadId=upload_id,
                filename=filename,
                chunkIndex=chunk_index,
                totalChunks=total_chunks,
                receivedChunks=received_chunks,
                nextChunkIndex=next_chunk_index,
                status="UPLOADING",
                message=f"已接收视频分片：{received_chunks}/{total_chunks}，下次从第 {next_chunk_index + 1} 片继续",
                completed=False,
            )

        lock = chunk_upload_lock(upload_key)
        with lock:
            completed = self._completed_chunk_material(upload_key, user_id)
            if completed is not None:
                return MaterialUploadChunkResponse(
                    uploadId=upload_id,
                    filename=filename,
                    chunkIndex=chunk_index,
                    totalChunks=total_chunks,
                    receivedChunks=total_chunks,
                    nextChunkIndex=total_chunks,
                    status="PROCESSING",
                    message="视频分片已收齐，继续沿用已有资料记录",
                    completed=True,
                    material=self._material_response_for_user(completed, user_id),
                )

            marker = directory / "material.id"
            existing = self._chunk_marker_material(marker, user_id)
            if existing is not None:
                remember_completed_chunk_material(upload_key, existing.id)
                return MaterialUploadChunkResponse(
                    uploadId=upload_id,
                    filename=filename,
                    chunkIndex=chunk_index,
                    totalChunks=total_chunks,
                    receivedChunks=total_chunks,
                    nextChunkIndex=total_chunks,
                    status="PROCESSING",
                    message="视频分片已收齐，继续沿用已有资料记录",
                    completed=True,
                    material=self._material_response_for_user(existing, user_id),
                )

            received_chunks = count_received_chunks(directory)
            if received_chunks < total_chunks:
                next_chunk_index = next_missing_chunk_index(directory, total_chunks)
                return MaterialUploadChunkResponse(
                    uploadId=upload_id,
                    filename=filename,
                    chunkIndex=chunk_index,
                    totalChunks=total_chunks,
                    receivedChunks=received_chunks,
                    nextChunkIndex=next_chunk_index,
                    status="UPLOADING",
                    message=f"已接收视频分片：{received_chunks}/{total_chunks}，下次从第 {next_chunk_index + 1} 片继续",
                    completed=False,
                )

            merged = merge_chunks(directory, filename, total_chunks, total_size)
            try:
                material = self.upload_material(
                    filename=filename,
                    source_path=merged,
                    content_type=content_type,
                    high_precision=high_precision,
                    user_id=user_id,
                )
                marker.write_text(str(material.id), encoding="utf-8")
                remember_completed_chunk_material(upload_key, material.id)
                return MaterialUploadChunkResponse(
                    uploadId=upload_id,
                    filename=filename,
                    chunkIndex=chunk_index,
                    totalChunks=total_chunks,
                    receivedChunks=total_chunks,
                    nextChunkIndex=total_chunks,
                    status="PROCESSING",
                    message="视频分片已收齐，已完成合并并触发索引",
                    completed=True,
                    material=material,
                )
            finally:
                cleanup_chunk_directory(directory, self._chunk_root())

    def reindex_material(self, material_id: int, high_precision: bool, user_id: str) -> RagMaterialResponse:
        """校验受控原文件后投递新版本耐久重建任务。"""
        with self.repository.transaction() as transaction:
            material = self._require_material(transaction.find_material(material_id, user_id))
            if (material.storage_type or "").lower() == "manual":
                raise BusinessError("手动文本资料没有原始上传文件，请重新提交文本内容")
            if (material.storage_type or "").lower() == "remote":
                remote = validate_remote_video_url(material.public_url or "")
                if material.status in {"PENDING", "PARSING", "REINDEXING"}:
                    return self._material_response(transaction, material)
                locked_material = transaction.find_material_by_public_url(remote.canonical_url, user_id, remote.platform)
                if locked_material is None or locked_material.id != material.id:
                    raise BusinessError("远程视频资料状态已变化，请刷新后重试")
                if locked_material.status in {"PENDING", "PARSING", "REINDEXING"}:
                    return self._material_response(transaction, locked_material)
                schedule = transaction.enqueue_index_job(
                    material=material,
                    operation="INDEX_REMOTE_VIDEO",
                    status="REINDEXING",
                    high_precision=high_precision,
                    source_ref={
                        "type": "REMOTE_VIDEO",
                        "platform": remote.platform,
                        "url": remote.canonical_url,
                        "videoId": remote.video_id,
                    },
                    text=None,
                )
            else:
                # 只校验受控来源，不在 API 请求进程读取并解析完整文件；OSS 由 Kafka worker 下载临时文件。
                source_path = self.object_storage.local_path(material)
                if source_path is None and (material.storage_type or "").lower() != "oss":
                    raise BusinessError("当前资料没有可供 Python worker 读取的原始文件")
                source_ref_path = str(source_path) if source_path is not None else material.original_file_path
                schedule = transaction.enqueue_index_job(
                    material=material,
                    operation="REINDEX",
                    status="REINDEXING",
                    high_precision=high_precision,
                    source_ref={
                        "type": "STORAGE",
                        "filename": material.original_filename or material.title,
                        "contentType": mimetypes.guess_type(material.original_filename or material.title)[0],
                        "storageType": material.storage_type,
                        "sourcePath": source_ref_path,
                        "objectKey": material.object_key,
                        "publicUrl": material.public_url,
                    },
                    text=None,
                )
            material = schedule.material
        return self._material_response_for_user(material, user_id)

    def query(self, request: RagQueryPublicRequest, user_id: str) -> QueryResponse:
        """执行当前用户私有范围内的同步查询，并持久化结果快照。"""
        start = time.perf_counter()
        scoped = self._scoped_query_request(request, user_id)
        try:
            response = self._execute_query(scoped, user_id)
        except Exception as exc:
            self._save_failed_query_history(request, user_id, elapsed_ms(start))
            raise BusinessError("RAG 查询失败") from exc
        self._insert_query_history(request, user_id, response, elapsed_ms(start), task_id=None, status="COMPLETED")
        return response

    async def query_async(self, request: RagQueryPublicRequest, user_id: str) -> QueryResponse:
        """异步执行公开查询，让最终回答 HTTP 直接 await 共享 AsyncClient。"""
        start = time.perf_counter()
        scoped = self._scoped_query_request(request, user_id)
        try:
            response = await self._execute_query_async(scoped, user_id)
        except Exception as exc:
            await asyncio.to_thread(
                self._save_failed_query_history,
                request,
                user_id,
                elapsed_ms(start),
            )
            raise BusinessError("RAG 查询失败") from exc
        duration_ms = elapsed_ms(start)
        await asyncio.to_thread(
            lambda: self._insert_query_history(
                request,
                user_id,
                response,
                duration_ms,
                task_id=None,
                status="COMPLETED",
            )
        )
        return response

    def list_query_history(
        self,
        user_id: str,
        start_date: date | None,
        end_date: date | None,
        limit: int | None,
    ) -> list[RagQueryHistoryResponse]:
        """读取用户近七日的查询历史，日期和条数按 Java 规则收敛。"""
        today = datetime.now().date()
        earliest = today - timedelta(days=6)
        safe_start = clamp_date(start_date or earliest, earliest, today)
        safe_end = clamp_date(end_date or today, earliest, today)
        if safe_start > safe_end:
            safe_start = safe_end
        safe_limit = clamp(limit, 1, 50, 5)
        with self.repository.transaction() as transaction:
            records = transaction.list_query_history(
                user_id,
                start_at=datetime.combine(safe_start, datetime.min.time()),
                end_at=datetime.combine(safe_end + timedelta(days=1), datetime.min.time()),
                limit=safe_limit,
            )
        return [history_to_response(item) for item in records]

    def start_query_task(self, request: RagQueryPublicRequest, user_id: str) -> QueryTaskResponse:
        """创建有用户归属的查询耐久任务，由独立 worker 以租约抢占执行。"""
        task_id = uuid4().hex
        scoped = self._scoped_query_request(request, user_id)
        task = self.task_repository.enqueue(
            task_id=task_id,
            user_id=user_id,
            question=request.question,
            top_k=scoped.topK,
            request_payload=scoped.model_dump(mode="json"),
        )
        return QueryTaskResponse(
            taskId=task_id,
            status="RUNNING",
            message="正在执行 RAG 检索问答",
            progressEvents=[],
            result=None,
            errorMessage=None,
            createdAt=iso_or_none(task.created_at),
            updatedAt=iso_or_none(task.updated_at),
        )

    def get_query_task(self, task_id: str, user_id: str) -> QueryTaskResponse:
        """读取 worker 写回的持久化任务快照，并再次校验用户归属。"""
        if not task_id or not task_id.strip():
            raise BusinessError("查询任务 ID 不能为空")
        history = self.task_repository.get(task_id.strip(), user_id)
        if history is None:
            raise BusinessError("查询任务不存在")
        progress = parse_progress_events(history.progress_events_json)
        if history.status == "RUNNING":
            return QueryTaskResponse(
                taskId=task_id,
                status="RUNNING",
                message="正在执行 RAG 检索问答",
                progressEvents=progress,
                result=None,
                errorMessage=None,
                createdAt=iso_or_none(history.created_at),
                updatedAt=iso_or_none(history.updated_at),
            )
        if history.status == "COMPLETED":
            return QueryTaskResponse(
                taskId=task_id,
                status="COMPLETED",
                message="RAG 检索问答完成",
                progressEvents=progress,
                result=history_to_query_response(history),
                errorMessage=None,
                createdAt=iso_or_none(history.created_at),
                updatedAt=iso_or_none(history.updated_at),
            )
        if history.status == "EXPIRED":
            return QueryTaskResponse(
                taskId=task_id,
                status="EXPIRED",
                message="RAG 查询任务已过期",
                progressEvents=progress,
                result=None,
                errorMessage=history.error_message or "RAG 查询任务已过期",
                createdAt=iso_or_none(history.created_at),
                updatedAt=iso_or_none(history.updated_at),
            )
        return QueryTaskResponse(
            taskId=task_id,
            status="FAILED",
            message="RAG 检索问答失败",
            progressEvents=progress,
            result=None,
            errorMessage=history.error_message or "RAG 查询失败",
            createdAt=iso_or_none(history.created_at),
            updatedAt=iso_or_none(history.updated_at),
        )

    def _execute_query(self, request: QueryRequest, user_id: str) -> QueryResponse:
        """以数据库投递模式执行检索，避免向 Java 日志接口回调。"""
        progress = RagProgressReporter(
            document_id="query",
            user_id=user_id,
            persist=True,
            delivery_mode="database",
        )
        return self.store.query(request, progress_reporter=progress)

    async def _execute_query_async(self, request: QueryRequest, user_id: str) -> QueryResponse:
        """在线程中完成现有同步检索，再在事件循环直接等待最终回答 HTTP。"""
        progress = RagProgressReporter(
            document_id="query",
            user_id=user_id,
            persist=True,
            delivery_mode="database",
        )
        response = await asyncio.to_thread(
            lambda: self.store.query(
                request,
                progress_reporter=progress,
                defer_answer=True,
            )
        )
        if response.answerStatus == "REFUSED":
            return response

        generated = await generate_grounded_answer_async(request.question, response.evidences)
        diagnostics = dict(response.diagnostics)
        diagnostics.update(generated.diagnostics())
        answer_model = generated.diagnostics().get("answerModel") or os.getenv("RAG_LLM_MODEL") or "qwen-plus"
        await asyncio.to_thread(
            progress.emit,
            "query.answer",
            "RAG 检索问答完成",
            status="COMPLETED",
            current_step=8,
            total_steps=8,
            percent=100,
            detail=f"回答模型：{answer_model}；引用 evidence 数：{len(response.evidences)}；ioMode=async-http",
        )
        return response.model_copy(
            update={
                "answer": generated.answer,
                "diagnostics": diagnostics,
                "progressEvents": list(progress.events),
            }
        )

    def _index_text_material(self, material: MaterialRecord, content: str):
        """保留 Markdown 标题和段落结构，走现有递归切块入口。"""
        progress = self._progress_reporter(material)
        parsed = self.parser_router.parse_text(
            document_id=f"material-{material.id}",
            title=material.title,
            document_type=material.document_type,
            source_path=None,
            content=content,
            parser="python-manual-text",
            progress_reporter=progress,
        )
        return self.store.index_blocks(
            document_id=f"material-{material.id}",
            title=material.title,
            document_type=material.document_type,
            source=material.source or "manual",
            user_id=material.user_id,
            visibility_scope="private",
            language="zh-CN",
            parser=parsed.parser,
            blocks=parsed.blocks,
            parse_quality=parsed.parse_quality,
            status=parsed.status,
            source_path=None,
            progress_reporter=progress,
        )

    def _index_file_material(self, material: MaterialRecord, content: bytes, content_type: str | None, high_precision: bool):
        """按文件类型选择视频路径或普通二进制解析路径。"""
        progress = self._progress_reporter(material)
        document_id = f"material-{material.id}"
        if material.document_type.lower() in VIDEO_TYPES:
            source_path = self.object_storage.local_path(material)
            if source_path is None:
                raise BusinessError("视频资料必须存储在 Python 可访问的受控对象存储中")
            parsed = parse_video_source_with_worker_pool(
                parser_router=self.parser_router,
                document_id=document_id,
                title=material.title,
                document_type=material.document_type,
                source=material.source or "upload",
                user_id=material.user_id,
                visibility_scope="private",
                source_path=str(source_path),
                filename=material.original_filename or material.title,
                content_type=content_type,
                high_precision=high_precision,
                progress_reporter=progress,
            ) or self.parser_router.parse_video_source(
                document_id=document_id,
                title=material.title,
                document_type=material.document_type,
                source=material.source or "upload",
                user_id=material.user_id,
                visibility_scope="private",
                source_path=str(source_path),
                filename=material.original_filename or material.title,
                content_type=content_type,
                high_precision=high_precision,
                progress_reporter=progress,
            )
        else:
            parsed = self.parser_router.parse_bytes(
                content=content,
                filename=material.original_filename or material.title,
                document_id=document_id,
                source_title=material.title,
                document_type=material.document_type,
                content_type=content_type or mimetypes.guess_type(material.original_filename or material.title)[0],
                source_path=material.original_file_path,
                high_precision=high_precision,
                progress_reporter=progress,
            )
        return self.store.index_blocks(
            document_id=document_id,
            title=material.title,
            document_type=material.document_type,
            source=material.source or "upload",
            user_id=material.user_id,
            visibility_scope="private",
            language="zh-CN",
            parser=parsed.parser,
            blocks=parsed.blocks,
            parse_quality=parsed.parse_quality,
            status=parsed.status,
            source_path=material.original_file_path,
            progress_reporter=progress,
        )

    def _progress_reporter(self, material: MaterialRecord) -> RagProgressReporter:
        """进度直接落库，明确禁止由公开控制面回调 Java。"""
        return RagProgressReporter(
            document_id=f"material-{material.id}",
            user_id=material.user_id,
            persist=True,
            delivery_mode="database",
        )

    def _save_index_result(self, material_id: int, result: Any) -> MaterialRecord:
        """将既有 Python 索引响应转换为资料终态。"""
        with self.repository.transaction() as transaction:
            material = transaction.update_material_result(
                material_id,
                status=str(result.status),
                parser=str(result.parser),
                document_summary=str(result.documentSummary),
                chunk_count=int(result.chunkCount),
            )
        if material is None:
            raise RuntimeError("资料索引结果回写失败")
        return material

    def _mark_material_failed(self, material_id: int, error: Exception) -> MaterialRecord:
        """失败摘要只保留异常类别，避免把资料正文写入数据库。"""
        with self.repository.transaction() as transaction:
            material = transaction.update_material_result(
                material_id,
                status="FAILED",
                parser="python-rag-error",
                document_summary=f"Python RAG 索引失败：{error.__class__.__name__}",
                chunk_count=0,
            )
        if material is None:
            raise RuntimeError("资料索引失败状态回写失败")
        return material

    def _material_response_for_user(self, material: MaterialRecord, user_id: str) -> RagMaterialResponse:
        """重新读取资料以获得日志进度，同时保持用户范围校验。"""
        with self.repository.transaction() as transaction:
            current = self._require_material(transaction.find_material(material.id, user_id))
            return self._material_response(transaction, current)

    def _material_response(self, transaction: Any, material: MaterialRecord) -> RagMaterialResponse:
        """将数据库资料与最近进度转换为 React 兼容对象。"""
        try:
            progress_records = transaction.list_progress(material.id, 30)
        except Exception:
            progress_records = []
        events = [progress_record_to_event(item) for item in progress_records]
        latest = events[0] if events else None
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
            latestProgress=latest,
            progressEvents=events,
            processingProgress=build_material_processing_progress(
                material_status=material.status,
                events=events,
                created_at=material.created_at,
                updated_at=material.updated_at,
                failure_summary=material.document_summary if material.status == "FAILED" else None,
            ),
            createdAt=material.created_at,
            updatedAt=material.updated_at,
        )

    def _scoped_query_request(self, request: RagQueryPublicRequest, user_id: str) -> QueryRequest:
        """白名单保留业务过滤器，最后覆盖身份与可见范围。"""
        metadata: dict[str, Any] = {}
        for key, value in (request.metadataFilter or {}).items():
            if key not in BUSINESS_METADATA_FILTER_KEYS:
                continue
            normalized = normalize_filter_value(value)
            if normalized is not None:
                metadata[key] = normalized
        metadata["userId"] = user_id
        metadata["visibilityScope"] = "private"
        return QueryRequest(
            question=request.question,
            topK=clamp(request.topK, 1, 20, 5),
            candidateMultiplier=clamp(request.candidateMultiplier, 2, 10, 4),
            metadataFilter=metadata,
        )

    def _insert_query_history(
        self,
        request: RagQueryPublicRequest,
        user_id: str,
        response: QueryResponse,
        duration_ms: int,
        *,
        task_id: str | None,
        status: str,
    ) -> None:
        """保存查询结果与 answer guard 字段，供刷新后复原。"""
        with self.repository.transaction() as transaction:
            transaction.insert_query_history(
                user_id=user_id,
                task_id=task_id,
                question=request.question,
                status=status,
                top_k=clamp(request.topK, 1, 20, 5),
                answer=response.answer,
                evidence_count=len(response.evidences),
                expanded_queries=response.expandedQueries,
                evidences=[item.model_dump(mode="json") for item in response.evidences],
                diagnostics=diagnostics_with_answer_guard(response),
                progress_events=[item.model_dump(mode="json") for item in response.progressEvents],
                duration_ms=duration_ms,
            )

    def _save_failed_query_history(self, request: RagQueryPublicRequest, user_id: str, duration_ms: int) -> None:
        """同步查询失败也写入脱敏历史，避免用户看到静默丢失。"""
        try:
            with self.repository.transaction() as transaction:
                transaction.insert_query_history(
                    user_id=user_id,
                    task_id=None,
                    question=request.question,
                    status="FAILED",
                    top_k=clamp(request.topK, 1, 20, 5),
                    error_message="RAG 查询失败",
                    duration_ms=duration_ms,
                )
        except Exception:
            return

    def _update_query_task(
        self,
        task_id: str,
        user_id: str,
        response: QueryResponse | None,
        status: str,
        error_message: str | None,
        duration_ms: int,
    ) -> None:
        """任务执行结束后按用户范围更新其持久化快照。"""
        if response is None:
            expanded_queries: list[str] = []
            evidences: list[dict[str, Any]] = []
            diagnostics: dict[str, Any] = {}
            progress_events: list[dict[str, Any]] = []
            answer = None
        else:
            expanded_queries = response.expandedQueries
            evidences = [item.model_dump(mode="json") for item in response.evidences]
            diagnostics = diagnostics_with_answer_guard(response)
            progress_events = [item.model_dump(mode="json") for item in response.progressEvents]
            answer = response.answer
        try:
            with self.repository.transaction() as transaction:
                transaction.update_query_history(
                    task_id=task_id,
                    user_id=user_id,
                    answer=answer,
                    status=status,
                    evidence_count=len(evidences),
                    expanded_queries=expanded_queries,
                    evidences=evidences,
                    diagnostics=diagnostics,
                    progress_events=progress_events,
                    error_message=error_message,
                    duration_ms=duration_ms,
                )
        except Exception:
            return

    def _chunk_root(self) -> Path:
        """返回分片临时根目录，独立于最终对象存储根目录。"""
        return Path(os.getenv("EVIDENCE_UPLOAD_CHUNK_ROOT", "uploads/chunks")).expanduser().resolve()

    def _chunk_directory(self, user_id: str, upload_id: str) -> Path:
        """以用户和受控 upload ID 生成不可穿越的暂存目录。"""
        return self._chunk_root() / safe_path_token(user_id) / sanitize_upload_id(upload_id)

    @staticmethod
    def _require_material(material: MaterialRecord | None) -> MaterialRecord:
        """统一隐藏资料是否存在，避免泄露其他用户资料。"""
        if material is None:
            raise BusinessError("资料不存在")
        return material

    def _chunk_marker_material(self, marker: Path, user_id: str) -> MaterialRecord | None:
        """用 marker 防止重复最终分片创建重复资料。"""
        if not marker.is_file():
            return None
        try:
            material_id = int(marker.read_text(encoding="utf-8").strip())
            with self.repository.transaction() as transaction:
                return transaction.find_material(material_id, user_id)
        except Exception:
            return None

    def _completed_chunk_material(self, upload_key: str, user_id: str) -> MaterialRecord | None:
        """读取当前进程已完成的分片上传记录，支撑并发最终分片幂等返回。"""
        material_id = completed_chunk_material_id(upload_key)
        if material_id is None:
            return None
        try:
            with self.repository.transaction() as transaction:
                return transaction.find_material(material_id, user_id)
        except Exception:
            return None

    def _delete_stored_object(self, stored: StoredObject) -> None:
        """资料建档失败时清理刚写入的本地或 OSS 对象。"""
        if stored.storage_type == "local":
            try:
                Path(stored.source_path).unlink(missing_ok=True)
            except OSError:
                return
            return
        delete_object_key = getattr(self.object_storage, "delete_object_key", None)
        if stored.object_key and callable(delete_object_key):
            try:
                delete_object_key(stored.object_key)
            except Exception:
                return


def history_to_response(history: QueryHistoryRecord) -> RagQueryHistoryResponse:
    """把 JSON 快照恢复为 React 可直接渲染的查询历史。"""
    diagnostics = parse_json_object(history.diagnostics_json)
    answer_guard = diagnostics.get("answerGuard") if isinstance(diagnostics.get("answerGuard"), dict) else {}
    evidence_count = history.evidence_count
    return RagQueryHistoryResponse(
        id=history.id,
        taskId=history.task_id,
        question=history.question,
        answer=history.answer,
        answerStatus=str(answer_guard.get("answerStatus") or ("REFUSED" if evidence_count == 0 else "ANSWERED")),
        refusalReason=nullable_str(answer_guard.get("refusalReason")),
        refusalPolicy=str(answer_guard.get("refusalPolicy") or "STRICT_EVIDENCE_GUARD_V1"),
        confidence=safe_float(answer_guard.get("confidence")),
        supportingEvidenceIds=string_list(answer_guard.get("supportingEvidenceIds")),
        refusalMessage=nullable_str(answer_guard.get("refusalMessage")),
        status=history.status,
        topK=history.top_k,
        evidenceCount=evidence_count,
        expandedQueries=string_list(parse_json_value(history.expanded_queries_json, [])),
        evidences=[Evidence.model_validate(item) for item in parse_json_list(history.evidences_json)],
        diagnostics=diagnostics,
        progressEvents=parse_progress_events(history.progress_events_json),
        errorMessage=history.error_message,
        durationMs=history.duration_ms,
        createdAt=history.created_at,
        updatedAt=history.updated_at,
    )


def history_to_query_response(history: QueryHistoryRecord) -> QueryResponse:
    """把完成任务的历史快照转换回查询任务结果。"""
    public = history_to_response(history)
    return QueryResponse(
        answer=public.answer or "",
        answerStatus=public.answerStatus if public.answerStatus in {"ANSWERED", "REFUSED"} else "REFUSED",
        refusalReason=public.refusalReason,
        refusalPolicy=public.refusalPolicy,
        confidence=public.confidence,
        supportingEvidenceIds=public.supportingEvidenceIds,
        refusalMessage=public.refusalMessage,
        expandedQueries=public.expandedQueries,
        evidences=public.evidences,
        diagnostics=public.diagnostics,
        progressEvents=public.progressEvents,
    )


def diagnostics_with_answer_guard(response: QueryResponse) -> dict[str, Any]:
    """历史兼容字段集中到 diagnostics.answerGuard。"""
    diagnostics = dict(response.diagnostics or {})
    diagnostics["answerGuard"] = {
        "answerStatus": response.answerStatus,
        "refusalReason": response.refusalReason,
        "refusalPolicy": response.refusalPolicy,
        "confidence": response.confidence,
        "supportingEvidenceIds": response.supportingEvidenceIds,
        "refusalMessage": response.refusalMessage,
    }
    return diagnostics


def progress_record_to_event(record: ProgressLogRecord) -> ProgressEvent:
    """把脱敏日志上下文转换为用户可见阶段事件。"""
    context = parse_json_object(record.context_json)
    return ProgressEvent(
        stageCode=str(context.get("stageCode") or record.stage or "unknown"),
        stageLabel=public_progress_text(
            context.get("stageLabel") or context.get("stageCode") or record.stage,
            "处理中",
        ),
        message=public_progress_text(context.get("message") or record.message, "RAG 处理进度更新"),
        status=str(context.get("status") or ("RUNNING" if record.success is not False else "FAILED")),
        currentStep=safe_int_or_none(context.get("currentStep")),
        totalSteps=safe_int_or_none(context.get("totalSteps")),
        currentChunk=safe_int_or_none(context.get("currentChunk")),
        totalChunks=safe_int_or_none(context.get("totalChunks")),
        chunkId=nullable_str(context.get("chunkId")),
        blockId=nullable_str(context.get("blockId")),
        percent=safe_int_or_none(context.get("percent")),
        detail=public_progress_text(nullable_str(context.get("detail")), "") or None,
        createdAt=iso_or_none(record.created_at),
    )


def parse_progress_events(value: str | None) -> list[ProgressEvent]:
    """容错读取历史任务的进度 JSON。"""
    events: list[ProgressEvent] = []
    for item in parse_json_list(value):
        try:
            events.append(ProgressEvent.model_validate(item))
        except Exception:
            continue
    return events


def parse_json_value(value: str | None, fallback: Any) -> Any:
    """解析历史 JSON，损坏旧数据使用稳定默认值。"""
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def parse_json_list(value: str | None) -> list[Any]:
    """确保 JSON 历史列表不会因旧数据异常中断接口。"""
    parsed = parse_json_value(value, [])
    return parsed if isinstance(parsed, list) else []


def parse_json_object(value: str | None) -> dict[str, Any]:
    """确保 JSON 历史对象不会因旧数据异常中断接口。"""
    parsed = parse_json_value(value, {})
    return parsed if isinstance(parsed, dict) else {}


def validate_upload_content(content: bytes) -> None:
    """校验空文件和可配置的最大上传字节数。"""
    if not content:
        raise BusinessError("上传文件不能为空")
    max_bytes = positive_int("RAG_UPLOAD_MAX_BYTES", 512 * 1024 * 1024)
    if len(content) > max_bytes:
        raise BusinessError("上传文件超过允许大小")


def validate_upload_file(source_path: str | Path) -> None:
    """校验受控临时文件，并按文件大小限制单文件上传。"""
    path = Path(source_path).expanduser().resolve()
    if not path.is_file():
        raise BusinessError("上传临时文件不存在")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise BusinessError("读取上传临时文件失败") from exc
    if size <= 0:
        raise BusinessError("上传文件不能为空")
    if size > positive_int("RAG_UPLOAD_MAX_BYTES", 512 * 1024 * 1024):
        raise BusinessError("上传文件超过允许大小")


def validate_chunk_request(
    content: bytes | None,
    filename: str,
    chunk_index: int,
    total_chunks: int,
    total_size: int | None,
    *,
    chunk_size: int | None = None,
) -> None:
    """拒绝空、越界和无效总大小的分片参数。"""
    if not content and not chunk_size:
        raise BusinessError("上传分片不能为空")
    if not filename or not filename.strip():
        raise BusinessError("上传文件名不能为空")
    if chunk_index < 0 or total_chunks <= 0 or chunk_index >= total_chunks:
        raise BusinessError("分片参数不合法")
    if total_size is not None and total_size <= 0:
        raise BusinessError("文件总大小不合法")
    if chunk_size is not None and chunk_size <= 0:
        raise BusinessError("上传分片不能为空")
    if total_size is not None and chunk_size is not None and chunk_size > total_size:
        raise BusinessError("上传分片大小超过文件总大小")


def validate_preview_source(material: MaterialRecord, requested_source: str | None) -> None:
    """确认可选来源属于当前资料，防止预览成为任意文件读取入口。"""
    requested = normalize_preview_source(requested_source)
    if requested is None:
        return
    allowed = [
        material.original_file_path,
        material.public_url,
        material.object_key,
        f"oss://{material.object_key}" if material.object_key else None,
    ]
    if not any(preview_source_matches(normalize_preview_source(item), requested) for item in allowed):
        raise BusinessError("预览来源不属于当前资料")


def normalize_preview_source(value: str | None) -> str | None:
    """规范化来源地址，忽略前端 evidence URL 的锚点部分。"""
    if not value or not value.strip():
        return None
    text = value.strip().strip("<>")
    return text.split("#", maxsplit=1)[0].replace("\\", "/")


def preview_source_matches(allowed: str | None, requested: str | None) -> bool:
    """兼容完整 URL、object key 与文件名三种 evidence 来源。"""
    if not allowed or not requested:
        return False
    return requested == allowed or requested.endswith("/" + allowed) or allowed.endswith("/" + requested)


def preview_content_type(document_type: str) -> str:
    """按文本格式返回浏览器可用的 UTF-8 内容类型。"""
    return "text/markdown; charset=UTF-8" if document_type.lower() in {"markdown", "md"} else "text/plain; charset=UTF-8"


def build_indexed_preview_content(title: str, evidences: list[Evidence], *, maximum_length: int = 1_500_000) -> str:
    """按 RAG chunk 顺序组装可跳转的章节、页码和原文片段。"""
    safe_title = " ".join(str(title or "学习资料").split()).replace("#", "") or "学习资料"
    lines = [
        f"# {safe_title}",
        "",
        "> 当前页面由该资料已入库的 RAG 原文片段组成，章节和页码用于定位答案来源。",
        "",
    ]
    last_section = ""
    seen: set[tuple[str, str]] = set()
    for evidence in evidences:
        section = " ".join(str(evidence.sectionName or evidence.sectionTitle or "全文").split()).replace("#", "") or "全文"
        snippet = " ".join(str(evidence.snippet or "").split())
        if not snippet:
            continue
        dedupe_key = (section, snippet)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        if section != last_section:
            lines.extend([f"## {section}", ""])
            last_section = section
        location = evidence_location_label(evidence)
        if location:
            lines.extend([f"> {location}", ""])
        lines.extend([snippet, ""])
        if sum(len(item) + 1 for item in lines) >= maximum_length:
            break
    content = "\n".join(lines).strip()
    return content[:maximum_length].rstrip() + ("..." if len(content) > maximum_length else "")


def evidence_location_label(evidence: Evidence) -> str:
    """将 evidence 的页码、幻灯片、表格或视频位置转换为中文定位标签。"""
    labels: list[str] = []
    if evidence.pageIndex is not None:
        labels.append(f"页码 {evidence.pageIndex}")
    if evidence.slideIndex is not None:
        labels.append(f"幻灯片 {evidence.slideIndex}")
    if evidence.sheetName:
        labels.append(f"工作表 {evidence.sheetName}")
    if evidence.cellRange:
        labels.append(f"单元格 {evidence.cellRange}")
    if evidence.startTime:
        time_range = evidence.startTime
        if evidence.endTime:
            time_range += f"-{evidence.endTime}"
        labels.append(f"时间 {time_range}")
    return " · ".join(labels)


def copy_video_sidecars_to_storage(
    *,
    stored: StoredObject,
    object_storage: RagObjectStorage,
    source_path: str | Path | None,
    filename: str,
    document_type: str,
    user_id: str,
) -> None:
    """将视频同名字幕同步到实际存储，供 worker 下载原视频后优先发现字幕。"""
    storage_type = str(stored.storage_type or "").strip().lower()
    if not is_video_upload(filename, document_type):
        return
    candidates = iter_video_sidecar_candidates(source_path, filename)
    if storage_type == "oss":
        upload_sidecar = getattr(object_storage, "store_video_sidecar_file", None)
        if not callable(upload_sidecar) or not stored.object_key:
            return
        copied_suffixes: set[str] = set()
        for candidate in candidates:
            suffix = candidate.suffix.lower()
            if suffix not in VIDEO_SIDECAR_SUFFIXES or suffix in copied_suffixes:
                continue
            try:
                resolved_candidate = candidate.expanduser().resolve()
                if not resolved_candidate.is_file():
                    continue
                upload_sidecar(resolved_candidate, stored.object_key, user_id)
                copied_suffixes.add(suffix)
            except Exception as exc:
                # 字幕侧车文件只是视频解析增强信息，上传失败不能阻断资料主流程。
                logger.warning("上传 OSS 视频侧车字幕失败，已忽略: %s", exc)
        return
    if storage_type != "local":
        return
    try:
        target_video = Path(stored.source_path).expanduser().resolve()
    except (OSError, RuntimeError):
        return
    if not target_video.is_file():
        return

    copied_suffixes: set[str] = set()
    for candidate in candidates:
        suffix = candidate.suffix.lower()
        if suffix not in VIDEO_SIDECAR_SUFFIXES or suffix in copied_suffixes:
            continue
        try:
            resolved_candidate = candidate.expanduser().resolve()
            if not resolved_candidate.is_file() or resolved_candidate == target_video.with_suffix(suffix).resolve():
                continue
            _copy_file(resolved_candidate, target_video.with_suffix(suffix))
            copied_suffixes.add(suffix)
        except Exception as exc:
            # 字幕侧车文件只是视频解析增强信息，复制失败不能阻断资料上传主流程。
            logger.warning("复制视频侧车字幕失败，已忽略: %s", exc)


def is_video_upload(filename: str, document_type: str) -> bool:
    """判断本次上传是否属于视频资料。"""
    detected_type = str(document_type or "").strip().lower()
    suffix_type = Path(filename or "").suffix.lower().lstrip(".")
    return detected_type in VIDEO_TYPES or suffix_type in VIDEO_TYPES


def iter_video_sidecar_candidates(source_path: str | Path | None, filename: str) -> list[Path]:
    """按上传源目录与额外搜索目录寻找同名 .srt/.vtt 字幕。"""
    candidates: list[Path] = []
    original_name = Path(safe_filename(filename))
    source = Path(source_path).expanduser() if source_path is not None else None
    if source is not None:
        # 同目录优先查原始文件名；兼容临时源文件本身已经保留原名的情况。
        for suffix in VIDEO_SIDECAR_SUFFIXES:
            candidates.append(source.parent / original_name.with_suffix(suffix).name)
            candidates.append(source.with_suffix(suffix))

    for directory in configured_sidecar_search_dirs():
        for suffix in VIDEO_SIDECAR_SUFFIXES:
            candidates.append(directory / original_name.with_suffix(suffix).name)
    return unique_paths(candidates)


def configured_sidecar_search_dirs() -> list[Path]:
    """读取 RAG_VIDEO_SIDECAR_SEARCH_DIRS 中用分号或逗号分隔的字幕搜索目录。"""
    raw_value = os.getenv("RAG_VIDEO_SIDECAR_SEARCH_DIRS", "")
    directories: list[Path] = []
    for item in raw_value.replace(";", ",").split(","):
        text = item.strip().strip('"')
        if text:
            directories.append(Path(text).expanduser())
    return directories


def unique_paths(paths: list[Path]) -> list[Path]:
    """保留候选路径顺序并去重，避免重复复制同一个字幕文件。"""
    seen: set[str] = set()
    unique: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def detect_document_type(filename: str) -> str:
    """根据受控文件名后缀推断解析路线。"""
    return DOCUMENT_TYPES.get(Path(filename).suffix.lower(), "text")


def normalized_document_type(value: str | None, default: str) -> str:
    """规范化客户端提供的资料类型，空值使用接口默认。"""
    return (value or "").strip().lower() or default


def non_blank(value: str | None, fallback: str) -> str:
    """将空字符串收敛为业务默认值。"""
    return value.strip() if value and value.strip() else fallback


def safe_filename(value: str) -> str:
    """移除文件名中的路径分隔符和 Windows 保留字符。"""
    cleaned = "".join("_" if char in '\\\\/:*?\"<>|' else char for char in value).strip()
    return cleaned or "material"


def safe_path_token(value: str) -> str:
    """将用户和类型压缩为单个无路径语义的目录名。"""
    cleaned = "".join(char if char.isalnum() or char in "_-" else "_" for char in value.strip())
    return cleaned or "anonymous"


def chunk_upload_completion_key(
    *,
    directory: Path,
    user_id: str,
    upload_id: str,
    filename: str,
    total_chunks: int,
    total_size: int | None,
) -> str:
    """生成分片上传完成幂等键，避免并发最终分片重复合并建档。"""
    safe_size = "" if total_size is None else str(total_size)
    return "|".join(
        [
            str(directory.resolve()),
            safe_path_token(user_id),
            sanitize_upload_id(upload_id),
            safe_filename(filename),
            str(total_chunks),
            safe_size,
        ]
    )


def chunk_upload_lock(upload_key: str) -> threading.Lock:
    """返回 upload 级别进程内锁，保证单进程 API 并发收尾只执行一次。"""
    with _CHUNK_UPLOAD_LOCKS_GUARD:
        lock = _CHUNK_UPLOAD_LOCKS.get(upload_key)
        if lock is None:
            lock = threading.Lock()
            _CHUNK_UPLOAD_LOCKS[upload_key] = lock
        return lock


def remember_completed_chunk_material(upload_key: str, material_id: int) -> None:
    """记录已完成上传对应资料，供同一 uploadId 的并发请求直接复用。"""
    with _CHUNK_UPLOAD_LOCKS_GUARD:
        _CHUNK_UPLOAD_COMPLETED_MATERIALS[upload_key] = material_id


def completed_chunk_material_id(upload_key: str) -> int | None:
    """读取已完成分片上传资料 ID。"""
    with _CHUNK_UPLOAD_LOCKS_GUARD:
        return _CHUNK_UPLOAD_COMPLETED_MATERIALS.get(upload_key)


def sanitize_upload_id(value: str | None) -> str:
    """upload ID 仅保留安全字符，防止目录穿越。"""
    if not value:
        return ""
    return "".join(char for char in value if char.isalnum() or char in "_-")


def completed_chunk_response(
    *,
    upload_id: str,
    filename: str,
    chunk_index: int,
    total_chunks: int,
    material: RagMaterialResponse,
) -> MaterialUploadChunkResponse:
    """构造同一 uploadId 的幂等完成响应。"""
    return MaterialUploadChunkResponse(
        uploadId=upload_id,
        filename=filename,
        chunkIndex=chunk_index,
        totalChunks=total_chunks,
        receivedChunks=total_chunks,
        nextChunkIndex=total_chunks,
        status="PROCESSING",
        message="视频分片已收齐，继续沿用已有资料记录",
        completed=True,
        material=material,
    )


def oss_multipart_state_path(directory: Path) -> Path:
    """返回仅含元数据的 OSS multipart 会话文件，不保存视频正文。"""
    return directory / "oss-multipart.json"


def load_oss_multipart_state(path: Path) -> dict[str, Any] | None:
    """读取可续传的 OSS multipart 会话元数据。"""
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BusinessError("OSS 分片上传会话状态损坏，请重新选择文件上传") from exc
    if not isinstance(raw, dict):
        raise BusinessError("OSS 分片上传会话状态不合法")
    return raw


def save_oss_multipart_state(path: Path, state: dict[str, Any]) -> None:
    """原子写入小型 multipart 会话元数据，避免并发请求留下半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(state, output, ensure_ascii=False, separators=(",", ":"))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def validate_oss_multipart_state(
    state: dict[str, Any],
    *,
    filename: str,
    total_chunks: int,
    total_size: int | None,
) -> None:
    """拒绝同一 uploadId 被不同文件或分片参数复用。"""
    if str(state.get("objectKey") or "").strip() == "" or str(state.get("ossUploadId") or "").strip() == "":
        raise BusinessError("OSS 分片上传会话缺少对象信息")
    if state.get("filename") != filename or safe_int_or_none(state.get("totalChunks")) != total_chunks:
        raise BusinessError("uploadId 已绑定其他视频或分片数量")
    stored_total_size = safe_int_or_none(state.get("totalSize"))
    if stored_total_size != total_size:
        raise BusinessError("uploadId 已绑定其他文件大小")
    if not isinstance(state.get("parts"), dict):
        raise BusinessError("OSS 分片上传会话缺少 part 信息")


def oss_multipart_parts(state: dict[str, Any], total_chunks: int) -> dict[int, OssMultipartPart]:
    """把会话 JSON 中已确认的 ETag 转换为连续性可校验的 part 清单。"""
    raw_parts = state.get("parts")
    if not isinstance(raw_parts, dict):
        raise BusinessError("OSS 分片上传会话缺少 part 信息")
    parts: dict[int, OssMultipartPart] = {}
    for raw_index, raw_part in raw_parts.items():
        try:
            index = int(raw_index)
        except (TypeError, ValueError) as exc:
            raise BusinessError("OSS 分片序号不合法") from exc
        if index < 0 or index >= total_chunks or not isinstance(raw_part, dict):
            raise BusinessError("OSS 分片序号不合法")
        etag = str(raw_part.get("etag") or "").strip()
        size = safe_int_or_none(raw_part.get("size"))
        if not etag or size is None or size <= 0:
            raise BusinessError("OSS 分片确认信息不完整")
        parts[index] = OssMultipartPart(part_number=index + 1, etag=etag, size=size)
    return parts


def next_missing_oss_chunk_index(parts: dict[int, OssMultipartPart], total_chunks: int) -> int:
    """返回 OSS multipart 最小缺失片序号，供前端续传。"""
    for index in range(total_chunks):
        if index not in parts:
            return index
    return total_chunks


def chunk_filename(index: int) -> str:
    """生成固定宽度分片文件名。"""
    return f"chunk-{index:05d}.part"


def count_received_chunks(directory: Path) -> int:
    """只统计完成原子写入的合法分片文件。"""
    return sum(1 for path in directory.glob("chunk-*.part") if path.name.startswith("chunk-") and len(path.stem) == 11)


def next_missing_chunk_index(directory: Path, total_chunks: int) -> int:
    """返回最小缺失序号，供前端断点续传。"""
    for index in range(total_chunks):
        if not (directory / chunk_filename(index)).is_file():
            return index
    return total_chunks


def merge_chunks(directory: Path, filename: str, total_chunks: int, total_size: int | None) -> Path:
    """按序合并全部分片并核对前端声明的总大小。"""
    target = directory / f"merged-{safe_filename(filename)}"
    with target.open("wb") as output:
        for index in range(total_chunks):
            chunk = directory / chunk_filename(index)
            if not chunk.is_file():
                raise BusinessError(f"上传分片缺失: {index}")
            with chunk.open("rb") as source:
                shutil.copyfileobj(source, output)
    if total_size is not None and target.stat().st_size != total_size:
        raise BusinessError("分片合并后的文件大小与前端声明不一致")
    return target


def _copy_file(source: Path, target: Path) -> None:
    """使用固定缓冲区复制上传分片并保留原子替换语义。"""
    with source.open("rb") as input_file, target.open("wb") as output_file:
        shutil.copyfileobj(input_file, output_file, length=1024 * 1024)
        output_file.flush()
        os.fsync(output_file.fileno())


def cleanup_chunk_directory(directory: Path, root: Path) -> None:
    """仅清理位于配置根目录内的完成分片目录。"""
    try:
        directory.resolve().relative_to(root.resolve())
    except ValueError:
        return
    shutil.rmtree(directory, ignore_errors=True)


def normalize_filter_value(value: Any) -> Any:
    """删除空过滤值，并统一页码、幻灯片页码为字符串。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (list, tuple, set)):
        values = [str(item).strip() for item in value if str(item).strip()]
        return values or None
    return value


def encode_material_cursor(updated_at: datetime, material_id: int) -> str:
    """把更新时间和 ID 编码为不透明游标，避免客户端拼接 SQL 条件。"""
    raw = f"{updated_at.isoformat()}|{material_id}".encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_material_cursor(cursor: str | None) -> tuple[datetime | None, int | None]:
    """解析游标；非法游标以中文业务错误拒绝，避免静默跳页。"""
    if not cursor:
        return None, None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        timestamp, raw_id = decoded.rsplit("|", 1)
        return datetime.fromisoformat(timestamp), int(raw_id)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise BusinessError("分页游标无效") from exc


def clamp(value: int | None, minimum: int, maximum: int, default: int) -> int:
    """按公开契约限制数值，异常类型使用默认值。"""
    try:
        candidate = int(default if value is None else value)
    except (TypeError, ValueError):
        candidate = default
    return max(minimum, min(maximum, candidate))


def clamp_date(value: date, minimum: date, maximum: date) -> date:
    """将日期范围限制在历史查询允许的七日窗口。"""
    return minimum if value < minimum else maximum if value > maximum else value


def positive_int(name: str, default: int) -> int:
    """读取正整数配置，非法值保持稳定默认。"""
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def elapsed_ms(start: float) -> int:
    """统一生成安全的毫秒耗时。"""
    return max(0, round((time.perf_counter() - start) * 1000))


def safe_int_or_none(value: Any) -> int | None:
    """读取可选整数字段，非法旧值不影响进度展示。"""
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def safe_float(value: Any) -> float:
    """读取可选置信度，异常旧值回退为零。"""
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def nullable_str(value: Any) -> str | None:
    """将空值转换为 None，避免前端收到无意义字符串。"""
    return None if value is None else str(value)


def string_list(value: Any) -> list[str]:
    """转换 JSON 数组中的文本项。"""
    return [str(item) for item in value] if isinstance(value, list) else []


def iso_or_none(value: datetime | None) -> str | None:
    """保持内部 QueryTaskResponse 使用 ISO 时间字符串的既有契约。"""
    return value.isoformat() if value else None
