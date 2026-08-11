import os

from run import (
    agent_worker_enabled,
    apply_runtime_mode_overrides,
    build_env_defaults,
    cron_enabled,
    kafka_enabled,
    load_runtime_config,
    main,
    parse_args,
    rag_task_worker_enabled,
    worker_config_args,
)
from app.workers.supervisor import build_worker_command


def test_llm_io_thread_pool_config_env_mapping_is_effective():
    """统一模型 I/O 线程数必须能从 YAML 映射为环境变量。"""
    env_defaults = build_env_defaults({"llm": {"io-max-workers": 24}})

    assert env_defaults["LLM_IO_MAX_WORKERS"] == "24"


def test_review_segment_budget_config_env_mapping_is_effective() -> None:
    """交互式分段的单请求、重试和质量轮次预算必须可由 YAML 配置。"""
    env_defaults = build_env_defaults(
        {
            "review": {
                "segment": {
                    "timeout-seconds": 1800,
                    "request-timeout-seconds": 180,
                    "cockpit-request-retries": 1,
                    "max-generation-attempts": 3,
                    "max-merge-rounds": 2,
                }
            }
        }
    )

    assert env_defaults["REVIEW_SEGMENT_TIMEOUT_SECONDS"] == "1800"
    assert env_defaults["REVIEW_SEGMENT_REQUEST_TIMEOUT_SECONDS"] == "180"
    assert env_defaults["REVIEW_SEGMENT_COCKPIT_REQUEST_RETRIES"] == "1"
    assert env_defaults["REVIEW_SEGMENT_MAX_GENERATION_ATTEMPTS"] == "3"
    assert env_defaults["REVIEW_SEGMENT_MAX_MERGE_ROUNDS"] == "2"


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


def test_review_llm_config_uses_dedicated_environment_variables():
    """复习模型的中转地址、模型、思考强度和密钥均映射到专用环境变量。"""
    env_defaults = build_env_defaults(
        {
            "review": {
                "llm": {
                    "base-url": "http://localhost:58966/v1",
                    "model": "gpt-5.6-terra",
                    "reasoning-effort": "max",
                    "thinking-enabled": True,
                    "api-key": "test-review-key",
                    "fallback": {
                        "enabled": True,
                        "model": "deepseek-v4-flash",
                        "base-url": "https://api.deepseek.com",
                        "api-key": "test-fallback-key",
                    },
                    "timeout-seconds": 45,
                    "max-in-flight": 8,
                    "cockpit": {
                        "retry-enabled": True,
                        "stream-open-timeout-seconds": 180,
                        "stream-idle-timeout-seconds": 240,
                        "bootstrap-retries": 1,
                        "request-retries": 1,
                        "retry-base-delay-ms": 300,
                        "retry-max-delay-ms": 1500,
                        "keepalive-seconds": 15,
                    },
                },
                "langextract": {
                    "enabled": True,
                    "extraction-passes": 2,
                    "max-char-buffer": 8000,
                    "max-workers": 8,
                    "max-model-requests": 32,
                    "timeout-seconds": 120,
                }
            }
        }
    )

    assert env_defaults["REVIEW_LLM_API_KEY"] == "test-review-key"
    assert env_defaults["REVIEW_LLM_BASE_URL"] == "http://localhost:58966/v1"
    assert env_defaults["REVIEW_LLM_MODEL"] == "gpt-5.6-terra"
    assert env_defaults["REVIEW_LLM_REASONING_EFFORT"] == "max"
    assert env_defaults["REVIEW_LLM_THINKING_ENABLED"] == "true"
    assert env_defaults["REVIEW_LLM_FALLBACK_ENABLED"] == "true"
    assert env_defaults["REVIEW_LLM_FALLBACK_MODEL"] == "deepseek-v4-flash"
    assert env_defaults["REVIEW_LLM_FALLBACK_BASE_URL"] == "https://api.deepseek.com"
    assert env_defaults["REVIEW_LLM_FALLBACK_API_KEY"] == "test-fallback-key"
    assert env_defaults["REVIEW_EXTRACTION_TIMEOUT_SECONDS"] == "45"
    assert env_defaults["REVIEW_DEEPSEEK_MAX_IN_FLIGHT"] == "8"
    assert env_defaults["REVIEW_COCKPIT_RETRY_ENABLED"] == "true"
    assert env_defaults["REVIEW_COCKPIT_STREAM_OPEN_TIMEOUT_SECONDS"] == "180"
    assert env_defaults["REVIEW_COCKPIT_STREAM_IDLE_TIMEOUT_SECONDS"] == "240"
    assert env_defaults["REVIEW_COCKPIT_BOOTSTRAP_RETRIES"] == "1"
    assert env_defaults["REVIEW_COCKPIT_REQUEST_RETRIES"] == "1"
    assert env_defaults["REVIEW_COCKPIT_RETRY_BASE_DELAY_MS"] == "300"
    assert env_defaults["REVIEW_COCKPIT_RETRY_MAX_DELAY_MS"] == "1500"
    assert env_defaults["REVIEW_COCKPIT_KEEPALIVE_SECONDS"] == "15"
    assert env_defaults["REVIEW_LANGEXTRACT_ENABLED"] == "true"
    assert env_defaults["REVIEW_LANGEXTRACT_EXTRACTION_PASSES"] == "2"
    assert env_defaults["REVIEW_LANGEXTRACT_MAX_CHAR_BUFFER"] == "8000"
    assert env_defaults["REVIEW_LANGEXTRACT_MAX_WORKERS"] == "8"
    assert env_defaults["REVIEW_LANGEXTRACT_MAX_MODEL_REQUESTS"] == "32"
    assert env_defaults["REVIEW_LANGEXTRACT_TIMEOUT_SECONDS"] == "120"
    assert "SUBAI_BASE_URL" not in env_defaults
    assert "SU_BAI_API_KEY" not in env_defaults
    assert "DASHSCOPE_API_KEY" not in env_defaults
    assert "RAG_LLM_BASE_URL" not in env_defaults


