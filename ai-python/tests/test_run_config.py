from run import build_env_defaults, cron_enabled, main, parse_args, worker_config_args
from app.core.agent_internal_token import resolve_agent_internal_token


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


def test_agent_internal_token_falls_back_to_local_shared_file(monkeypatch, tmp_path):
    """未显式配置内部令牌时，Python 使用本地共享文件自动生成并复用令牌。"""
    token_file = tmp_path / "agent-internal-token"
    monkeypatch.delenv("EVIDENCE_AGENT_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("EVIDENCE_AGENT_INTERNAL_TOKEN_FILE", str(token_file))

    first = resolve_agent_internal_token()
    monkeypatch.delenv("EVIDENCE_AGENT_INTERNAL_TOKEN", raising=False)
    second = resolve_agent_internal_token()

    assert first
    assert second == first
    assert token_file.read_text(encoding="utf-8").strip() == first


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


def test_cron_cli_override_and_worker_config_forwarding(monkeypatch):
    """命令行可覆盖 cron 开关，子进程只继承配置文件参数而不递归启动。"""
    monkeypatch.setenv("AI_CRON_ENABLED", "false")
    args = parse_args(["--with-cron", "--config", "config/test.yml", "--skip-default-config"])

    assert cron_enabled(args) is True
    assert worker_config_args(args) == ["--skip-default-config", "--config", "config/test.yml"]


def test_run_entry_starts_and_stops_cron_subprocess(monkeypatch):
    """run.py 启动 API 时监督 cron，Uvicorn 退出后必须回收子进程。"""
    calls = []

    class FakeCronProcess:
        def stop(self):
            calls.append("cron-stop")

    monkeypatch.setattr("app.workers.supervisor.start_cron_process", lambda config_args: calls.append(config_args) or FakeCronProcess())
    monkeypatch.setattr("app.core.runtime_config.uvicorn.run", lambda *args, **kwargs: calls.append("uvicorn"))

    main(["--with-cron", "--config", "config/worker-test.yml"])

    assert calls == [["--config", "config/worker-test.yml"], "uvicorn", "cron-stop"]
