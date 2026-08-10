import os
import sys
import threading
import time
import types
from datetime import datetime, timedelta, timezone

import pytest

os.environ["RAG_EMBEDDING_PROVIDER"] = "hash"

from app.repositories.rag_job import IndexExecutionClaim
from app.schemas.kafka import IndexRequestPayload, KafkaEnvelope, StorageSourceRef
from app.schemas.rag import DocumentBlock, ParseQuality
from app.services.remote_video_import import RemoteVideoTaskTimeoutError
from app.storage.object_storage import OpenedStorageObject
from app.workers.kafka_worker import (
    KafkaMessageDispatcher,
    KafkaWorkerConnectionError,
    kafka_auto_offset_reset,
    is_connection_exception,
    is_reconnectable_error,
    message_hash,
    positive_milliseconds,
    publish_consumer_dlq,
    reconnect_max_seconds,
    run_consumer_loop,
    run_consumer_forever,
)
from rag.kafka.producer import KafkaJsonProducer, KafkaProgressProducer, KafkaProgressThrottler, redacted_json
from rag.kafka.worker import PermanentSourceError, RagKafkaIndexWorker, RagKafkaPromoteWorker, RagKafkaRetryScheduler, RetryNotReady, StalePromoteRequestError, open_storage_source
from rag.observability.progress import RagProgressReporter
from rag.retrievers.retrieval import InMemoryRagStore
from rag.core.models import ParsedBlockDocument
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
    monkeypatch.setattr("app.workers.kafka_worker.run_consumer_loop", fake_consumer_loop)
    monkeypatch.setattr("app.workers.kafka_worker.time.sleep", lambda seconds: sleeps.append(seconds))

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


def test_kafka_long_task_poll_interval_has_safe_default(monkeypatch):
    """长视频索引应允许覆盖 Kafka 默认 5 分钟 poll 间隔。"""
    monkeypatch.setenv("RAG_KAFKA_MAX_POLL_INTERVAL_MS", "1800000")
    assert positive_milliseconds("RAG_KAFKA_MAX_POLL_INTERVAL_MS", 3_600_000) == 1_800_000
    monkeypatch.setenv("RAG_KAFKA_MAX_POLL_INTERVAL_MS", "invalid")
    assert positive_milliseconds("RAG_KAFKA_MAX_POLL_INTERVAL_MS", 21_600_000) == 21_600_000


def test_kafka_auto_offset_reset_accepts_only_supported_values(monkeypatch):
    """测试隔离消费组可从启动后新增消息开始，非法值仍保持安全默认。"""
    monkeypatch.setenv("RAG_KAFKA_AUTO_OFFSET_RESET", "latest")
    assert kafka_auto_offset_reset() == "latest"
    monkeypatch.setenv("RAG_KAFKA_AUTO_OFFSET_RESET", "invalid")
    assert kafka_auto_offset_reset() == "earliest"


def test_unhandled_consumer_message_is_redacted_into_dlq():
    producer = FakeProducer()

    class Message:
        def topic(self):
            return "rag.material.index.request.v1"

        def partition(self):
            return 2

        def offset(self):
            return 17

        def value(self):
            return b'{"text":"SECRET_BODY_SHOULD_NOT_LEAK"}'

    with pytest.raises(Exception) as exc_info:
        KafkaEnvelope.model_validate_json(Message().value())
    publish_consumer_dlq(producer, Message(), exc_info.value, None)

    topic, key, event = producer.sent[0]
    assert topic == "rag.material.index.dlq.v1"
    assert key == "rag.material.index.request.v1-2"
    assert event.messageType == "RAG_KAFKA_CONSUMER_DLQ"
    assert event.payload["offset"] == 17
    assert "SECRET_BODY_SHOULD_NOT_LEAK" not in str(event.payload)
    assert event.payload["errorMessage"] == "Kafka envelope 格式或字段校验失败"
    assert event.payload["messageHash"] == message_hash(Message().value())


def test_kafka_connection_classifier_recognizes_producer_timeout():
    assert is_connection_exception(RuntimeError("KafkaError{code=_MSG_TIMED_OUT,str=Local: Message timed out}")) is True


