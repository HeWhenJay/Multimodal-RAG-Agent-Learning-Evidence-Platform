from __future__ import annotations

from contextlib import contextmanager
import json
from threading import Event
import time

from app.review.repository import DatabaseReviewTransaction, MaterialSourceRecord
from app.workers.review_task_worker import ReviewGenerationTaskWorker


def material(material_id: int) -> MaterialSourceRecord:
    """构造恢复 worker 使用的最小资料记录。"""
    return MaterialSourceRecord(
        id=material_id,
        title=f"资料 {material_id}",
        user_id="user-1",
        document_type="mp4",
        material_status="READY",
        document_summary=None,
        index_request_version=1,
        updated_at=None,
    )


class QueueTransaction:
    """记录 worker 的原子领取参数，并模拟数据库事务提交。"""

    def __init__(self, candidates: list[MaterialSourceRecord]) -> None:
        self.candidates = candidates
        self.claim_calls: list[tuple[int, int]] = []

    def claim_review_generation_candidates(self, *, batch_size: int, stale_seconds: int):
        self.claim_calls.append((batch_size, stale_seconds))
        candidates = self.candidates[:batch_size]
        self.candidates = self.candidates[batch_size:]
        return candidates


class QueueRepository:
    """为 worker 测试提供可替换的事务仓储。"""

    def __init__(self, transaction: QueueTransaction) -> None:
        self.transaction_value = transaction

    @contextmanager
    def transaction(self):
        yield self.transaction_value


class BlockingService:
    """让测试可以观察多个资料是否同时进入模型调用。"""

    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.calls: list[int] = []

    def generate_material(self, material_id: int, user_id: str) -> None:
        self.calls.append(material_id)
        self.started.set()
        self.release.wait(timeout=3)


def wait_for_call_count(service: BlockingService, count: int) -> None:
    """等待后台线程达到预期调用数，避免测试依赖固定 sleep。"""
    deadline = time.monotonic() + 3
    while len(service.calls) < count and time.monotonic() < deadline:
        time.sleep(0.01)


def test_worker_claims_queued_and_stale_tasks_with_configured_limits(monkeypatch) -> None:
    """worker 应把排队和超时任务交给数据库原子领取，并传递租约参数。"""
    monkeypatch.setenv("REVIEW_TASK_WORKER_BATCH_SIZE", "3")
    monkeypatch.setenv("REVIEW_TASK_WORKER_STALE_SECONDS", "900")
    transaction = QueueTransaction([material(1), material(2), material(3)])
    service = BlockingService()
    worker = ReviewGenerationTaskWorker(
        repository=QueueRepository(transaction),
        service=service,
        max_workers=2,
    )
    try:
        summary = worker.run_once()
        assert summary["claimed"] == 2
        assert summary["submitted"] == 2
        assert transaction.claim_calls == [(2, 900)]
        wait_for_call_count(service, 2)
        assert sorted(service.calls) == [1, 2]
    finally:
        service.release.set()
        worker.close(wait=True)


def test_worker_releases_slots_and_submits_next_batch_after_completion(monkeypatch) -> None:
    """前一批模型调用完成后必须释放并发槽，继续处理剩余资料。"""
    monkeypatch.setenv("REVIEW_TASK_WORKER_BATCH_SIZE", "16")
    transaction = QueueTransaction([material(1), material(2), material(3)])
    service = BlockingService()
    worker = ReviewGenerationTaskWorker(
        repository=QueueRepository(transaction),
        service=service,
        max_workers=2,
    )
    try:
        first = worker.run_once()
        assert first["submitted"] == 2
        wait_for_call_count(service, 2)
        service.release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and worker._active:
            worker._remove_finished()
            time.sleep(0.01)

        second = worker.run_once()
        assert second["claimed"] == 1
        assert second["submitted"] == 1
        wait_for_call_count(service, 3)
        assert sorted(service.calls) == [1, 2, 3]
    finally:
        service.release.set()
        worker.close(wait=True)


def test_claim_sql_marks_recovery_progress_after_row_lock() -> None:
    """数据库领取必须在同一事务内写入恢复进度，供前端显示而非永久卡住。"""
    class RecordingCursor:
        """返回一条可领取资料并记录 SELECT/UPDATE。"""

        def __init__(self) -> None:
            self.statements: list[str] = []
            self.params: list[tuple[object, ...]] = []
            self.rowcount = 1
            self._fetchall_calls = 0

        def execute(self, statement, params=()):
            self.statements.append(str(statement))
            self.params.append(tuple(params))

        def fetchall(self):
            self._fetchall_calls += 1
            if self._fetchall_calls == 1:
                return [
                    {
                        "id": 42,
                        "title": "Redis 缓存穿透",
                        "user_id": "user-1",
                        "document_type": "mp4",
                        "status": "READY",
                        "document_summary": None,
                        "index_request_version": 1,
                        "updated_at": None,
                        "review_generation_progress": {"stageCode": "review.queued"},
                    }
                ]
            return []

    cursor = RecordingCursor()
    transaction = DatabaseReviewTransaction(cursor, "learning_evidence")
    transaction._statement = lambda query: query  # type: ignore[method-assign]

    claimed = transaction.claim_review_generation_candidates(batch_size=4, stale_seconds=1200)

    assert [item.id for item in claimed] == [42]
    assert "FOR UPDATE OF rm SKIP LOCKED" in cursor.statements[0]
    update_params = cursor.params[1]
    progress = json.loads(str(update_params[1]))
    assert progress["stageCode"] == "review.recovery.claimed"
    assert progress["status"] == "RUNNING"
