import os

from run import (
    agent_worker_enabled,
    apply_runtime_mode_overrides,
    build_env_defaults,
    cron_enabled,
    kafka_enabled,
    main,
    parse_args,
    rag_task_worker_enabled,
    worker_config_args,
)
from app.workers.supervisor import build_worker_command


def test_fusion_and_local_rerank_config_env_mapping_is_effective():
    """校验融合与本地重排配置能从 YAML 映射为运行环境变量。"""
    env_defaults = build_env_defaults(
        {
            "rag": {
                "fusion": {
                    "strategy": "rrf",
                    "rrf-k": 42,
                    "bm25-weight": 1.1,
                    "vector-weight": 0.9,
                    "original-query-weight": 1.3,
                    "expanded-query-weight": 0.8,
                    "score-blend": 0.2,
                    "diagnostic-limit": 12,
                },
                "local-rerank": {
                    "fusion-weight": 0.3,
                    "lexical-weight": 0.4,
                    "title-weight": 0.2,
                    "rank-weight": 0.1,
                },
            }
        }
    )

    assert env_defaults["RAG_FUSION_STRATEGY"] == "rrf"
    assert env_defaults["RAG_FUSION_RRF_K"] == "42"
    assert env_defaults["RAG_FUSION_BM25_WEIGHT"] == "1.1"
    assert env_defaults["RAG_FUSION_VECTOR_WEIGHT"] == "0.9"
    assert env_defaults["RAG_FUSION_ORIGINAL_QUERY_WEIGHT"] == "1.3"
    assert env_defaults["RAG_FUSION_EXPANDED_QUERY_WEIGHT"] == "0.8"
    assert env_defaults["RAG_FUSION_SCORE_BLEND"] == "0.2"
    assert env_defaults["RAG_FUSION_DIAGNOSTIC_LIMIT"] == "12"
    assert env_defaults["RAG_LOCAL_RERANK_FUSION_WEIGHT"] == "0.3"
    assert env_defaults["RAG_LOCAL_RERANK_LEXICAL_WEIGHT"] == "0.4"
    assert env_defaults["RAG_LOCAL_RERANK_TITLE_WEIGHT"] == "0.2"
    assert env_defaults["RAG_LOCAL_RERANK_RANK_WEIGHT"] == "0.1"


def test_answer_guard_config_env_mapping_is_effective():
    """校验回答准入阈值能从 YAML 映射为运行环境变量。"""
    env_defaults = build_env_defaults(
        {
            "rag": {
                "answer-guard": {
                    "min-answerable-score": 0.5,
                    "min-top-score-dashscope": 0.55,
                    "min-top-score-local": 0.3,
                    "min-keyword-coverage": 0.1,
                    "min-supporting-evidence-count": 2,
                    "strict-mode": True,
                }
            }
        }
    )

    assert env_defaults["RAG_ANSWER_MIN_ANSWERABLE_SCORE"] == "0.5"
    assert env_defaults["RAG_ANSWER_MIN_TOP_SCORE_DASHSCOPE"] == "0.55"
    assert env_defaults["RAG_ANSWER_MIN_TOP_SCORE_LOCAL"] == "0.3"
    assert env_defaults["RAG_ANSWER_MIN_KEYWORD_COVERAGE"] == "0.1"
    assert env_defaults["RAG_ANSWER_MIN_SUPPORTING_EVIDENCE_COUNT"] == "2"
    assert env_defaults["RAG_ANSWER_STRICT_MODE"] == "true"


