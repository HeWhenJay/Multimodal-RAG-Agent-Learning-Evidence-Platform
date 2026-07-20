"""RAG PostgreSQL 耐久任务和 Python Kafka 状态写回测试。"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from app.repositories.rag_job import RagIndexJobRecord
from app.repositories.rag_task import InMemoryRagQueryTaskRepository, build_query_task_repository
from app.schemas.kafka import KafkaEnvelope
from app.schemas.rag import QueryResponse
from app.schemas.rag_control import RagQueryPublicRequest
from app.services.rag_control_service import RagControlService
from app.workers.rag_kafka_state import RagKafkaStateWriter
from app.workers.rag_task_worker import RagDurableTaskWorker


class EmptyIndexRepository:
    """查询 worker 测试不需要索引任务。"""

    def claim_local_jobs(self, **_kwargs):
        return []


class QueryStore:
    """模拟可产生阶段事件的检索存储。"""

    def query(self, request, progress_reporter):
        progress_reporter.emit("query.expand", "正在生成多查询", current_step=1, total_steps=3)
        progress_reporter.emit("query.completed", "查询完成", status="COMPLETED", current_step=3, total_steps=3)
        return QueryResponse(
            answer=f"回答：{request.question}",
            answerStatus="REFUSED",
            refusalReason="NO_EVIDENCE",
            expandedQueries=[request.question],
            evidences=[],
        )


class StateRepository:
    """记录 state writer 路由，不连接 PostgreSQL。"""

    def __init__(self):
        self.calls = []

    def consume_progress(self, envelope):
        self.calls.append(("progress", envelope.messageId))
        return True

    def consume_index_result(self, envelope):
        self.calls.append(("index", envelope.messageId))
        return {"jobId": "job-1"}

    def consume_promote_result(self, envelope):
        self.calls.append(("promote", envelope.messageId))
        return True

    def consume_dlq(self, envelope):
        self.calls.append(("dlq", envelope.messageId))
        return True


def event(message_type: str) -> KafkaEnvelope:
    """构造不会包含资料正文的最小 Kafka envelope。"""
    return KafkaEnvelope(
        messageId=f"message-{message_type}",
        originalMessageId=None,
        messageType=message_type,
        eventTime="2026-07-21T00:00:00Z",
        producer="unit-test",
        partitionKey="material-1",
        idempotencyKey=f"unit:{message_type}",
        payload={"jobId": "job-1", "materialId": 1, "requestVersion": 1},
    )


def test_public_query_task_only_enqueues_durable_snapshot() -> None:
    """公开 service 不应向旧 ThreadPoolExecutor 提交查询任务。"""
    tasks = InMemoryRagQueryTaskRepository()
    service = RagControlService(
        repository=object(),
        store=object(),
        parser_router=object(),
        object_storage=object(),
        task_repository=tasks,
        executor=object(),
    )

    created = service.start_query_task(RagQueryPublicRequest(question="我的资料有哪些技能？"), "42")
    persisted = tasks.get(created.taskId, "42")

    assert created.status == "RUNNING"
    assert persisted is not None
    assert json.loads(persisted.request_json)["metadataFilter"] == {"userId": "42", "visibilityScope": "private"}


def test_durable_query_worker_persists_progress_and_result(monkeypatch) -> None:
    """worker 重启边界之外的任务也能通过仓储读取完成结果。"""
    monkeypatch.setenv("RAG_KAFKA_ENABLED", "false")
    tasks = InMemoryRagQueryTaskRepository()
    task = tasks.enqueue(
        task_id="query-1",
        user_id="42",
        question="资料中的 Kafka 重点是什么？",
        top_k=5,
        request_payload={
            "question": "资料中的 Kafka 重点是什么？",
            "topK": 5,
            "candidateMultiplier": 4,
            "metadataFilter": {"userId": "42", "visibilityScope": "private"},
        },
    )
    worker = RagDurableTaskWorker(
        query_repository=tasks,
        index_repository=EmptyIndexRepository(),
        state_writer=RagKafkaStateWriter(repository=StateRepository()),
        store=QueryStore(),
        worker_id="test-worker",
    )

    result = worker.run_once()
    persisted = tasks.get(task.task_id, task.user_id)

    assert result["queryCompleted"] == 1
    assert persisted is not None and persisted.status == "COMPLETED"
    assert persisted.answer == "回答：资料中的 Kafka 重点是什么？"
    assert len(json.loads(persisted.progress_events_json)) == 2


def test_expired_query_task_cannot_be_reactivated_by_late_worker_result() -> None:
    """TTL 过期后的迟到结果不得把 `EXPIRED` 改回完成状态。"""
    tasks = InMemoryRagQueryTaskRepository()
    task = tasks.enqueue(
        task_id="expired-1",
        user_id="42",
        question="过期任务",
        top_k=5,
        request_payload={"question": "过期任务", "topK": 5, "candidateMultiplier": 4, "metadataFilter": {}},
    )
    tasks._tasks[(task.task_id, task.user_id)] = replace(task, expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    tasks.expire_due()
    tasks.complete(
        task.task_id,
        task.user_id,
        QueryResponse(answer="迟到回答", answerStatus="REFUSED", expandedQueries=[], evidences=[]).model_dump(mode="json"),
        1,
    )

    persisted = tasks.get(task.task_id, task.user_id)

    assert persisted is not None and persisted.status == "EXPIRED"


def test_query_task_repository_allows_explicit_memory_fallback(monkeypatch) -> None:
    """无数据库测试环境可显式启用内存仓储，生产默认仍应使用 PostgreSQL。"""
    for name in ("RAG_CONTROL_DATABASE_URL", "RAG_DATABASE_URL", "DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAG_TASK_ALLOW_MEMORY_FALLBACK", "true")

    repository = build_query_task_repository()

    assert isinstance(repository, InMemoryRagQueryTaskRepository)


def test_python_kafka_state_writer_routes_all_terminal_event_types() -> None:
    """Kafka worker 的 progress/result/promote/DLQ 均进入 Python 状态仓储。"""
    repository = StateRepository()
    writer = RagKafkaStateWriter(repository=repository)

    assert writer.handle_progress(event("RAG_INDEX_PROGRESS")) is True
    assert writer.handle_index_result(event("RAG_INDEX_RESULT")) == {"jobId": "job-1"}
    assert writer.handle_promote_result(event("RAG_PROMOTE_RESULT")) is True
    assert writer.handle_dlq(event("RAG_INDEX_DLQ")) is True
    assert [name for name, _message_id in repository.calls] == ["progress", "index", "promote", "dlq"]


def test_local_index_worker_consumes_durable_job_without_kafka(monkeypatch) -> None:
    """Kafka 关闭时 LOCAL job 仍由 Python worker 执行 staging/promote 链路。"""
    monkeypatch.setenv("RAG_EMBEDDING_PROVIDER", "hash")
    monkeypatch.setenv("RAG_QUERY_EXPANSION_PROVIDER", "local")
    monkeypatch.setenv("RAG_KAFKA_ENABLED", "false")

    class IndexRepository(EmptyIndexRepository):
        def __init__(self):
            self.claimed = False
            self.active_checks = []

        def claim_local_jobs(self, **_kwargs):
            if self.claimed:
                return []
            self.claimed = True
            return [
                RagIndexJobRecord(
                    id="job-1",
                    material_id=1,
                    user_id="42",
                    operation="INDEX_TEXT",
                    request_version=1,
                    attempt=1,
                    delivery_mode="LOCAL",
                    request_json=json.dumps(
                        {
                            "jobId": "job-1",
                            "operation": "INDEX_TEXT",
                            "materialId": 1,
                            "canonicalDocumentId": "material-1",
                            "stagingDocumentId": "material-1__job-job-1",
                            "userId": "42",
                            "title": "Kafka 笔记",
                            "documentType": "markdown",
                            "source": "manual",
                            "requestVersion": 1,
                            "sourceRef": {"type": "INLINE_TEXT", "parser": "python-manual-text"},
                            "text": "## Kafka\n索引任务需要可恢复。",
                        },
                        ensure_ascii=False,
                    ),
                )
            ]

        def is_active(self, material_id, job_id, request_version):
            self.active_checks.append((material_id, job_id, request_version))
            return True

        def reschedule_local_job(self, *_args, **_kwargs):
            raise AssertionError("索引成功时不应重试")

    class IndexStateRepository(StateRepository):
        def consume_index_result(self, envelope):
            self.calls.append(("index", envelope.messageId))
            return {
                "jobId": "job-1",
                "materialId": 1,
                "canonicalDocumentId": "material-1",
                "stagingDocumentId": "material-1__job-job-1",
                "requestVersion": 1,
                "chunkCount": None,
            }

    from rag.retrievers.retrieval import InMemoryRagStore

    index_repository = IndexRepository()
    state_repository = IndexStateRepository()
    worker = RagDurableTaskWorker(
        query_repository=InMemoryRagQueryTaskRepository(),
        index_repository=index_repository,
        state_writer=RagKafkaStateWriter(repository=state_repository),
        store=InMemoryRagStore(),
        worker_id="test-worker",
    )

    result = worker.run_once()

    assert result["indexCompleted"] == 1
    assert [name for name, _message_id in state_repository.calls if name in {"index", "promote"}] == ["index", "promote"]
    assert index_repository.active_checks == [(1, "job-1", 1)]