def test_kafka_poll_continues_while_handler_runs_and_commits_after_completion(monkeypatch):
    """长任务在线程池执行，poll 必须持续且只能在 handler 完成后提交 offset。"""
    stop_event = threading.Event()
    handler_started = threading.Event()
    handler_done = threading.Event()
    release_handler = threading.Event()

    class FakeMessage:
        def topic(self):
            return "rag.material.index.request.v1"

        def partition(self):
            return 0

        def offset(self):
            return 3

        def value(self):
            return envelope("RAG_INDEX_REQUESTED", base_index_payload()).model_dump_json().encode("utf-8")

        def error(self):
            return None

    class FakeTopicPartition:
        def __init__(self, topic, partition, offset=None):
            self.topic = topic
            self.partition = partition
            self.offset = offset

    class FakeKafkaError:
        _PARTITION_EOF = -191

    class FakeKafkaException(Exception):
        pass

    class FakeConsumer:
        instance = None

        def __init__(self, _config):
            type(self).instance = self
            self.poll_count = 0
            self.polled_while_handler_running = False
            self.committed_after_done = False
            self.paused = []
            self.resumed = []

        def subscribe(self, _topics):
            return None

        def poll(self, _timeout):
            self.poll_count += 1
            if self.poll_count == 1:
                return FakeMessage()
            if handler_started.is_set() and not handler_done.is_set():
                self.polled_while_handler_running = True
                release_handler.set()
            time.sleep(0.001)
            return None

        def pause(self, partitions):
            self.paused.extend(partitions)

        def resume(self, partitions):
            self.resumed.extend(partitions)

        def commit(self, *, message, asynchronous):
            assert message.offset() == 3
            assert asynchronous is False
            self.committed_after_done = handler_done.is_set()
            stop_event.set()

        def close(self):
            return None

    fake_module = types.SimpleNamespace(
        Consumer=FakeConsumer,
        KafkaError=FakeKafkaError,
        KafkaException=FakeKafkaException,
        TopicPartition=FakeTopicPartition,
    )
    monkeypatch.setitem(sys.modules, "confluent_kafka", fake_module)
    monkeypatch.setattr("app.workers.kafka_worker.KafkaJsonProducer", FakeProducer)

    handler_thread_ids = []

    def long_handler(_event):
        handler_thread_ids.append(threading.get_ident())
        handler_started.set()
        release_handler.wait(0.5)
        handler_done.set()

    poll_thread_id = threading.get_ident()
    run_consumer_loop({"rag.material.index.request.v1": long_handler}, stop_event=stop_event)

    consumer = FakeConsumer.instance
    assert consumer is not None
    assert consumer.polled_while_handler_running is True
    assert consumer.committed_after_done is True
    assert consumer.paused == []
    assert consumer.resumed == []
    assert handler_thread_ids and handler_thread_ids[0] != poll_thread_id


def test_kafka_dispatcher_reserves_capacity_for_control_messages() -> None:
    """索引线程全部繁忙时，progress/result 等控制消息仍应立即执行。"""
    release_index = threading.Event()
    control_started = threading.Event()

    class Message:
        def __init__(self, topic: str, partition: int, offset: int) -> None:
            self._topic = topic
            self._partition = partition
            self._offset = offset

        def topic(self):
            return self._topic

        def partition(self):
            return self._partition

        def offset(self):
            return self._offset

        def value(self):
            return envelope("RAG_INDEX_REQUESTED", base_index_payload()).model_dump_json().encode("utf-8")

    class Consumer:
        def pause(self, _partitions):
            return None

        def resume(self, _partitions):
            return None

        def commit(self, **_kwargs):
            return None

    class TopicPartition:
        def __init__(self, topic, partition):
            self.topic = topic
            self.partition = partition

    def index_handler(_event):
        release_index.wait(1.0)

    def control_handler(_event):
        control_started.set()

    request_topic = "rag.material.index.request.v1"
    progress_topic = "rag.material.index.progress.v1"
    dispatcher = KafkaMessageDispatcher(
        consumer=Consumer(),
        handlers={request_topic: index_handler, progress_topic: control_handler},
        topic_partition_type=TopicPartition,
        dead_letter_producer=FakeProducer(),
        worker_count=2,
        control_worker_count=1,
        retry_max_delay_seconds=1.0,
    )
    try:
        dispatcher.accept(Message(request_topic, 0, 1))
        dispatcher.accept(Message(request_topic, 1, 1))
        dispatcher.accept(Message(progress_topic, 0, 1))

        assert control_started.wait(0.5)
    finally:
        release_index.set()
        while dispatcher.has_active_handlers:
            dispatcher.advance()
            time.sleep(0.001)
        dispatcher.close()