def test_video_v6_config_env_mapping_is_effective():
    """校验 V6 视频 OCR 新配置能从 YAML 映射为运行环境变量。"""
    env_defaults = build_env_defaults(
        {
            "video": {
                "frame-scan-mode": "full",
                "frame-target-candidates": 360,
                "frame-max-candidates": 720,
                "frame-min-interval-seconds": 30,
                "frame-visual-dedup-enabled": True,
                "frame-visual-hash-algorithm": "dhash",
                "frame-visual-hash-max-distance": 4,
                "frame-visual-ambiguous-margin": 2,
                "frame-max-representatives-per-visual-group": 1,
                "frame-visual-verify-interval-seconds": 900,
                "frame-visual-stay-verify-seconds": 600,
                "frame-visual-revisit-verify-seconds": 1800,
                "frame-visual-verification-ratio": 0.25,
                "frame-max-verifications-per-visual-group": 2,
            }
        }
    )

    assert env_defaults["RAG_VIDEO_FRAME_SCAN_MODE"] == "full"
    assert env_defaults["RAG_VIDEO_FRAME_TARGET_CANDIDATES"] == "360"
    assert env_defaults["RAG_VIDEO_FRAME_MAX_CANDIDATES"] == "720"
    assert env_defaults["RAG_VIDEO_FRAME_MIN_INTERVAL_SECONDS"] == "30"
    assert env_defaults["RAG_VIDEO_FRAME_VISUAL_DEDUP_ENABLED"] == "true"
    assert env_defaults["RAG_VIDEO_FRAME_VISUAL_HASH_ALGORITHM"] == "dhash"
    assert env_defaults["RAG_VIDEO_FRAME_VISUAL_HASH_MAX_DISTANCE"] == "4"
    assert env_defaults["RAG_VIDEO_FRAME_VISUAL_AMBIGUOUS_MARGIN"] == "2"
    assert env_defaults["RAG_VIDEO_FRAME_MAX_REPRESENTATIVES_PER_VISUAL_GROUP"] == "1"
    assert env_defaults["RAG_VIDEO_FRAME_VISUAL_VERIFY_INTERVAL_SECONDS"] == "900"
    assert env_defaults["RAG_VIDEO_FRAME_VISUAL_STAY_VERIFY_SECONDS"] == "600"
    assert env_defaults["RAG_VIDEO_FRAME_VISUAL_REVISIT_VERIFY_SECONDS"] == "1800"
    assert env_defaults["RAG_VIDEO_FRAME_VISUAL_VERIFICATION_RATIO"] == "0.25"
    assert env_defaults["RAG_VIDEO_FRAME_MAX_VERIFICATIONS_PER_VISUAL_GROUP"] == "2"


def test_worker_cron_config_env_mapping_is_effective():
    """校验 Python worker cron 配置可由 YAML 统一映射到运行环境变量。"""
    env_defaults = build_env_defaults(
        {
            "workers": {
                "cron": {"enabled": True, "poll-interval-seconds": 0.25},
                "outbox": {
                    "enabled": True,
                    "batch-size": 20,
                    "lease-seconds": 45,
                    "publish-fixed-delay-ms": 750,
                    "max-attempts": 6,
                    "publish-timeout-ms": 2500,
                },
                "staging-cleanup": {"enabled": False, "fixed-delay-seconds": 600},
                "rag-task": {"enabled": True, "poll-interval-seconds": 0.2},
            }
        }
    )

    assert env_defaults["AI_CRON_ENABLED"] == "true"
    assert env_defaults["AI_CRON_POLL_INTERVAL_SECONDS"] == "0.25"
    assert env_defaults["RAG_OUTBOX_PUBLISHER_ENABLED"] == "true"
    assert env_defaults["RAG_OUTBOX_BATCH_SIZE"] == "20"
    assert env_defaults["RAG_OUTBOX_LEASE_SECONDS"] == "45"
    assert env_defaults["RAG_OUTBOX_PUBLISH_FIXED_DELAY_MS"] == "750"
    assert env_defaults["RAG_OUTBOX_MAX_ATTEMPTS"] == "6"
    assert env_defaults["RAG_KAFKA_PUBLISH_TIMEOUT_MS"] == "2500"
    assert env_defaults["RAG_STAGING_CLEANUP_ENABLED"] == "false"
    assert env_defaults["RAG_STAGING_CLEANUP_FIXED_DELAY_SECONDS"] == "600"
    assert env_defaults["RAG_TASK_WORKER_ENABLED"] == "true"
    assert env_defaults["RAG_TASK_WORKER_POLL_SECONDS"] == "0.2"