def test_missing_review_key_is_reported_during_startup(monkeypatch, capsys):
    """缺少复习中转密钥时启动日志应给出可操作提示，但不阻止其他接口启动。"""
    monkeypatch.delenv("REVIEW_LLM_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.core.runtime_config.read_process_or_windows_user_environment",
        lambda _name: "",
    )

    load_runtime_config(parse_args(["--skip-default-config"]))

    assert "未配置 REVIEW_LLM_API_KEY" in capsys.readouterr().out


def test_model_dynamic_batch_config_env_mapping_is_effective():
    """校验 OCR/ASR/embedding 动态批处理配置可由 YAML 统一映射。"""
    env_defaults = build_env_defaults(
        {
            "rag": {
                "embedding": {
                    "batch-max-size": 10,
                    "batch-wait-ms": 1000,
                    "max-in-flight": 2,
                },
                "retrieval": {"io-workers": 8},
            },
            "asr": {
                "batch-max-size": 4,
                "batch-wait-ms": 1000,
                "max-in-flight": 2,
                "rpm-limit": 90,
            },
        }
    )

    assert env_defaults["RAG_EMBEDDING_BATCH_MAX_SIZE"] == "10"
    assert env_defaults["RAG_EMBEDDING_BATCH_WAIT_MS"] == "1000"
    assert env_defaults["RAG_EMBEDDING_MAX_IN_FLIGHT"] == "2"
    assert env_defaults["RAG_RETRIEVAL_IO_WORKERS"] == "8"
    assert env_defaults["RAG_ASR_BATCH_MAX_SIZE"] == "4"
    assert env_defaults["RAG_ASR_BATCH_WAIT_MS"] == "1000"
    assert env_defaults["RAG_ASR_MAX_IN_FLIGHT"] == "2"
    assert env_defaults["RAG_ASR_RPM_LIMIT"] == "90"


