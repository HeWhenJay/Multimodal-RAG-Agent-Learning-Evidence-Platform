from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Callable

from app.schemas.rag import ProgressEvent


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
    "parse.video.asr": "视频 ASR",
    "parse.video.frame.extract": "抽取视频候选帧",
    "parse.video.frame.candidates": "视频候选帧",
    "parse.video.slide_detect": "PPT 翻页检测",
    "parse.video.ocr": "关键帧 OCR",
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
    "query.guard": "回答准入",
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
        on_emit: Callable[[ProgressEvent], None] | None = None,
        delivery_mode: str | None = None,
        kafka_producer: Any | None = None,
        kafka_context: dict[str, Any] | None = None,
    ) -> None:
        self.document_id = document_id
        self.user_id = user_id or "anonymous"
        self.schema = os.getenv("RAG_DATABASE_SCHEMA", "learning_evidence")
        self.database_url = database_url or os.getenv("RAG_DATABASE_URL") or os.getenv("DATABASE_URL")
        self.delivery_mode = normalize_delivery_mode(delivery_mode, persist=persist, on_emit=on_emit)
        self.persist = (
            persist
            and bool(self.database_url)
            and self.delivery_mode == "database"
            and progress_persist_enabled()
        )
        self.material_id = parse_material_id(document_id)
        self.events: list[ProgressEvent] = []
        self.on_emit = on_emit
        self.kafka_producer = kafka_producer
        self.kafka_context = kafka_context or {}

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
        extra_context: dict[str, Any] | None = None,
    ) -> ProgressEvent:
        """追加一次进度事件，并直接写入 PostgreSQL 供前端轮询。"""
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
        if self.on_emit:
            try:
                self.on_emit(event)
            except Exception as exc:
                logger.debug("RAG 进度回调失败，已忽略: %s", exc)
        self._console(event)
        if self.delivery_mode == "none" or self.delivery_mode == "memory":
            return event
        if self.delivery_mode == "kafka":
            self._publish_kafka(event, parser=parser, extra_context=extra_context)
            return event
        if self.persist:
            self._persist(event, parser=parser, extra_context=extra_context)
        return event

    def _publish_kafka(self, event: ProgressEvent, *, parser: str | None, extra_context: dict[str, Any] | None) -> None:
        """Kafka worker 模式只通过 progress topic 上报，避免 HTTP 回调和 DB fallback 重复落库。"""
        if self.kafka_producer is None:
            logger.debug("RAG Kafka progress producer 未配置，跳过进度发送: %s", event.stageCode)
            return
        context = dict(self.kafka_context)
        if extra_context:
            context.update(extra_context)
        try:
            self.kafka_producer.send_progress(
                event=event,
                document_id=self.document_id,
                material_id=self.material_id,
                user_id=self.user_id,
                parser=parser,
                extra_context=context,
            )
        except Exception as exc:
            logger.warning("RAG Kafka 进度发送失败: %s", exc)

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

    def _persist(self, event: ProgressEvent, *, parser: str | None, extra_context: dict[str, Any] | None) -> None:
        try:
            import psycopg
            from psycopg import sql
        except ImportError:
            return

        context = event.model_dump(mode="json")
        context["documentId"] = self.document_id
        context["materialId"] = self.material_id
        if extra_context:
            context.update(extra_context)
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


def progress_persist_enabled() -> bool:
    """判断进度事件是否需要写入数据库，内存后端默认跳过落库。"""
    configured = os.getenv("RAG_PROGRESS_PERSIST_ENABLED")
    if configured is not None and configured.strip() != "":
        return configured.strip().lower() in {"1", "true", "yes", "y", "on"}
    return os.getenv("RAG_STORE_BACKEND", "").strip().lower() != "memory"


def normalize_delivery_mode(delivery_mode: str | None, *, persist: bool, on_emit: Callable[[ProgressEvent], None] | None) -> str:
    """根据显式参数和旧 persist/on_emit 语义选择进度投递模式。"""
    if delivery_mode:
        mode = delivery_mode.strip().lower()
    elif on_emit is not None and not persist:
        mode = "memory"
    else:
        mode = os.getenv("RAG_PROGRESS_DELIVERY_MODE", "database").strip().lower()
    if mode not in {"database", "kafka", "memory", "none"}:
        return "database"
    return mode