def test_cron_cli_override_and_worker_config_forwarding(monkeypatch):
    """命令行可覆盖 cron 开关，子进程只继承配置文件参数而不递归启动。"""
    monkeypatch.setenv("AI_CRON_ENABLED", "false")
    args = parse_args(["--with-cron", "--config", "config/test.yml", "--skip-default-config"])

    assert cron_enabled(args) is True
    assert worker_config_args(args) == ["--skip-default-config", "--config", "config/test.yml"]


def test_bootstrap_database_flag_is_explicit() -> None:
    """空库初始化只能由明确启动参数触发。"""
    assert parse_args([]).bootstrap_database is False
    assert parse_args(["--bootstrap-database"]).bootstrap_database is True


def test_kafka_and_agent_worker_switches_follow_cli_then_configuration(monkeypatch):
    """Kafka 与 Agent worker 开关不会误随 cron 配置启动。"""
    monkeypatch.setenv("RAG_KAFKA_ENABLED", "true")
    monkeypatch.setenv("AI_KAFKA_WORKER_ENABLED", "false")
    monkeypatch.setenv("AI_AGENT_WORKER_ENABLED", "false")
    args = parse_args([])

    assert kafka_enabled(args) is False
    assert agent_worker_enabled(args) is False

    assert kafka_enabled(parse_args(["--with-kafka"])) is True
    assert kafka_enabled(parse_args(["--without-kafka"])) is False
    assert agent_worker_enabled(parse_args(["--with-agent-worker"])) is True
    assert agent_worker_enabled(parse_args(["--without-agent-worker"])) is False


def test_kafka_cli_switch_also_overrides_rag_delivery_mode(monkeypatch):
    """避免关闭 Kafka worker 后仍创建无人消费的 KAFKA 索引任务。"""
    monkeypatch.setenv("RAG_KAFKA_ENABLED", "true")
    monkeypatch.setenv("AI_KAFKA_WORKER_ENABLED", "true")

    apply_runtime_mode_overrides(parse_args(["--without-kafka"]))

    assert os.getenv("RAG_KAFKA_ENABLED") == "false"
    assert os.getenv("AI_KAFKA_WORKER_ENABLED") == "false"

    apply_runtime_mode_overrides(parse_args(["--with-kafka"]))

    assert os.getenv("RAG_KAFKA_ENABLED") == "true"
    assert os.getenv("AI_KAFKA_WORKER_ENABLED") == "true"


def test_rag_task_worker_switches_follow_cli_then_configuration(monkeypatch):
    """RAG 耐久任务 worker 仅由自身开关或明确命令行启动。"""
    monkeypatch.setenv("RAG_TASK_WORKER_ENABLED", "false")

    assert rag_task_worker_enabled(parse_args([])) is False
    assert rag_task_worker_enabled(parse_args(["--with-rag-worker"])) is True
    assert rag_task_worker_enabled(parse_args(["--without-rag-worker"])) is False


def test_generic_worker_command_reuses_current_interpreter_and_config_arguments():
    """所有受监督 worker 均以当前 Conda 解释器和同一配置启动。"""
    command = build_worker_command("app.workers.agent_task_worker", ["--config", "config/local.yml"])

    assert command[1:3] == ["-m", "app.workers.agent_task_worker"]
    assert command[3:] == ["--config", "config/local.yml"]


def test_run_entry_starts_and_stops_cron_subprocess(monkeypatch):
    """run.py 启动 API 时先完成迁移编排，并在退出后回收 cron 子进程。"""
    calls = []

    class FakeCronProcess:
        def stop(self):
            calls.append("cron-stop")

    # 启动监督测试只验证进程顺序，迁移 I/O 由 database_migrations 的专门测试覆盖。
    monkeypatch.setattr(
        "app.core.database_migrations.apply_python_schema_migrations",
        lambda: calls.append("migrations") or [],
    )
    monkeypatch.setattr("app.workers.supervisor.start_cron_process", lambda config_args: calls.append(config_args) or FakeCronProcess())
    monkeypatch.setattr("app.core.runtime_config.uvicorn.run", lambda *args, **kwargs: calls.append("uvicorn"))

    main(["--with-cron", "--config", "config/worker-test.yml"])

    assert calls == ["migrations", ["--config", "config/worker-test.yml"], "uvicorn", "cron-stop"]
