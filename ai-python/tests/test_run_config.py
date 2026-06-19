from run import build_env_defaults


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
