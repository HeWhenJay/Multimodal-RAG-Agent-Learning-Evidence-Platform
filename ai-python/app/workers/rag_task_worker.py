"""查询与 Kafka 关闭时本地索引的 PostgreSQL 耐久 worker。"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import signal
import socket
import threading
import time
from typing import Any
from uuid import uuid4

from app.core.runtime_config import load_runtime_config, parse_args
from app.repositories.rag_job import RagIndexJobRecord, RagJobRepository
from app.repositories.rag_task import DurableQueryTask, RagQueryTaskRepositoryProtocol, build_query_task_repository
from app.schemas.kafka import IndexRequestPayload, KafkaEnvelope, PromoteRequestPayload
from app.schemas.rag import ProgressEvent, QueryRequest
from app.workers.rag_kafka_state import RagKafkaStateWriter
from rag.kafka.producer import build_envelope
from rag.kafka.worker import RagKafkaIndexWorker, RagKafkaPromoteWorker
from rag.observability.progress import RagProgressReporter
from rag.retrievers.retrieval import create_rag_store


class CapturingProducer:
    """本地 worker 的消息捕获器，复用 Kafka worker 的状态机而不访问 Broker。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, KafkaEnvelope]] = []

    def send(self, topic: str, key: str, envelope: KafkaEnvelope) -> None:
        self.sent.append((topic, key, envelope))


class LocalProgressProducer:
    """将与 Kafka 同形的 progress envelope 直接交给 PostgreSQL 状态写回器。"""

    def __init__(self, state_writer: RagKafkaStateWriter, *, sequence_start: int = 0) -> None:
        self.state_writer = state_writer
        self.sequence = max(0, sequence_start)

    def send_progress(
        self,
        *,
        event: ProgressEvent,
        document_id: str,
        material_id: int | None,
        user_id: str,
        parser: str | None,
        extra_context: dict[str, Any],
    ) -> None:
        """同步落库，local 模式不会因缺少 Kafka 丢失用户可见进度。"""
        self.sequence += 1
        canonical = str(extra_context.get("canonicalDocumentId") or document_id)
        job_id = str(extra_context.get("jobId") or "")
        payload = event.model_dump(mode="json")
        payload.update(
            {
                "jobId": job_id,
                "materialId": int(extra_context.get("materialId") or material_id or 0),
                "canonicalDocumentId": canonical,
                "stagingDocumentId": str(extra_context.get("stagingDocumentId") or document_id),
                "userId": user_id,
                "parser": parser,
                "requestVersion": int(extra_context.get("requestVersion") or 0),
                "progressSequence": self.sequence,
            }
        )
        envelope = build_envelope(
            message_type="RAG_INDEX_PROGRESS",
            partition_key=canonical,
            idempotency_key=f"RAG_PROGRESS:{canonical}:{job_id}:{self.sequence}:v1",
            payload=payload,
        )
        self.state_writer.handle_progress(envelope)