def test_recognition_text_correction_config_env_mapping_is_effective():
    """校验 ASR/OCR 纠错节点配置能从 YAML 映射为运行环境变量。"""
    env_defaults = build_env_defaults(
        {
            "rag": {
                "text-correction": {
                    "enabled": "auto",
                    "model": "qwen-plus",
                    "base-url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "timeout-seconds": 45,
                    "batch-max-items": 32,
                    "batch-max-chars": 12000,
                    "min-similarity": 0.55,
                }
            }
        }
    )

    assert env_defaults["RAG_TEXT_CORRECTION_ENABLED"] == "auto"
    assert env_defaults["RAG_TEXT_CORRECTION_MODEL"] == "qwen-plus"
    assert env_defaults["RAG_TEXT_CORRECTION_BASE_URL"].endswith("/compatible-mode/v1")
    assert env_defaults["RAG_TEXT_CORRECTION_TIMEOUT_SECONDS"] == "45"
    assert env_defaults["RAG_TEXT_CORRECTION_BATCH_MAX_ITEMS"] == "32"
    assert env_defaults["RAG_TEXT_CORRECTION_BATCH_MAX_CHARS"] == "12000"
    assert env_defaults["RAG_TEXT_CORRECTION_MIN_SIMILARITY"] == "0.55"


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
            },
            "ocr": {
                "bailian": {
                    "batch-max-size": 4,
                    "batch-wait-ms": 800,
                    "max-in-flight": 2,
                }
            },
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
    assert env_defaults["RAG_VIDEO_OCR_BATCH_MAX_SIZE"] == "4"
    assert env_defaults["RAG_VIDEO_OCR_BATCH_WAIT_MS"] == "800"
    assert env_defaults["RAG_VIDEO_OCR_MAX_IN_FLIGHT"] == "2"


def test_douyin_mcp_config_env_mapping_is_effective():
    """抖音 MCP 开关、固定 endpoint、密钥和轮询参数可由本地 YAML 注入。"""
    env_defaults = build_env_defaults(
        {
            "remote-video": {
                "douyin": {
                    "enabled": True,
                    "mcp-endpoint": "https://mcp.52choujiang.com/douyin/mcp",
                    "api-key": "unit-socialdatax-key",
                    "connection-timeout-seconds": 20,
                    "tool-timeout-seconds": 45,
                    "poll-interval-seconds": 3,
                    "max-wait-seconds": 600,
                }
            }
        }
    )

    assert env_defaults["RAG_DOUYIN_MCP_ENABLED"] == "true"
    assert env_defaults["RAG_DOUYIN_MCP_ENDPOINT"] == "https://mcp.52choujiang.com/douyin/mcp"
    assert env_defaults["SOCIALDATAX_API_KEY"] == "unit-socialdatax-key"
    assert env_defaults["RAG_DOUYIN_MCP_CONNECTION_TIMEOUT_SECONDS"] == "20"
    assert env_defaults["RAG_DOUYIN_MCP_TOOL_TIMEOUT_SECONDS"] == "45"
    assert env_defaults["RAG_DOUYIN_TRANSCRIPT_POLL_INTERVAL_SECONDS"] == "3"
    assert env_defaults["RAG_DOUYIN_TRANSCRIPT_MAX_WAIT_SECONDS"] == "600"


