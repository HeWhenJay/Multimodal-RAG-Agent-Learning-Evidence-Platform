import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

os.environ["RAG_EMBEDDING_PROVIDER"] = "hash"

from app.schemas.kafka import IndexRequestPayload, KafkaEnvelope
from app.kafka_worker import KafkaWorkerConnectionError, is_reconnectable_error, reconnect_max_seconds, run_consumer_forever
from rag.kafka.producer import KafkaJsonProducer, KafkaProgressThrottler, redacted_json
from rag.kafka.worker import PermanentSourceError, RagKafkaIndexWorker, RagKafkaPromoteWorker, RagKafkaRetryScheduler, RetryNotReady, StalePromoteRequestError, download_java_source
from rag.observability.progress import RagProgressReporter
from rag.retrievers.retrieval import InMemoryRagStore
from app.schemas.rag import QueryRequest


class FakeProducer:
    def __init__(self):
        self.sent = []

    def send(self, topic, key, envelope):
        self.sent.append((topic, key, envelope))


class FakeProgressProducer:
    def __init__(self):
        self.events = []

    def send_progress(self, **kwargs):
        self.events.append(kwargs)


def test_kafka_worker_reconnects_after_broker_connection_error(monkeypatch):
    calls = []
    sleeps = []

    def fake_consumer_loop(_handlers):
        calls.append(True)
        if len(calls) == 1:
            raise KafkaWorkerConnectionError("Broker 不可达")

    monkeypatch.setenv("RAG_KAFKA_RECONNECT_INITIAL_SECONDS", "0.1")
    monkeypatch.setenv("RAG_KAFKA_RECONNECT_MAX_SECONDS", "0.2")
    monkeypatch.setattr("app.kafka_worker.run_consumer_loop", fake_consumer_loop)
    monkeypatch.setattr("app.kafka_worker.time.sleep", lambda seconds: sleeps.append(seconds))

    run_consumer_forever({})

    assert len(calls) == 2
    assert sleeps == [0.1]


def test_kafka_worker_error_classifier_ignores_unknown_error_code():
    class Error:
        def code(self):
            return None

    class KafkaErrorType:
        _ALL_BROKERS_DOWN = -187
        _TRANSPORT = -195
        _TIMED_OUT = -185

    assert is_reconnectable_error(Error(), KafkaErrorType) is False
    assert reconnect_max_seconds(2.0) >= 2.0


def envelope(message_type, payload):
    return KafkaEnvelope(
        messageId="msg-1",
        originalMessageId="msg-1",
        messageType=message_type,
        eventTime="2026-07-04T00:00:00Z",
        producer="unit-test",
        partitionKey=payload["canonicalDocumentId"],
        idempotencyKey=f"unit:{message_type}",
        payload=payload,
    )


def test_index_request_schema_accepts_inline_text():
    payload = base_index_payload()

    parsed = IndexRequestPayload.model_validate(payload)

    assert parsed.sourceRef.type == "INLINE_TEXT"
    assert parsed.stagingVisibilityScope == "staging"


def test_progress_delivery_mode_memory_skips_http_and_persist(monkeypatch):
    called = []
    monkeypatch.setattr("rag.observability.progress.post_log_event", lambda payload: called.append(payload) or True)
    reporter = RagProgressReporter(document_id="query", persist=False, on_emit=lambda event: None)

    reporter.emit("query.expand", "查询任务内存进度")

    assert called == []


def test_kafka_progress_delivery_mode_uses_only_kafka(monkeypatch):
    callback_payloads = []
    monkeypatch.setattr("rag.observability.progress.post_log_event", lambda payload: callback_payloads.append(payload) or True)
    progress_producer = FakeProgressProducer()
    reporter = RagProgressReporter(
        document_id="material-1__job-job-1",
        user_id="7",
        delivery_mode="kafka",
        kafka_producer=progress_producer,
        kafka_context={
            "jobId": "job-1",
            "materialId": 1,
            "canonicalDocumentId": "material-1",
            "stagingDocumentId": "material-1__job-job-1",
            "requestVersion": 1,
        },
    )

    reporter.emit("index.request", "Kafka 进度")

    assert callback_payloads == []
    assert progress_producer.events