def test_kafka_dispatcher_runs_distinct_keys_concurrently_on_one_partition() -> None:
    """单分区不应把不同资料串行化；offset 仍只能提交到连续完成水位。"""
    first_started = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    commits: list[int] = []

    class Message:
        def __init__(self, offset: int, canonical: str) -> None:
            self._offset = offset
            self._canonical = canonical

        def topic(self):
            return "rag.material.index.request.v1"

        def partition(self):
            return 0

        def offset(self):
            return self._offset

        def key(self):
            return self._canonical.encode("utf-8")

        def value(self):
            payload = base_index_payload()
            payload["canonicalDocumentId"] = self._canonical
            payload["stagingDocumentId"] = f"{self._canonical}__job-job-1"
            return envelope("RAG_INDEX_REQUESTED", payload).model_dump_json().encode("utf-8")

    class Consumer:
        def pause(self, _partitions):
            return None

        def resume(self, _partitions):
            return None

        def commit(self, *, message, asynchronous):
            assert asynchronous is False
            commits.append(message.offset())

    class TopicPartition:
        def __init__(self, topic, partition):
            self.topic = topic
            self.partition = partition

    def handler(event):
        if event.partitionKey == "material-a":
            first_started.set()
            release_first.wait(1.0)
            return
        second_started.set()

    dispatcher = KafkaMessageDispatcher(
        consumer=Consumer(),
        handlers={"rag.material.index.request.v1": handler},
        topic_partition_type=TopicPartition,
        dead_letter_producer=FakeProducer(),
        worker_count=2,
        control_worker_count=1,
        retry_max_delay_seconds=1.0,
    )
    try:
        dispatcher.accept(Message(10, "material-a"))
        dispatcher.accept(Message(11, "material-b"))
        assert first_started.wait(0.5)
        assert second_started.wait(0.5)

        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and len(dispatcher.in_flight) == 2:
            dispatcher.advance()
            time.sleep(0.001)
        assert commits == []

        release_first.set()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and dispatcher.has_active_handlers:
            dispatcher.advance()
            time.sleep(0.001)
        dispatcher.advance()
        assert commits == [11]
    finally:
        release_first.set()
        while dispatcher.has_active_handlers:
            dispatcher.advance()
            time.sleep(0.001)
        dispatcher.close()


def test_index_worker_rejects_second_execution_while_database_lease_is_busy():
    """同一 job 的重复 Kafka 投递不得绕过数据库执行权再次构建 staging 索引。"""
    class ClaimRepository:
        def __init__(self):
            self.claim_count = 0

        def claim_index_execution(self, *_args, **_kwargs):
            self.claim_count += 1
            if self.claim_count == 1:
                return IndexExecutionClaim("ACQUIRED")
            return IndexExecutionClaim("BUSY", 10.0)

        def owns_index_execution(self, *_args):
            return True

        def renew_index_execution(self, *_args):
            return True

    repository = ClaimRepository()
    worker = RagKafkaIndexWorker(
        store=InMemoryRagStore(),
        producer=FakeProducer(),
        progress_producer=FakeProgressProducer(),
        job_repository=repository,
        execution_heartbeat_seconds=999,
    )
    source = envelope("RAG_INDEX_REQUESTED", base_index_payload())

    first = worker.handle_envelope(source)
    with pytest.raises(RetryNotReady, match="已有执行者"):
        worker.handle_envelope(source)

    assert first.status == "READY"
    assert repository.claim_count == 2


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


def test_progress_delivery_mode_memory_skips_persistence():
    """内存任务进度不依赖已删除的内部 HTTP callback。"""
    reporter = RagProgressReporter(document_id="query", persist=False, on_emit=lambda event: None)

    reporter.emit("query.expand", "查询任务内存进度")


