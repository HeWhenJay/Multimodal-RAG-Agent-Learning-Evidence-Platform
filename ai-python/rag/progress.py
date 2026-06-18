from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.schemas.rag import ProgressEvent
from rag.log_callback import post_log_event


logger = logging.getLogger(__name__)

STAGE_LABELS = {
    "index.request": "接收索引请求",
    "parse.route": "选择解析路线",
    "parse.pdf": "解析 PDF",
    "parse.docx": "解析 Word",
    "parse.pptx": "解析 PPT",
    "parse.spreadsheet": "解析表格",
    "parse.image.ocr": "图片 OCR",
    "parse.video": "处理视频",
    "parse.text": "解析文本",
    "parse.completed": "解析完成",
    "sanitize.blocks": "清洗文本",
    "chunk.recursive": "递归切块",
    "summary.index": "摘要索引",
    "embedding.chunk": "生成 embedding",
    "vector.upsert.chunk": "写入向量数据库",
    "memory.upsert.chunk": "写入内存索引",
    "index.completed": "索引完成",
    "index.failed": "索引失败",
    "query.expand": "Multi-Query",
    "query.filter": "过滤候选块",
    "query.bm25": "BM25 召回",
    "query.vector": "向量召回",
    "query.fusion": "RAG-Fusion",
    "query.rerank": "重排",
    "query.answer": "生成回答",
}


class RagProgressReporter:
    """记录 RAG 阶段进度；数据库写入失败不影响主索引流程。"""

    def __init__(
        self,
        *,
        document_id: str,
        user_id: str = "anonymous",
        database_url: str | None = None,
        persist: bool = True,
    ) -> None:
        self.document_id = document_id
        self.user_id = user_id or "anonymous"
        self.schema = os.getenv("RAG_DATABASE_SCHEMA", "learning_evidence")
        self.database_url = database_url or os.getenv("RAG_DATABASE_URL") or os.getenv("DATABASE_URL")
        self.persist = persist and bool(self.database_url)
        self.material_id = parse_material_id(document_id)
        self.events: list[ProgressEvent] = []

    def emit(
        self,
        stage_code: str,
        message: str,
        *,
        status: str = "RUNNING",
        current_step: int | None = None,
        total_steps: int | None = None,
        current_chunk: int | None = None,
        total_chunks: int | None = None,
        chunk_id: str | None = None,
        block_id: str | None = None,
        percent: int | None = None,
        detail: str | None = None,
        parser: str | None = None,
    ) -> ProgressEvent:
        """追加一次进度事件，并尽量写入 Java 侧 log_event 表供前端轮询。"""
        safe_percent = normalize_percent(percent, current_step, total_steps)
        event = ProgressEvent(
            stageCode=stage_code,
            stageLabel=STAGE_LABELS.get(stage_code, stage_code),
            message=message,
            status=status if status in {"RUNNING", "COMPLETED", "FAILED"} else "RUNNING",
            currentStep=current_step,
            totalSteps=total_steps,
            currentChunk=current_chunk,
            totalChunks=total_chunks,
            chunkId=chunk_id,
            blockId=block_id,
            percent=safe_percent,
            detail=detail,
            createdAt=datetime.now(timezone.utc).isoformat(),
        )
        self.events.append(event)
        self._console(event)
        if self._post_callback(event, parser=parser):
            return event
        if self.persist:
            self._persist(event, parser=parser)
        return event

    def _console(self, event: ProgressEvent) -> None:
        """每个用户可见进度都输出到 Python 控制台，便于大文件解析时定位当前阶段。"""
        if not console_progress_enabled():
            return
        parts = [
            f"documentId={self.document_id}",
            f"stage={event.stageCode}",
            f"status={event.status}",
        ]
        if event.percent is not None:
            parts.append(f"percent={event.percent}%")
        if event.currentStep is not None and event.totalSteps is not None:
            parts.append(f"step={event.currentStep}/{event.totalSteps}")
        if event.currentChunk is not None and event.totalChunks is not None:
            parts.append(f"chunk={event.currentChunk}/{event.totalChunks}")
        if event.chunkId:
            parts.append(f"chunkId={event.chunkId}")
        parts.append(f"message={event.message}")
        if event.detail:
            parts.append(f"detail={event.detail}")
        text = "RAG进度 | " + " | ".join(parts)
        logger.info(text)
        print(text, flush=True)

    def _post_callback(self, event: ProgressEvent, *, parser: str | None) -> bool:
        """优先回调 Java 日志接口，让前端无需等待 Python 请求结束即可轮询进度。"""
        context = event.model_dump(mode="json")
        context["documentId"] = self.document_id
        context["materialId"] = self.material_id
        action = "rag_progress_" + re.sub(r"[^a-zA-Z0-9_]+", "_", event.stageCode).strip("_")
        payload = {
            "traceId": "py_" + uuid.uuid4().hex,
            "userId": truncate(self.user_id, 120),
            "source": "python",
            "domain": "rag",
            "level": "INFO",
            "module": "material",
            "stage": truncate(event.stageCode, 80),
            "eventType": "rag_progress",
            "action": truncate(action, 120),
            "message": truncate(event.message, 500),
            "success": event.status != "FAILED",
            "materialId": self.material_id,
            "documentId": truncate(self.document_id, 120),
            "parser": truncate(parser, 80),
            "context": context,
        }
        return post_log_event(payload)

    def _persist(self, event: ProgressEvent, *, parser: str | None) -> None:
        try:
            import psycopg
            from psycopg import sql
        except ImportError:
            return

        context = event.model_dump(mode="json")
        context["documentId"] = self.document_id
        context["materialId"] = self.material_id
        action = "rag_progress_" + re.sub(r"[^a-zA-Z0-9_]+", "_", event.stageCode).strip("_")
        try:
            with psycopg.connect(self.database_url) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(sql.SQL("SET search_path TO {}, public").format(sql.Identifier(self.schema)))
                    cursor.execute(
                        """
                        INSERT INTO log_event (
                            trace_id,
                            user_id,
                            source,
                            domain,
                            level,
                            module,
                            stage,
                            event_type,
                            action,
                            message,
                            success,
                            material_id,
                            document_id,
                            parser,
                            context_json
                        )
                        VALUES (%s, %s, 'python', 'rag', 'INFO', 'material', %s,
                                'rag_progress', %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            "py_" + uuid.uuid4().hex,
                            truncate(self.user_id, 120),
                            truncate(event.stageCode, 80),
                            truncate(action, 120),
                            truncate(event.message, 500),
                            event.status != "FAILED",
                            self.material_id,
                            truncate(self.document_id, 120),
                            truncate(parser, 80),
                            truncate(json.dumps(context, ensure_ascii=False), 20000),
                        ),
                    )
        except Exception:
            return


def parse_material_id(document_id: str) -> int | None:
    match = re.match(r"^material-(\d+)$", document_id or "")
    return int(match.group(1)) if match else None


def normalize_percent(percent: int | None, current_step: int | None, total_steps: int | None) -> int | None:
    if percent is not None:
        return max(0, min(100, int(percent)))
    if current_step is not None and total_steps:
        return max(0, min(100, round(current_step * 100 / total_steps)))
    return None


def truncate(value: str | None, max_length: int) -> str | None:
    if value is None or len(value) <= max_length:
        return value
    return value[:max_length]


def console_progress_enabled() -> bool:
    """读取控制台进度输出开关，默认开启。"""
    value = os.getenv("RAG_CONSOLE_PROGRESS_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}