def test_inline_text_indexes_staging_and_private_query_does_not_see_it(monkeypatch):
    monkeypatch.setenv("RAG_QUERY_EXPANSION_PROVIDER", "local")
    store = InMemoryRagStore()
    fake_producer = FakeProducer()
    worker = RagKafkaIndexWorker(store=store, producer=fake_producer, progress_producer=FakeProgressProducer())

    worker.handle_envelope(envelope("RAG_INDEX_REQUESTED", base_index_payload()))
    response = store.query(
        QueryRequest(
            question="Kafka RAG 索引",
            topK=3,
            metadataFilter={"userId": "7", "visibilityScope": "private"},
        )
    )

    assert "material-1__job-job-1" in store.documents
    assert store.documents["material-1__job-job-1"]["visibilityScope"] == "staging"
    assert response.answerStatus == "REFUSED"
    assert any(sent[2].messageType == "RAG_INDEX_RESULT" for sent in fake_producer.sent)


def test_promote_is_idempotent_and_private_query_can_see_canonical(monkeypatch):
    monkeypatch.setenv("RAG_QUERY_EXPANSION_PROVIDER", "local")
    store = InMemoryRagStore()
    index_worker = RagKafkaIndexWorker(store=store, producer=FakeProducer(), progress_producer=FakeProgressProducer())
    index_worker.handle_envelope(envelope("RAG_INDEX_REQUESTED", base_index_payload()))
    fake_producer = FakeProducer()
    promote_worker = RagKafkaPromoteWorker(store=store, producer=fake_producer, active_checker=lambda payload: None)
    promote_payload = {
        "jobId": "job-1",
        "materialId": 1,
        "canonicalDocumentId": "material-1",
        "stagingDocumentId": "material-1__job-job-1",
        "requestVersion": 1,
        "chunkCount": len([chunk for chunk in store.chunks.values() if chunk.document_id == "material-1__job-job-1"]),
    }

    first = promote_worker.handle_envelope(envelope("RAG_PROMOTE_REQUESTED", promote_payload))
    second = promote_worker.handle_envelope(envelope("RAG_PROMOTE_REQUESTED", promote_payload))
    response = store.query(
        QueryRequest(
            question="Kafka RAG 索引",
            topK=3,
            metadataFilter={"userId": "7", "visibilityScope": "private"},
        )
    )

    assert first["status"] == "SUCCEEDED"
    assert second["alreadyPromoted"] is True
    assert response.answerStatus == "ANSWERED"
    assert all(item.documentId == "material-1" for item in response.evidences)