def test_kafka_progress_delivery_mode_uses_only_kafka():
    """Kafka 模式只发送 Kafka progress，不回调内部 HTTP。"""
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

    assert progress_producer.events


def test_kafka_progress_envelope_preserves_execution_owner() -> None:
    """进度消息必须携带执行令牌，迟到事件才能被数据库终态围栏拒绝。"""
    producer = FakeProducer()
    progress_producer = KafkaProgressProducer(producer)

    progress_producer.send_progress(
        event=progress_event(1, 1),
        document_id="material-1__job-job-1",
        material_id=1,
        user_id="7",
        parser="unit-parser",
        extra_context={
            "jobId": "job-1",
            "materialId": 1,
            "canonicalDocumentId": "material-1",
            "stagingDocumentId": "material-1__job-job-1",
            "requestVersion": 1,
            "executionOwner": "worker-token-1",
        },
    )

    assert producer.sent[0][2].payload["executionOwner"] == "worker-token-1"


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


def test_index_result_omits_full_progress_history_from_kafka_payload():
    """长视频的进度历史应走 progress topic，不能撑大终态消息。"""
    from app.schemas.rag import IndexResponse, ProgressEvent

    fake_producer = FakeProducer()
    worker = RagKafkaIndexWorker(
        store=InMemoryRagStore(),
        producer=fake_producer,
        progress_producer=FakeProgressProducer(),
    )
    payload = IndexRequestPayload.model_validate(base_index_payload())
    response = IndexResponse(
        documentId=payload.stagingDocumentId,
        title=payload.title,
        status="READY",
        chunkCount=1702,
        parser="video-parallel-worker-pool+sidecar-subtitle",
        documentSummary="长视频已完成索引",
        progressEvents=[
            ProgressEvent(
                stageCode="vector.upsert.chunk",
                stageLabel="写入向量数据库",
                message="逐块进度",
                currentChunk=index,
                totalChunks=1702,
            )
            for index in range(1, 6200)
        ],
    )

    worker._send_result(envelope("RAG_INDEX_REQUESTED", payload.model_dump(mode="json")), payload, response)

    result_event = fake_producer.sent[-1][2]
    assert result_event.messageType == "RAG_INDEX_RESULT"
    assert "progressEvents" not in result_event.payload
    assert len(result_event.model_dump_json().encode("utf-8")) < 1_048_588


def test_kafka_oss_video_keeps_temp_path_out_of_index(monkeypatch, tmp_path):
    """Kafka worker 应读取 OSS 临时文件，但只把公开来源交给视频解析器和索引。"""

    class RecordingVideoParser:
        def __init__(self) -> None:
            self.call: dict = {}

        def parse_video_source(self, **kwargs) -> ParsedBlockDocument:
            self.call = kwargs
            block = DocumentBlock(
                documentId=kwargs["document_id"],
                blockId=f"{kwargs['document_id']}-subtitle-1",
                fileType="mp4",
                blockType="text",
                startTime="00:00:01",
                endTime="00:00:02",
                sectionTitle="00:00:01 - 00:00:02",
                contentText="OSS 视频字幕证据",
                parseEngine="unit-video-parser",
                sourceTitle=kwargs["title"],
                sourcePath=kwargs["source_reference"],
                metadata={"mediaType": "video", "evidenceChannel": "subtitle"},
            )
            return ParsedBlockDocument(
                blocks=[block],
                parser="unit-video-parser",
                status="READY",
                parse_quality=ParseQuality(score=1.0, nativeTextChars=len(block.contentText)),
            )

    class TempOssStorage:
        def __init__(self, path, public_url) -> None:
            self.path = path
            self.public_url = public_url

        def download_to_temp(self, **_kwargs) -> OpenedStorageObject:
            return OpenedStorageObject(
                path=self.path,
                filename="course.mp4",
                content_type="video/mp4",
                source_path=self.public_url,
                _temporary=True,
            )

    temp_video = tmp_path / "rag-oss-private.mp4"
    temp_video.write_bytes(b"video")
    public_source = "https://cdn.example.com/learning-evidence/7/mp4/course.mp4"
    parser = RecordingVideoParser()
    store = InMemoryRagStore()
    worker = RagKafkaIndexWorker(
        store=store,
        parser_router=parser,
        producer=FakeProducer(),
        progress_producer=FakeProgressProducer(),
        object_storage=TempOssStorage(temp_video, public_source),
    )
    monkeypatch.setenv("RAG_VIDEO_PARALLEL_SEGMENTS_ENABLED", "false")
    payload = IndexRequestPayload.model_validate({
        **base_index_payload(),
        "operation": "INDEX_UPLOAD",
        "documentType": "mp4",
        "source": "upload",
        "sourceRef": {
            "type": "STORAGE",
            "filename": "course.mp4",
            "contentType": "video/mp4",
            "storageType": "oss",
            "objectKey": "learning-evidence/7/mp4/course.mp4",
        },
        "text": None,
    })

    worker._index_to_staging(payload)

    assert parser.call["source_path"] == str(temp_video)
    assert parser.call["source_reference"] == public_source
    assert not temp_video.exists()
    assert {chunk.metadata.get("sourcePath") for chunk in store.chunks.values()} == {public_source}


