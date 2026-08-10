from __future__ import annotations

from datetime import datetime, timezone
import threading
import time

from app.workers.outbox_publisher import RagOutboxEvent, RagOutboxPublisher, backoff_seconds, publish_timeout_seconds


class FakeOutboxRepository:
    """隔离 Kafka 与 PostgreSQL 的发布器行为测试替身。"""

    def __init__(self, events: list[RagOutboxEvent]) -> None:
        self.events = events
        self.claim_calls = []
        self.published = []
        self.failed = []

    def claim_due_events(self, **kwargs):
        self.claim_calls.append(kwargs)
        return list(self.events)

    def mark_published(self, event_id):
        self.published.append(event_id)

    def mark_failed(self, event_id, error_message, next_attempt_at):
        self.failed.append((event_id, error_message, next_attempt_at))


class RecordingProducer:
    """记录原始 Kafka payload，验证 Outbox 不重编码消息。"""

    def __init__(self) -> None:
        self.sent = []

    def send_serialized(self, topic, key, payload_json, *, flush_seconds):
        self.sent.append((topic, key, payload_json, flush_seconds))


class FailingProducer(RecordingProducer):
    """模拟 Kafka 确认阶段失败。"""

    def send_serialized(self, topic, key, payload_json, *, flush_seconds):
        raise RuntimeError("KafkaError{SECRET_BODY_SHOULD_NOT_LEAK}")


class ParallelProbeProducer(RecordingProducer):
    """用同步屏障确认不同消息键确实进入独立 I/O worker。"""

    def __init__(self) -> None:
        super().__init__()
        self.barrier = threading.Barrier(2)
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def send_serialized(self, topic, key, payload_json, *, flush_seconds):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.barrier.wait(timeout=2)
            with self.lock:
                self.sent.append((topic, key, payload_json, flush_seconds))
        finally:
            with self.lock:
                self.active -= 1


class OrderProbeProducer(RecordingProducer):
    """延迟首条消息，确认相同 topic/key 不会被后续事件越过。"""

    def send_serialized(self, topic, key, payload_json, *, flush_seconds):
        if payload_json == "event-1":
            time.sleep(0.05)
        self.sent.append((topic, key, payload_json, flush_seconds))


def sample_event(
    *,
    event_id: int = 1,
    attempt: int = 0,
    message_key: str = "material-1",
    payload_json: str = '{"schemaVersion":"1.0","payload":{"text":"原始正文"}}',
) -> RagOutboxEvent:
    return RagOutboxEvent(
        id=event_id,
        topic="rag.material.index.request.v1",
        message_key=message_key,
        payload_json=payload_json,
        attempt=attempt,
    )


def test_outbox_publisher_marks_event_published_after_raw_kafka_delivery(monkeypatch):
    """Kafka 确认成功后才标记 PUBLISHED，并保留原 payload 与 key。"""
    monkeypatch.setenv("RAG_OUTBOX_BATCH_SIZE", "20")
    monkeypatch.setenv("RAG_OUTBOX_LEASE_SECONDS", "45")
    monkeypatch.setenv("RAG_KAFKA_PUBLISH_TIMEOUT_MS", "2500")
    repository = FakeOutboxRepository([sample_event()])
    producer = RecordingProducer()
    publisher = RagOutboxPublisher(repository=repository, producer=producer, publisher_id="publisher-1")

    result = publisher.publish_due_events()

    assert result.claimed == 1
    assert result.published == 1
    assert result.failed == 0
    assert repository.claim_calls[0]["batch_size"] == 20
    assert repository.claim_calls[0]["lease_seconds"] == 45
    assert repository.claim_calls[0]["publisher_id"] == "publisher-1"
    assert repository.published == [1]
    assert repository.failed == []
    assert producer.sent == [
        (
            "rag.material.index.request.v1",
            "material-1",
            '{"schemaVersion":"1.0","payload":{"text":"原始正文"}}',
            2.5,
        )
    ]


def test_outbox_publisher_releases_failed_event_with_redacted_summary(monkeypatch):
    """Kafka 失败后释放租约并按已领取前 attempt 计算退避，错误不泄漏正文。"""
    monkeypatch.setenv("RAG_OUTBOX_MAX_ATTEMPTS", "3")
    repository = FakeOutboxRepository([sample_event(event_id=7, attempt=4)])
    publisher = RagOutboxPublisher(repository=repository, producer=FailingProducer(), publisher_id="publisher-1")

    before = datetime.now(timezone.utc)
    result = publisher.publish_due_events()

    assert result.claimed == 1
    assert result.published == 0
    assert result.failed == 1
    assert repository.published == []
    event_id, summary, next_attempt_at = repository.failed[0]
    assert event_id == 7
    assert summary == "Kafka 发布失败：RuntimeError"
    assert "SECRET_BODY_SHOULD_NOT_LEAK" not in summary
    assert next_attempt_at >= before
    assert backoff_seconds(4) == 8


def test_outbox_publisher_publishes_different_keys_concurrently(monkeypatch):
    """不同 Kafka key 属于独立 I/O，单轮内应并行等待投递确认。"""
    monkeypatch.setenv("RAG_OUTBOX_PUBLISH_CONCURRENCY", "16")
    repository = FakeOutboxRepository(
        [
            sample_event(event_id=1, message_key="material-1", payload_json="event-1"),
            sample_event(event_id=2, message_key="material-2", payload_json="event-2"),
        ]
    )
    producer = ParallelProbeProducer()
    publisher = RagOutboxPublisher(repository=repository, producer=producer, publisher_id="publisher-1")

    result = publisher.publish_due_events()

    assert result == type(result)(claimed=2, published=2, failed=0)
    assert producer.max_active == 2
    assert sorted(repository.published) == [1, 2]


def test_outbox_publisher_keeps_same_key_in_event_id_order(monkeypatch):
    """同一 topic/key 即使启用 16 路线程池，也必须按事件 ID 串行发布。"""
    monkeypatch.setenv("RAG_OUTBOX_PUBLISH_CONCURRENCY", "16")
    repository = FakeOutboxRepository(
        [
            sample_event(event_id=3, payload_json="event-3"),
            sample_event(event_id=1, payload_json="event-1"),
            sample_event(event_id=2, payload_json="event-2"),
        ]
    )
    producer = OrderProbeProducer()
    publisher = RagOutboxPublisher(repository=repository, producer=producer, publisher_id="publisher-1")

    result = publisher.publish_due_events()

    assert result == type(result)(claimed=3, published=3, failed=0)
    assert [payload for _, _, payload, _ in producer.sent] == ["event-1", "event-2", "event-3"]
    assert repository.published == [1, 2, 3]


def test_outbox_backoff_is_capped_but_never_disables_retries(monkeypatch):
    """max-attempts 仅限制指数，长期失败事件仍可继续被 cron 重试。"""
    monkeypatch.setenv("RAG_OUTBOX_MAX_ATTEMPTS", "8")

    assert backoff_seconds(None) == 2
    assert backoff_seconds(8) == 256
    assert backoff_seconds(100) == 256


def test_outbox_publish_timeout_has_java_compatible_lower_bound(monkeypatch):
    """非法或过小 timeout 仍至少等待 100ms，避免立即判定 Kafka 失败。"""
    monkeypatch.setenv("RAG_KAFKA_PUBLISH_TIMEOUT_MS", "0")

    assert publish_timeout_seconds() == 3.0

    monkeypatch.setenv("RAG_KAFKA_PUBLISH_TIMEOUT_MS", "50")
    assert publish_timeout_seconds() == 0.1