def test_stale_promote_does_not_overwrite_new_canonical(monkeypatch):
    monkeypatch.setenv("RAG_QUERY_EXPANSION_PROVIDER", "local")
    store = InMemoryRagStore()
    index_worker = RagKafkaIndexWorker(store=store, producer=FakeProducer(), progress_producer=FakeProgressProducer())
    old_payload = base_index_payload()
    new_payload = base_index_payload()
    new_payload.update(
        {
            "jobId": "job-2",
            "stagingDocumentId": "material-1__job-job-2",
            "requestVersion": 2,
            "text": "## 新版本索引\n新 canonical 应该保留，不允许旧 promote 覆盖。",
        }
    )
    index_worker.handle_envelope(envelope("RAG_INDEX_REQUESTED", old_payload))
    index_worker.handle_envelope(envelope("RAG_INDEX_REQUESTED", new_payload))
    new_promote_payload = {
        "jobId": "job-2",
        "materialId": 1,
        "canonicalDocumentId": "material-1",
        "stagingDocumentId": "material-1__job-job-2",
        "requestVersion": 2,
        "chunkCount": len([chunk for chunk in store.chunks.values() if chunk.document_id == "material-1__job-job-2"]),
    }
    RagKafkaPromoteWorker(store=store, producer=FakeProducer(), active_checker=lambda payload: None).handle_envelope(
        envelope("RAG_PROMOTE_REQUESTED", new_promote_payload)
    )
    old_promote_payload = {
        "jobId": "job-1",
        "materialId": 1,
        "canonicalDocumentId": "material-1",
        "stagingDocumentId": "material-1__job-job-1",
        "requestVersion": 1,
        "chunkCount": len([chunk for chunk in store.chunks.values() if chunk.document_id == "material-1__job-job-1"]),
    }
    fake_producer = FakeProducer()
    stale_worker = RagKafkaPromoteWorker(
        store=store,
        producer=fake_producer,
        active_checker=lambda payload: (_ for _ in ()).throw(StalePromoteRequestError("旧 promote 已过期")),
    )

    result = stale_worker.handle_envelope(envelope("RAG_PROMOTE_REQUESTED", old_promote_payload))
    response = store.query(QueryRequest(question="新 canonical", topK=3, metadataFilter={"userId": "7", "visibilityScope": "private"}))

    assert result["status"] == "FAILED"
    assert result["errorCode"] == "RAG_PROMOTE_STALE"
    assert response.answerStatus == "ANSWERED"
    assert "新版本索引" in response.evidences[0].snippet


def test_dlq_redaction_removes_sensitive_text():
    serialized = redacted_json(
        {
            "jobId": "job-1",
            "text": "简历全文和资料正文",
            "apiKey": "secret",
            "accessKeySecret": "oss-secret",
            "sourceRef": {
                "publicUrl": "https://oss.example.com/private?signature=secret",
                "objectKey": "private/path.md",
            },
            "filename": "note.md",
        }
    )

    assert "简历全文" not in serialized
    assert "secret" not in serialized
    assert "private/path.md" not in serialized
    assert "note.md" in serialized


def test_transient_failure_sends_retry_without_throwing(monkeypatch):
    monkeypatch.setenv("RAG_KAFKA_RETRY_1M_SECONDS", "0")
    fake_producer = FakeProducer()

    class FailingWorker(RagKafkaIndexWorker):
        """用于模拟临时解析失败的 worker。"""

        def _index_to_staging(self, payload):
            raise RuntimeError("临时 embedding 超时")

    worker = FailingWorker(store=InMemoryRagStore(), producer=fake_producer, progress_producer=FakeProgressProducer())

    result = worker.handle_envelope(envelope("RAG_INDEX_REQUESTED", base_index_payload()))

    assert result["status"] == "RETRY_SCHEDULED"
    assert fake_producer.sent[0][0] == "rag.material.index.retry.1m.v1"
    assert fake_producer.sent[0][2].messageType == "RAG_INDEX_RETRY"
    assert fake_producer.sent[0][2].idempotencyKey == "RAG_INDEX:material-1:job-1:v1"


def test_permanent_failure_sends_failed_result_and_dlq_without_throwing():
    fake_producer = FakeProducer()

    class PermanentFailingWorker(RagKafkaIndexWorker):
        """用于模拟 Java Source API 永久失败的 worker。"""

        def _index_to_staging(self, payload):
            raise PermanentSourceError("Java Source API 返回 404: 文件不存在")

    worker = PermanentFailingWorker(store=InMemoryRagStore(), producer=fake_producer, progress_producer=FakeProgressProducer())

    result = worker.handle_envelope(envelope("RAG_INDEX_REQUESTED", base_index_payload()))

    assert result["status"] == "FAILED"
    assert [sent[2].messageType for sent in fake_producer.sent] == ["RAG_INDEX_RESULT", "RAG_INDEX_DLQ"]
    assert fake_producer.sent[-1][2].payload["request"].find("Kafka RAG 索引") == -1