def test_remote_bilibili_video_is_temporary_and_keeps_public_source(monkeypatch, tmp_path):
    """远程视频解析后必须清理临时文件，并只把规范化页面 URL 写入 evidence。"""
    from app.services.remote_video_import import OpenedRemoteVideo

    class RecordingRemoteVideoParser:
        """记录远程视频解析参数，避免读取真实媒体。"""

        def __init__(self) -> None:
            self.call: dict = {}

        def parse_video_source(self, **kwargs) -> ParsedBlockDocument:
            self.call = kwargs
            block = DocumentBlock(
                documentId=kwargs["document_id"],
                blockId=f"{kwargs['document_id']}-subtitle-1",
                fileType="mp4",
                blockType="text",
                startTime="00:00:01",
                endTime="00:00:02",
                sectionTitle="00:00:01 - 00:00:02",
                contentText="Bilibili 视频字幕证据",
                parseEngine="unit-video-parser",
                sourceTitle=kwargs["title"],
                sourcePath=kwargs["source_reference"],
                metadata={"mediaType": "video", "evidenceChannel": "subtitle"},
            )
            return ParsedBlockDocument(
                blocks=[block],
                parser="unit-video-parser",
                status="READY",
                parse_quality=ParseQuality(
                    score=1.0,
                    nativeTextChars=len(block.contentText),
                    messages=["FFmpeg 失败: C:\\Temp\\rag-remote-video-secret\\source.mp4"],
                ),
                warnings=["解析失败: C:\\Temp\\rag-remote-video-secret\\source.mp4"],
            )

    temp_video = tmp_path / "source.mp4"
    temp_video.write_bytes(b"video")
    public_source = "https://www.bilibili.com/video/BV1xx411c7mD"
    cleaned: list[bool] = []

    class FakeTempDirectory:
        def cleanup(self) -> None:
            cleaned.append(True)
            temp_video.unlink(missing_ok=True)

    class RemoteJobRepository:
        def __init__(self) -> None:
            self.updated: dict | None = None

        def update_remote_material_source(self, material_id, job_id, request_version, **kwargs):
            self.updated = {"materialId": material_id, "jobId": job_id, "requestVersion": request_version, **kwargs}
            return True

    opened = OpenedRemoteVideo(
        path=temp_video,
        filename="source.mp4",
        title="Kafka 高可用课程",
        content_type="video/mp4",
        source_url=public_source,
        duration_seconds=30,
        _temp_directory=FakeTempDirectory(),
    )
    monkeypatch.setattr("rag.kafka.worker.download_bilibili_video", lambda *_args, **_kwargs: opened)
    monkeypatch.setenv("RAG_VIDEO_PARALLEL_SEGMENTS_ENABLED", "false")
    parser = RecordingRemoteVideoParser()
    store = InMemoryRagStore()
    repository = RemoteJobRepository()
    worker = RagKafkaIndexWorker(
        store=store,
        parser_router=parser,
        producer=FakeProducer(),
        progress_producer=FakeProgressProducer(),
        job_repository=repository,
    )
    payload = IndexRequestPayload.model_validate({
        **base_index_payload(),
        "operation": "INDEX_REMOTE_VIDEO",
        "documentType": "mp4",
        "source": "bilibili",
        "sourceRef": {
            "type": "REMOTE_VIDEO",
            "platform": "bilibili",
            "url": public_source,
            "videoId": "BV1xx411c7mD",
        },
    })

    result = worker._index_to_staging(payload)

    assert cleaned == [True]
    assert not temp_video.exists()
    assert repository.updated and repository.updated["source_url"] == public_source
    assert parser.call["source_reference"] == public_source
    assert {chunk.metadata.get("sourcePath") for chunk in store.chunks.values()} == {public_source}
    assert "rag-remote-video-" not in result.model_dump_json()
    assert "远程视频解析异常，临时文件信息已隐藏" in result.parseQuality.messages


