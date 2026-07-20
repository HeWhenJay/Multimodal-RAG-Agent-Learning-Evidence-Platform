from __future__ import annotations

from app.workers.scheduler import FixedDelayScheduler, FixedDelayTask, build_default_scheduler, has_enabled_jobs
from app.workers.supervisor import build_cron_command


def test_fixed_delay_scheduler_runs_immediately_then_waits_for_task_completion_delay():
    """cron 首轮立即执行，后续严格遵循 fixed delay，不按壁钟重复触发。"""
    now = [0.0]
    calls = []
    scheduler = FixedDelayScheduler(
        [FixedDelayTask(name="outbox", callback=lambda: calls.append("run"), delay_seconds=2.0)],
        clock=lambda: now[0],
    )

    assert scheduler.run_due() == ["outbox"]
    assert calls == ["run"]
    now[0] = 1.99
    assert scheduler.run_due() == []
    now[0] = 2.0
    assert scheduler.run_due() == ["outbox"]
    assert calls == ["run", "run"]


def test_scheduler_registers_outbox_only_when_kafka_and_owner_are_enabled(monkeypatch):
    """防止迁移期开启 Python 发布器但 Kafka 未开，或与 Java 发布器无意重叠。"""
    monkeypatch.setenv("RAG_KAFKA_ENABLED", "false")
    monkeypatch.setenv("RAG_OUTBOX_PUBLISHER_ENABLED", "true")
    monkeypatch.setenv("RAG_STAGING_CLEANUP_ENABLED", "false")

    assert build_default_scheduler().tasks == []
    assert has_enabled_jobs() is False

    monkeypatch.setenv("RAG_KAFKA_ENABLED", "true")
    scheduler = build_default_scheduler(publisher_factory=lambda: type("Publisher", (), {"publish_due_events": lambda self: None})())

    assert [task.name for task in scheduler.tasks] == ["rag-outbox-publisher"]
    assert has_enabled_jobs() is True


def test_scheduler_registers_staging_cleanup_independently(monkeypatch):
    """staging 清理不依赖 Kafka，可由同一 cron 进程独立执行。"""
    monkeypatch.setenv("RAG_KAFKA_ENABLED", "false")
    monkeypatch.setenv("RAG_OUTBOX_PUBLISHER_ENABLED", "false")
    monkeypatch.setenv("RAG_STAGING_CLEANUP_ENABLED", "true")
    scheduler = build_default_scheduler(cleanup_factory=lambda: type("Cleanup", (), {"cleanup": lambda self: None})())

    assert [task.name for task in scheduler.tasks] == ["rag-staging-cleanup"]
    assert has_enabled_jobs() is True


def test_cron_supervisor_uses_current_python_and_passes_config_arguments():
    """run.py 监督 cron 时复用当前 Conda 解释器与配置文件。"""
    command = build_cron_command(["--skip-default-config", "--config", "config/local.yml"])

    assert command[1:3] == ["-m", "app.workers.scheduler"]
    assert command[3:] == ["--skip-default-config", "--config", "config/local.yml"]