class RagDurableTaskWorker:
    """从 PostgreSQL 抢占查询和 local 索引任务，进程重启后可继续恢复。"""

    def __init__(
        self,
        *,
        query_repository: RagQueryTaskRepositoryProtocol | None = None,
        index_repository: RagJobRepository | None = None,
        state_writer: RagKafkaStateWriter | None = None,
        store: Any | None = None,
        parser_router: Any | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.query_repository = query_repository or build_query_task_repository()
        self.index_repository = index_repository or RagJobRepository()
        self.state_writer = state_writer or RagKafkaStateWriter(repository=self.index_repository)
        self.store = store or create_rag_store()
        self.parser_router = parser_router
        self.worker_id = worker_id or f"{socket.gethostname() or 'python'}-{uuid4().hex[:12]}"

    def run_once(self) -> dict[str, int]:
        """执行一轮抢占任务；单个任务失败不阻塞同轮其他任务。"""
        summary = {"queryClaimed": 0, "queryCompleted": 0, "queryFailed": 0, "indexClaimed": 0, "indexCompleted": 0, "indexFailed": 0}
        batch_size = positive_int("RAG_TASK_WORKER_BATCH_SIZE", 4)
        lease_seconds = positive_int("RAG_TASK_WORKER_LEASE_SECONDS", 120)
        for task in self.query_repository.claim(worker_id=self.worker_id, batch_size=batch_size, lease_seconds=lease_seconds):
            summary["queryClaimed"] += 1
            if self._run_query_task(task):
                summary["queryCompleted"] += 1
            else:
                summary["queryFailed"] += 1
        if local_index_enabled():
            for job in self.index_repository.claim_local_jobs(
                worker_id=self.worker_id,
                batch_size=batch_size,
                lease_seconds=lease_seconds,
            ):
                summary["indexClaimed"] += 1
                if self._run_local_index_job(job):
                    summary["indexCompleted"] += 1
                else:
                    summary["indexFailed"] += 1
        return summary

    def _run_query_task(self, task: DurableQueryTask) -> bool:
        """执行一条已租约查询，并逐阶段更新其持久化历史。"""
        start = time.perf_counter()
        try:
            request = QueryRequest.model_validate(json.loads(task.request_json))

            def on_emit(event: ProgressEvent) -> None:
                self.query_repository.append_progress(task.task_id, task.user_id, event.model_dump(mode="json"))

            reporter = RagProgressReporter(
                document_id=f"query-{task.task_id}",
                user_id=task.user_id,
                persist=False,
                on_emit=on_emit,
            )
            response = self.store.query(request, progress_reporter=reporter)
            self.query_repository.complete(
                task.task_id,
                task.user_id,
                response.model_dump(mode="json"),
                elapsed_ms(start),
            )
            return True
        except Exception:
            self.query_repository.fail(task.task_id, task.user_id, "RAG 查询失败", elapsed_ms(start))
            return False

    def _run_local_index_job(self, job: RagIndexJobRecord) -> bool:
        """执行 local staging/promote 状态机，复用与 Kafka 相同的解析和防旧版本规则。"""
        try:
            payload = IndexRequestPayload.model_validate(json.loads(job.request_json))
            source_envelope = build_envelope(
                message_type="RAG_INDEX_REQUESTED",
                partition_key=payload.canonicalDocumentId,
                idempotency_key=f"RAG_INDEX:{payload.canonicalDocumentId}:{payload.jobId}:v1",
                payload=payload.model_dump(mode="json"),
                attempt=max(0, job.attempt - 1),
            )
            captured = CapturingProducer()
            # 重试任务使用独立序号区间，避免 consumed-event 将重试进度误判为第一次尝试的重复消息。
            progress_producer = LocalProgressProducer(self.state_writer, sequence_start=max(0, job.attempt - 1) * 100000)
            index_worker = RagKafkaIndexWorker(
                store=self.store,
                parser_router=self.parser_router,
                producer=captured,
                progress_producer=progress_producer,
            )
            index_worker.handle_envelope(source_envelope)
            terminal_failed = False
            for _topic, _key, envelope in captured.sent:
                if envelope.messageType == "RAG_INDEX_RETRY":
                    self.index_repository.reschedule_local_job(
                        job.id,
                        not_before=parse_not_before(envelope.notBefore),
                        error_message=str(envelope.payload.get("lastErrorMessage") or "本地 RAG 索引暂时失败"),
                    )
                    return False
                if envelope.messageType == "RAG_INDEX_RESULT":
                    promote_payload = self.state_writer.handle_index_result(envelope)
                    terminal_failed = str(envelope.payload.get("status") or "") == "FAILED"
                    if promote_payload:
                        self._run_local_promote(promote_payload, envelope)
                elif envelope.messageType == "RAG_INDEX_DLQ":
                    self.state_writer.handle_dlq(envelope)
                    terminal_failed = True
            return not terminal_failed
        except Exception:
            # 数据库/请求反序列化等失败由租约恢复；业务异常由 worker 内部结果/DLQ 收敛。
            return False

    def _run_local_promote(self, payload: dict[str, Any], source_envelope: KafkaEnvelope) -> None:
        """在 local 模式直接执行 promote，再将相同结果路径回写 PostgreSQL。"""
        request = PromoteRequestPayload.model_validate(payload)
        captured = CapturingProducer()

        def assert_active(candidate: PromoteRequestPayload) -> None:
            if not self.index_repository.is_active(candidate.materialId, candidate.jobId, candidate.requestVersion):
                from rag.kafka.worker import StalePromoteRequestError

                raise StalePromoteRequestError("本地提升请求已过期")

        promote_worker = RagKafkaPromoteWorker(store=self.store, producer=captured, active_checker=assert_active)
        promote_envelope = build_envelope(
            message_type="RAG_PROMOTE_REQUESTED",
            partition_key=request.canonicalDocumentId,
            idempotency_key=f"RAG_PROMOTE:{request.canonicalDocumentId}:{request.jobId}:v1",
            payload=request.model_dump(mode="json"),
            original_message_id=source_envelope.originalMessageId or source_envelope.messageId,
        )
        promote_worker.handle_envelope(promote_envelope)
        for _topic, _key, result in captured.sent:
            if result.messageType == "RAG_PROMOTE_RESULT":
                self.state_writer.handle_promote_result(result)


def main(argv: list[str] | None = None) -> None:
    """独立启动耐久任务 worker，通常由 `run.py` supervisor 拉起。"""
    load_runtime_config(parse_args(argv))
    if not read_bool("RAG_TASK_WORKER_ENABLED", True):
        print("RAG 耐久任务 worker 未启用，进程退出")
        return
    worker = RagDurableTaskWorker()
    stop_event = threading.Event()
    install_signal_handlers(stop_event)
    poll_seconds = positive_float("RAG_TASK_WORKER_POLL_SECONDS", 1.0)
    print("RAG 耐久任务 worker 已启动")
    while not stop_event.is_set():
        worker.run_once()
        stop_event.wait(poll_seconds)


def local_index_enabled() -> bool:
    """Kafka 打开时索引只由 Kafka worker 处理，关闭时由本地耐久 worker 接管。"""
    if read_bool("RAG_KAFKA_ENABLED", False):
        return False
    return read_bool("RAG_LOCAL_INDEX_WORKER_ENABLED", True)


def parse_not_before(value: object) -> datetime:
    if isinstance(value, datetime):
        target = value
    else:
        try:
            target = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return datetime.now(timezone.utc)
    return target if target.tzinfo else target.replace(tzinfo=timezone.utc)


def elapsed_ms(start: float) -> int:
    return max(0, round((time.perf_counter() - start) * 1000))


def read_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def positive_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def install_signal_handlers(stop_event: threading.Event) -> None:
    def stop(_signum: int, _frame: Any) -> None:
        stop_event.set()

    try:
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
    except ValueError:
        return


if __name__ == "__main__":
    main()