def test_remote_bilibili_video_is_cleaned_when_metadata_write_fails(monkeypatch, tmp_path):
    """任务元数据写回异常时也必须清理已下载的临时目录。"""
    from app.services.remote_video_import import OpenedRemoteVideo

    temp_video = tmp_path / "source.mp4"
    temp_video.write_bytes(b"video")
    cleaned: list[bool] = []

    class FakeTempDirectory:
        def cleanup(self) -> None:
            cleaned.append(True)
            temp_video.unlink(missing_ok=True)

    class FailingJobRepository:
        def update_remote_material_source(self, *_args, **_kwargs):
            raise RuntimeError("模拟数据库写入失败")

    opened = OpenedRemoteVideo(
        path=temp_video,
        filename="source.mp4",
        title="Kafka 高可用课程",
        content_type="video/mp4",
        source_url="https://www.bilibili.com/video/BV1xx411c7mD",
        duration_seconds=30,
        _temp_directory=FakeTempDirectory(),
    )
    monkeypatch.setattr("rag.kafka.worker.download_bilibili_video", lambda *_args, **_kwargs: opened)
    worker = RagKafkaIndexWorker(
        store=InMemoryRagStore(),
        parser_router=object(),
        producer=FakeProducer(),
        progress_producer=FakeProgressProducer(),
        job_repository=FailingJobRepository(),
    )
    payload = IndexRequestPayload.model_validate({
        **base_index_payload(),
        "operation": "INDEX_REMOTE_VIDEO",
        "documentType": "mp4",
        "source": "bilibili",
        "sourceRef": {
            "type": "REMOTE_VIDEO",
            "platform": "bilibili",
            "url": opened.source_url,
            "videoId": "BV1xx411c7mD",
        },
    })

    with pytest.raises(RuntimeError, match="数据库写入失败"):
        worker._index_to_staging(payload)

    assert cleaned == [True]
    assert not temp_video.exists()