def test_retry_scheduler_waits_until_not_before():
    scheduler = RagKafkaRetryScheduler(producer=FakeProducer())
    retry = envelope("RAG_INDEX_RETRY", base_index_payload())
    retry.notBefore = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()

    with pytest.raises(RetryNotReady) as exc_info:
        scheduler.handle_envelope(retry)

    assert exc_info.value.delay_seconds > 0


def test_retry_scheduler_resends_request_with_incremented_attempt():
    fake_producer = FakeProducer()
    scheduler = RagKafkaRetryScheduler(producer=fake_producer)
    retry = envelope("RAG_INDEX_RETRY", base_index_payload())
    retry.attempt = 1
    retry.notBefore = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()

    result = scheduler.handle_envelope(retry)

    assert result["status"] == "RESENT"
    assert fake_producer.sent[0][0] == "rag.material.index.request.v1"
    assert fake_producer.sent[0][2].attempt == 2
    assert fake_producer.sent[0][2].messageType == "RAG_INDEX_REQUESTED"


def test_kafka_json_producer_raises_when_flush_leaves_messages():
    class SlowProducer:
        """模拟 flush 超时仍有消息未投递。"""

        def produce(self, *args, **kwargs):
            return None

        def flush(self, timeout):
            return 1

    producer = KafkaJsonProducer(producer=SlowProducer())

    with pytest.raises(RuntimeError, match="未投递"):
        producer.send("topic", "key", envelope("RAG_INDEX_REQUESTED", base_index_payload()))


def test_download_java_source_streams_to_temp_file(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {"content-type": "text/markdown", "content-disposition": 'attachment; filename="note.md"'}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield b"Kafka "
            yield b"stream"

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def stream(self, method, url, headers):
            assert method == "GET"
            assert url == "http://java/api/internal/rag/source"
            return FakeResponse()

    monkeypatch.setattr("rag.kafka.worker.httpx.Client", FakeClient)
    source_ref = SimpleNamespace(
        javaBaseUrl="http://java",
        downloadPath="/api/internal/rag/source",
        filename=None,
        contentType=None,
        sourcePath="oss://bucket/note.md",
    )

    downloaded = download_java_source(source_ref, "job-1", 1)

    try:
        assert downloaded.path.read_bytes() == b"Kafka stream"
        assert downloaded.filename == "note.md"
        assert downloaded.content_type == "text/markdown"
    finally:
        downloaded.cleanup()
    assert not downloaded.path.exists()


def test_kafka_progress_throttler_keeps_first_last_and_completed():
    throttler = KafkaProgressThrottler(chunk_interval=10, min_seconds=999)

    assert throttler.should_emit(progress_event(1, 100)) is True
    assert throttler.should_emit(progress_event(2, 100)) is False
    assert throttler.should_emit(progress_event(10, 100)) is True
    assert throttler.should_emit(progress_event(100, 100)) is True


def progress_event(current, total):
    from app.schemas.rag import ProgressEvent

    return ProgressEvent(
        stageCode="embedding.chunk",
        stageLabel="生成 embedding",
        message="chunk",
        currentChunk=current,
        totalChunks=total,
    )


def base_index_payload():
    return {
        "jobId": "job-1",
        "operation": "INDEX_TEXT",
        "materialId": 1,
        "canonicalDocumentId": "material-1",
        "stagingDocumentId": "material-1__job-job-1",
        "userId": "7",
        "title": "Kafka RAG 笔记",
        "documentType": "markdown",
        "source": "manual",
        "visibilityScope": "private",
        "stagingVisibilityScope": "staging",
        "highPrecision": False,
        "requestVersion": 1,
        "sourceRef": {"type": "INLINE_TEXT", "parser": "java-manual-text"},
        "text": "## Kafka RAG 索引\nKafka 可以让上传接口快速返回，staging promote 后再进入 private 查询。",
    }