def test_worker_cron_config_env_mapping_is_effective():
    """校验 Python worker cron 配置可由 YAML 统一映射到运行环境变量。"""
    env_defaults = build_env_defaults(
        {
            "workers": {
                "cron": {"enabled": True, "poll-interval-seconds": 0.25},
                "outbox": {
                    "enabled": True,
                    "batch-size": 20,
                    "publish-concurrency": 16,
                    "lease-seconds": 45,
                    "publish-fixed-delay-ms": 750,
                    "max-attempts": 6,
                    "publish-timeout-ms": 2500,
                },
                "staging-cleanup": {"enabled": False, "fixed-delay-seconds": 600},
                "rag-task": {"enabled": True, "poll-interval-seconds": 0.2, "concurrency": 4},
                "review-sync": {"max-workers": 2},
                "review-task": {
                    "enabled": True,
                    "poll-seconds": 3,
                    "batch-size": 12,
                    "concurrency": 16,
                    "stale-seconds": 900,
                },
            }
        }
    )

    assert env_defaults["AI_CRON_ENABLED"] == "true"
    assert env_defaults["AI_CRON_POLL_INTERVAL_SECONDS"] == "0.25"
    assert env_defaults["RAG_OUTBOX_PUBLISHER_ENABLED"] == "true"
    assert env_defaults["RAG_OUTBOX_BATCH_SIZE"] == "20"
    assert env_defaults["RAG_OUTBOX_PUBLISH_CONCURRENCY"] == "16"
    assert env_defaults["RAG_OUTBOX_LEASE_SECONDS"] == "45"
    assert env_defaults["RAG_OUTBOX_PUBLISH_FIXED_DELAY_MS"] == "750"
    assert env_defaults["RAG_OUTBOX_MAX_ATTEMPTS"] == "6"
    assert env_defaults["RAG_KAFKA_PUBLISH_TIMEOUT_MS"] == "2500"
    assert env_defaults["RAG_STAGING_CLEANUP_ENABLED"] == "false"
    assert env_defaults["RAG_STAGING_CLEANUP_FIXED_DELAY_SECONDS"] == "600"
    assert env_defaults["RAG_TASK_WORKER_ENABLED"] == "true"
    assert env_defaults["RAG_TASK_WORKER_POLL_SECONDS"] == "0.2"
    assert env_defaults["RAG_TASK_WORKER_CONCURRENCY"] == "4"
    assert env_defaults["RAG_REVIEW_SYNC_WORKERS"] == "2"
    assert env_defaults["REVIEW_TASK_WORKER_ENABLED"] == "true"
    assert env_defaults["REVIEW_TASK_WORKER_POLL_SECONDS"] == "3"
    assert env_defaults["REVIEW_TASK_WORKER_BATCH_SIZE"] == "12"
    assert env_defaults["REVIEW_TASK_WORKER_CONCURRENCY"] == "16"
    assert env_defaults["REVIEW_TASK_WORKER_STALE_SECONDS"] == "900"


def test_kafka_worker_consumer_config_env_mapping_is_effective():
    """隔离基准与长视频 worker 参数应由 YAML 统一映射。"""
    env_defaults = build_env_defaults(
        {
            "rag": {
                "kafka": {
                    "worker": {
                        "auto-offset-reset": "latest",
                        "max-poll-interval-ms": 1_800_000,
                        "handler-concurrency": 6,
                        "control-concurrency": 2,
                    }
                }
            }
        }
    )

    assert env_defaults["RAG_KAFKA_AUTO_OFFSET_RESET"] == "latest"
    assert env_defaults["RAG_KAFKA_MAX_POLL_INTERVAL_MS"] == "1800000"
    assert env_defaults["RAG_KAFKA_HANDLER_CONCURRENCY"] == "6"
    assert env_defaults["RAG_KAFKA_CONTROL_CONCURRENCY"] == "2"


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

    class FakeReviewTaskProcess:
        def stop(self):
            calls.append("review-task-stop")

    # 启动监督测试只验证进程顺序，迁移 I/O 由 database_migrations 的专门测试覆盖。
    monkeypatch.setattr(
        "app.core.database_migrations.apply_python_schema_migrations",
        lambda: calls.append("migrations") or [],
    )
    monkeypatch.setattr("app.workers.supervisor.start_cron_process", lambda config_args: calls.append(config_args) or FakeCronProcess())
    monkeypatch.setattr(
        "app.workers.supervisor.start_worker_process",
        lambda module, config_args: calls.append((module, config_args)) or FakeReviewTaskProcess(),
    )
    monkeypatch.setattr("app.core.runtime_config.kafka_enabled", lambda _args: False)
    monkeypatch.setattr("app.core.runtime_config.agent_worker_enabled", lambda _args: False)
    monkeypatch.setattr("app.core.runtime_config.rag_task_worker_enabled", lambda _args: False)
    monkeypatch.setattr("app.core.runtime_config.uvicorn.run", lambda *args, **kwargs: calls.append("uvicorn"))

    main(["--with-cron", "--config", "config/worker-test.yml"])

    assert calls == [
        "migrations",
        ["--config", "config/worker-test.yml"],
        ("app.workers.review_task_worker", ["--config", "config/worker-test.yml"]),
        "uvicorn",
        "review-task-stop",
        "cron-stop",
    ]