def test_remote_douyin_video_indexes_mcp_transcript_without_downloading_media():
    """抖音任务直接索引 MCP 转写，并保留作品页和视频 evidence 元数据。"""
    from app.services.douyin_mcp_client import DouyinTranscript

    public_source = "https://www.douyin.com/video/741234567890"

    class FakeDouyinClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def transcribe_video(self, url: str, **_kwargs) -> DouyinTranscript:
            self.calls.append(url)
            return DouyinTranscript(
                title="RAG-Fusion 抖音课程",
                aweme_id="741234567890",
                source_url=url,
                text="1\n00:00:01,000 --> 00:00:03,000\n先进行 BM25 与向量召回。",
            )

    class RecordingTextParser:
        def __init__(self) -> None:
            self.call: dict = {}

        def parse_text(self, **kwargs) -> ParsedBlockDocument:
            self.call = kwargs
            block = DocumentBlock(
                documentId=kwargs["document_id"],
                blockId=f"{kwargs['document_id']}-subtitle-1",
                fileType="srt",
                blockType="text",
                startTime="00:00:01",
                endTime="00:00:03",
                sectionTitle="00:00:01 - 00:00:03",
                contentText="先进行 BM25 与向量召回。",
                parseEngine=kwargs["parser"],
                sourceTitle=kwargs["title"],
                sourcePath=kwargs["source_path"],
            )
            return ParsedBlockDocument(
                blocks=[block],
                parser=kwargs["parser"],
                status="READY",
                parse_quality=ParseQuality(score=1.0, nativeTextChars=len(block.contentText)),
            )

    class RemoteJobRepository:
        def __init__(self) -> None:
            self.updated: dict | None = None

        def update_remote_material_source(self, material_id, job_id, request_version, **kwargs):
            self.updated = {"materialId": material_id, "jobId": job_id, "requestVersion": request_version, **kwargs}
            return True

    douyin_client = FakeDouyinClient()
    parser = RecordingTextParser()
    repository = RemoteJobRepository()
    store = InMemoryRagStore()
    worker = RagKafkaIndexWorker(
        store=store,
        parser_router=parser,
        producer=FakeProducer(),
        progress_producer=FakeProgressProducer(),
        job_repository=repository,
        douyin_client=douyin_client,
    )
    payload = IndexRequestPayload.model_validate({
        **base_index_payload(),
        "operation": "INDEX_REMOTE_VIDEO",
        "documentType": "mp4",
        "source": "douyin",
        "sourceRef": {
            "type": "REMOTE_VIDEO",
            "platform": "douyin",
            "url": public_source,
            "videoId": "741234567890",
        },
    })

    result = worker._index_to_staging(payload)

    assert result.status == "READY"
    assert douyin_client.calls == [public_source]
    assert parser.call["document_type"] == "srt"
    assert parser.call["source_path"] == public_source
    assert repository.updated and repository.updated["filename"] == "741234567890.srt"
    assert repository.updated["platform"] == "douyin"
    chunk = next(iter(store.chunks.values()))
    assert chunk.metadata["sourcePath"] == public_source
    assert chunk.metadata["sourcePlatform"] == "douyin"
    assert chunk.metadata["awemeId"] == "741234567890"
    assert chunk.metadata["evidenceChannel"] == "subtitle"


def test_remote_local_execution_stops_before_index_write_after_lease_loss(monkeypatch, tmp_path):
    """本地远程视频任务续租失败后不得写 staging 索引或发布完成结果。"""
    from app.services.remote_video_import import OpenedRemoteVideo

    renew_attempted = threading.Event()
    cleaned: list[bool] = []
    temp_video = tmp_path / "lease-lost.mp4"
    temp_video.write_bytes(b"video")

    class FakeTempDirectory:
        def cleanup(self):
            cleaned.append(True)
            temp_video.unlink(missing_ok=True)

    class LeaseRepository:
        def owns_index_execution(self, *_args):
            return True

        def renew_index_execution(self, *_args):
            renew_attempted.set()
            return False

        def update_remote_material_source(self, *_args, **_kwargs):
            return True

    class WaitingParser:
        def parse_video_source(self, **kwargs):
            assert renew_attempted.wait(1.0)
            block = DocumentBlock(
                documentId=kwargs["document_id"],
                blockId=f"{kwargs['document_id']}-subtitle-1",
                fileType="mp4",
                blockType="text",
                startTime="00:00:01",
                endTime="00:00:02",
                sectionTitle="00:00:01 - 00:00:02",
                contentText="租约失效后不能写入的内容",
                parseEngine="unit-video-parser",
                sourceTitle=kwargs["title"],
                sourcePath=kwargs["source_reference"],
                metadata={"mediaType": "video", "evidenceChannel": "subtitle"},
            )
            return ParsedBlockDocument(
                blocks=[block],
                parser="unit-video-parser",
                status="READY",
                parse_quality=ParseQuality(score=1.0, nativeTextChars=len(block.contentText)),
            )

    class RejectingStore:
        def index_blocks(self, **_kwargs):
            raise AssertionError("失去租约后不应进入 staging 写入")

    opened = OpenedRemoteVideo(
        path=temp_video,
        filename="lease-lost.mp4",
        title="Kafka 课程",
        content_type="video/mp4",
        source_url="https://www.bilibili.com/video/BV1xx411c7mD",
        duration_seconds=30,
        _temp_directory=FakeTempDirectory(),
    )
    monkeypatch.setattr("rag.kafka.worker.download_bilibili_video", lambda *_args, **_kwargs: opened)
    monkeypatch.setenv("RAG_VIDEO_PARALLEL_SEGMENTS_ENABLED", "false")
    producer = FakeProducer()
    worker = RagKafkaIndexWorker(
        store=RejectingStore(),
        parser_router=WaitingParser(),
        producer=producer,
        progress_producer=FakeProgressProducer(),
        job_repository=LeaseRepository(),
        execution_owner_id="local-worker-1",
        execution_lease_seconds=10,
        execution_heartbeat_seconds=0.01,
    )
    payload = {
        **base_index_payload(),
        "operation": "INDEX_REMOTE_VIDEO",
        "documentType": "mp4",
        "source": "bilibili",
        "sourceRef": {
            "type": "REMOTE_VIDEO",
            "platform": "bilibili",
            "url": opened.source_url,
            "videoId": "BV1xx411c7mD",
        },
    }

    with pytest.raises(RetryNotReady, match="租约"):
        worker.handle_envelope(envelope("RAG_INDEX_REQUESTED", payload))

    assert renew_attempted.is_set()
    assert producer.sent == []
    assert cleaned == [True]
    assert not temp_video.exists()


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
                "url": "https://www.bilibili.com/video/BV1xx411c7mD?token=secret",
            },
            "filename": "note.md",
        }
    )

    assert "简历全文" not in serialized
    assert "secret" not in serialized
    assert "private/path.md" not in serialized
    assert "token=secret" not in serialized
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


def test_remote_video_task_timeout_is_sent_to_durable_retry_topic(monkeypatch):
    """远程资源总墙钟超时必须保留受控中文摘要并进入耐久重试。"""
    monkeypatch.setenv("RAG_KAFKA_RETRY_1M_SECONDS", "0")
    fake_producer = FakeProducer()

    class TimeoutWorker(RagKafkaIndexWorker):
        def _index_to_staging(self, payload, *, execution=None):
            del payload, execution
            raise RemoteVideoTaskTimeoutError("Bilibili 视频处理超过任务总时限")

    worker = TimeoutWorker(
        store=InMemoryRagStore(),
        producer=fake_producer,
        progress_producer=FakeProgressProducer(),
    )

    result = worker.handle_envelope(envelope("RAG_INDEX_REQUESTED", base_index_payload()))

    assert result["status"] == "RETRY_SCHEDULED"
    retry = fake_producer.sent[0][2]
    assert retry.messageType == "RAG_INDEX_RETRY"
    assert retry.payload["lastErrorMessage"] == "Bilibili 视频处理超过任务总时限"


def test_permanent_failure_sends_failed_result_and_dlq_without_throwing():
    fake_producer = FakeProducer()

    class PermanentFailingWorker(RagKafkaIndexWorker):
        """用于模拟受控原文件永久不可读取的 worker。"""

        def _index_to_staging(self, payload):
            raise PermanentSourceError("受控原文件不存在")

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


def test_kafka_json_producer_sends_serialized_payload_without_reencoding():
    """Outbox 已保存的 envelope 必须原样写入 Kafka，不能二次 JSON 编码。"""
    values = []

    class RecordingProducer:
        def produce(self, _topic, *, key, value, callback):
            values.append((key, value))
            callback(None, None)

        def flush(self, _timeout):
            return 0

    payload_json = '{"schemaVersion":"1.0","payload":{"title":"保持原文"}}'
    producer = KafkaJsonProducer(producer=RecordingProducer())

    producer.send_serialized("outbox-topic", "material-1", payload_json, flush_seconds=0.1)

    assert values == [("material-1", payload_json)]


def test_open_storage_source_reads_controlled_local_file_without_java_http(monkeypatch, tmp_path):
    root = tmp_path / "uploads"
    source = root / "7" / "markdown" / "note.md"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"Kafka stream")
    monkeypatch.setenv("EVIDENCE_UPLOAD_ROOT", str(root))
    source_ref = StorageSourceRef(
        storageType="local",
        sourcePath=str(source),
        filename="note.md",
        contentType="text/markdown",
    )

    opened = open_storage_source(source_ref)

    opened.cleanup()
    assert opened.path.read_bytes() == b"Kafka stream"
    assert opened.filename == "note.md"
    assert opened.content_type == "text/markdown"


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
