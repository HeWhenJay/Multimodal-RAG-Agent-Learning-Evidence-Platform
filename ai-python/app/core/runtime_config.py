from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import uvicorn

from app.core.agent_internal_token import resolve_agent_internal_token


AI_PYTHON_DIR = Path(__file__).resolve().parents[2]
if str(AI_PYTHON_DIR) not in sys.path:
    sys.path.insert(0, str(AI_PYTHON_DIR))

DEFAULT_CONFIG_PATH = AI_PYTHON_DIR / "config" / "application.yml"
LOCAL_CONFIG_PATH = AI_PYTHON_DIR / "config" / "application.local.yml"
PLACEHOLDER_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::([^}]*))?\}")
CONFIG_ENV_MAPPING: dict[tuple[str, ...], str] = {
    ("server", "host"): "AI_SERVICE_HOST",
    ("server", "port"): "AI_SERVICE_PORT",
    ("server", "reload"): "AI_SERVICE_RELOAD",
    ("rag", "store-backend"): "RAG_STORE_BACKEND",
    ("rag", "store", "backend"): "RAG_STORE_BACKEND",
    ("rag", "database-url"): "RAG_DATABASE_URL",
    ("rag", "database", "url"): "RAG_DATABASE_URL",
    ("rag", "database-schema"): "RAG_DATABASE_SCHEMA",
    ("rag", "database", "schema"): "RAG_DATABASE_SCHEMA",
    ("rag", "vector-dimensions"): "RAG_VECTOR_DIMENSIONS",
    ("rag", "vector", "dimensions"): "RAG_VECTOR_DIMENSIONS",
    ("rag", "embedding-model"): "RAG_EMBEDDING_MODEL",
    ("rag", "embedding", "model"): "RAG_EMBEDDING_MODEL",
    ("rag", "embedding-provider"): "RAG_EMBEDDING_PROVIDER",
    ("rag", "embedding", "provider"): "RAG_EMBEDDING_PROVIDER",
    ("rag", "embedding-base-url"): "RAG_EMBEDDING_BASE_URL",
    ("rag", "embedding", "base-url"): "RAG_EMBEDDING_BASE_URL",
    ("rag", "embedding-timeout-seconds"): "RAG_EMBEDDING_TIMEOUT_SECONDS",
    ("rag", "embedding", "timeout-seconds"): "RAG_EMBEDDING_TIMEOUT_SECONDS",
    ("rag", "rerank-provider"): "RAG_RERANK_PROVIDER",
    ("rag", "rerank", "provider"): "RAG_RERANK_PROVIDER",
    ("rag", "rerank-model"): "RAG_RERANK_MODEL",
    ("rag", "rerank", "model"): "RAG_RERANK_MODEL",
    ("rag", "rerank", "base-url"): "RAG_RERANK_BASE_URL",
    ("rag", "rerank", "timeout-seconds"): "RAG_RERANK_TIMEOUT_SECONDS",
    ("rag", "answer-provider"): "RAG_ANSWER_PROVIDER",
    ("rag", "answer", "provider"): "RAG_ANSWER_PROVIDER",
    ("rag", "llm", "provider"): "RAG_ANSWER_PROVIDER",
    ("rag", "llm-model"): "RAG_LLM_MODEL",
    ("rag", "llm", "model"): "RAG_LLM_MODEL",
    ("rag", "llm", "base-url"): "RAG_LLM_BASE_URL",
    ("rag", "llm", "timeout-seconds"): "RAG_LLM_TIMEOUT_SECONDS",
    ("rag", "llm-temperature"): "RAG_LLM_TEMPERATURE",
    ("rag", "llm", "temperature"): "RAG_LLM_TEMPERATURE",
    ("rag", "progress", "console-enabled"): "RAG_CONSOLE_PROGRESS_ENABLED",
    ("rag", "process-log", "console-enabled"): "RAG_CONSOLE_PROCESS_ENABLED",
    ("rag", "callbacks", "event-url"): "RAG_EVENT_CALLBACK_URL",
    ("rag", "callbacks", "error-url"): "RAG_ERROR_CALLBACK_URL",
    ("rag", "kafka", "enabled"): "RAG_KAFKA_ENABLED",
    ("rag", "kafka", "bootstrap-servers"): "RAG_KAFKA_BOOTSTRAP_SERVERS",
    ("rag", "kafka", "java-base-url"): "RAG_JAVA_BASE_URL",
    ("rag", "kafka", "java-internal-token"): "RAG_JAVA_INTERNAL_TOKEN",
    ("rag", "kafka", "worker", "reconnect-initial-seconds"): "RAG_KAFKA_RECONNECT_INITIAL_SECONDS",
    ("rag", "kafka", "worker", "reconnect-max-seconds"): "RAG_KAFKA_RECONNECT_MAX_SECONDS",
    ("rag", "kafka", "topics", "index-request"): "RAG_KAFKA_TOPIC_INDEX_REQUEST",
    ("rag", "kafka", "topics", "index-result"): "RAG_KAFKA_TOPIC_INDEX_RESULT",
    ("rag", "kafka", "topics", "progress"): "RAG_KAFKA_TOPIC_PROGRESS",
    ("rag", "kafka", "topics", "promote-request"): "RAG_KAFKA_TOPIC_PROMOTE_REQUEST",
    ("rag", "kafka", "topics", "promote-result"): "RAG_KAFKA_TOPIC_PROMOTE_RESULT",
    ("rag", "kafka", "topics", "index-retry-1m"): "RAG_KAFKA_TOPIC_INDEX_RETRY_1M",
    ("rag", "kafka", "topics", "index-retry-10m"): "RAG_KAFKA_TOPIC_INDEX_RETRY_10M",
    ("rag", "kafka", "topics", "index-retry-1h"): "RAG_KAFKA_TOPIC_INDEX_RETRY_1H",
    ("rag", "kafka", "topics", "index-dlq"): "RAG_KAFKA_TOPIC_INDEX_DLQ",
    ("rag", "kafka", "retry", "max-attempts"): "RAG_KAFKA_MAX_ATTEMPTS",
    ("rag", "kafka", "retry", "delay-1m-seconds"): "RAG_KAFKA_RETRY_1M_SECONDS",
    ("rag", "kafka", "retry", "delay-10m-seconds"): "RAG_KAFKA_RETRY_10M_SECONDS",
    ("rag", "kafka", "retry", "delay-1h-seconds"): "RAG_KAFKA_RETRY_1H_SECONDS",
    ("rag", "kafka", "retry", "max-sleep-seconds"): "RAG_KAFKA_RETRY_MAX_SLEEP_SECONDS",
    ("rag", "kafka", "progress", "chunk-interval"): "RAG_KAFKA_PROGRESS_CHUNK_INTERVAL",
    ("rag", "kafka", "progress", "min-seconds"): "RAG_KAFKA_PROGRESS_MIN_SECONDS",
    ("rag", "staging-retention-hours"): "RAG_STAGING_RETENTION_HOURS",
    ("rag", "staging-failed-retention-hours"): "RAG_STAGING_FAILED_RETENTION_HOURS",
    ("dashscope", "api-key"): "DASHSCOPE_API_KEY",
    ("mineru", "command"): "MINERU_COMMAND",
    ("mineru", "token"): "MINERU_TOKEN",
    ("mineru", "api-token"): "MINERU_API_TOKEN",
    ("mineru", "api-key"): "MINERU_API_KEY",
    ("ocr", "enabled"): "BAILIAN_OCR_ENABLED",
    ("ocr", "bailian", "enabled"): "BAILIAN_OCR_ENABLED",
    ("ocr", "model"): "BAILIAN_OCR_MODEL",
    ("ocr", "bailian", "model"): "BAILIAN_OCR_MODEL",
    ("ocr", "base-url"): "BAILIAN_OCR_BASE_URL",
    ("ocr", "bailian", "base-url"): "BAILIAN_OCR_BASE_URL",
    ("ocr", "bailian", "timeout-seconds"): "BAILIAN_OCR_TIMEOUT_SECONDS",
    ("ocr", "bailian", "max-image-bytes"): "BAILIAN_OCR_MAX_IMAGE_BYTES",
    ("ocr", "bailian", "max-attempts"): "BAILIAN_OCR_MAX_ATTEMPTS",
    ("ocr", "bailian", "retry-delay-seconds"): "BAILIAN_OCR_RETRY_DELAY_SECONDS",
    ("ocr", "lang"): "OCR_LANG",
    ("asr", "provider"): "RAG_ASR_PROVIDER",
    ("asr", "base-url"): "RAG_ASR_BASE_URL",
    ("asr", "task-base-url"): "RAG_ASR_TASK_BASE_URL",
    ("asr", "model"): "RAG_ASR_MODEL",
    ("asr", "filetrans-model"): "RAG_ASR_FILETRANS_MODEL",
    ("asr", "filetrans-enabled"): "RAG_ASR_FILETRANS_ENABLED",
    ("asr", "enable-words"): "RAG_ASR_ENABLE_WORDS",
    ("asr", "timeout-seconds"): "RAG_ASR_TIMEOUT_SECONDS",
    ("asr", "max-audio-bytes"): "RAG_ASR_MAX_AUDIO_BYTES",
    ("asr", "filetrans-max-polls"): "RAG_ASR_FILETRANS_MAX_POLLS",
    ("asr", "filetrans-poll-interval-seconds"): "RAG_ASR_FILETRANS_POLL_INTERVAL_SECONDS",
    ("video", "ffmpeg-command"): "FFMPEG_COMMAND",
    ("video", "ffprobe-command"): "FFPROBE_COMMAND",
    ("video", "ffmpeg-timeout-seconds"): "RAG_VIDEO_FFMPEG_TIMEOUT_SECONDS",
    ("video", "audio-segment-seconds"): "RAG_VIDEO_AUDIO_SEGMENT_SECONDS",
    ("video", "audio-overlap-seconds"): "RAG_VIDEO_AUDIO_OVERLAP_SECONDS",
    ("video", "frame-scan-mode"): "RAG_VIDEO_FRAME_SCAN_MODE",
    ("video", "frame-sample-interval-seconds"): "RAG_VIDEO_FRAME_SAMPLE_INTERVAL_SECONDS",
    ("video", "frame-interval-seconds"): "RAG_VIDEO_FRAME_INTERVAL_SECONDS",
    ("video", "frame-min-interval-seconds"): "RAG_VIDEO_FRAME_MIN_INTERVAL_SECONDS",
    ("video", "max-frames"): "RAG_VIDEO_MAX_FRAMES",
    ("video", "frame-target-candidates"): "RAG_VIDEO_FRAME_TARGET_CANDIDATES",
    ("video", "frame-max-candidates"): "RAG_VIDEO_FRAME_MAX_CANDIDATES",
    ("video", "ppt-flip-diff-threshold"): "RAG_VIDEO_PPT_FLIP_DIFF_THRESHOLD",
    ("video", "frame-visual-dedup-enabled"): "RAG_VIDEO_FRAME_VISUAL_DEDUP_ENABLED",
    ("video", "frame-visual-hash-algorithm"): "RAG_VIDEO_FRAME_VISUAL_HASH_ALGORITHM",
    ("video", "frame-visual-hash-max-distance"): "RAG_VIDEO_FRAME_VISUAL_HASH_MAX_DISTANCE",
    ("video", "frame-visual-ambiguous-margin"): "RAG_VIDEO_FRAME_VISUAL_AMBIGUOUS_MARGIN",
    ("video", "frame-max-representatives-per-visual-group"): "RAG_VIDEO_FRAME_MAX_REPRESENTATIVES_PER_VISUAL_GROUP",
    ("video", "frame-visual-verify-interval-seconds"): "RAG_VIDEO_FRAME_VISUAL_VERIFY_INTERVAL_SECONDS",
    ("video", "frame-visual-stay-verify-seconds"): "RAG_VIDEO_FRAME_VISUAL_STAY_VERIFY_SECONDS",
    ("video", "frame-visual-revisit-verify-seconds"): "RAG_VIDEO_FRAME_VISUAL_REVISIT_VERIFY_SECONDS",
    ("video", "frame-visual-verification-ratio"): "RAG_VIDEO_FRAME_VISUAL_VERIFICATION_RATIO",
    ("video", "frame-max-verifications-per-visual-group"): "RAG_VIDEO_FRAME_MAX_VERIFICATIONS_PER_VISUAL_GROUP",
    ("video", "segment-seconds"): "RAG_VIDEO_SEGMENT_SECONDS",
    ("video", "segment-max-cues"): "RAG_VIDEO_SEGMENT_MAX_CUES",
    ("document", "convert", "libreoffice-command"): "LIBREOFFICE_COMMAND",
    ("document", "convert", "soffice-command"): "SOFFICE_COMMAND",
    ("agent", "internal-token"): "EVIDENCE_AGENT_INTERNAL_TOKEN",
    ("agent", "internal-token-file"): "EVIDENCE_AGENT_INTERNAL_TOKEN_FILE",
}


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """解析本地启动参数，支持指定额外运行配置文件。"""
    parser = argparse.ArgumentParser(description="启动 Python RAG FastAPI 服务")
    parser.add_argument(
        "--config",
        action="append",
        default=[],
        help="额外加载的 YAML 配置文件，可重复传入；相对路径按 ai-python 目录解析。",
    )
    parser.add_argument(
        "--skip-default-config",
        action="store_true",
        help="跳过默认的 config/application.yml 和 config/application.local.yml。",
    )
    return parser.parse_args(argv)


def load_runtime_config(args: argparse.Namespace) -> None:
    """读取 YAML 运行配置，并写入缺省环境变量。"""
    config_paths = resolve_config_paths(args)
    merged_config: dict[str, Any] = {}
    loaded_paths: list[Path] = []
    for path in config_paths:
        if not path.exists():
            continue
        config = read_yaml_mapping(path)
        deep_merge(merged_config, config)
        loaded_paths.append(path)

    env_defaults = build_env_defaults(merged_config)
    for name, value in env_defaults.items():
        os.environ.setdefault(name, value)
    os.environ.setdefault("EVIDENCE_AGENT_INTERNAL_TOKEN", resolve_agent_internal_token())

    if loaded_paths:
        joined_paths = ", ".join(str(path) for path in loaded_paths)
        print(f"已加载 Python AI 运行配置: {joined_paths}")


def resolve_config_paths(args: argparse.Namespace) -> list[Path]:
    """计算本次启动需要加载的配置文件路径。"""
    paths: list[Path] = []
    if not args.skip_default_config:
        paths.extend([DEFAULT_CONFIG_PATH, LOCAL_CONFIG_PATH])
    paths.extend(resolve_config_path(path) for path in args.config)
    return paths


def resolve_config_path(path_text: str) -> Path:
    """将配置文件路径解析为绝对路径。"""
    path = Path(path_text)
    if path.is_absolute():
        return path
    return AI_PYTHON_DIR / path


def read_yaml_mapping(path: Path) -> dict[str, Any]:
    """读取 YAML 配置；未安装 PyYAML 时支持当前配置所需的简单 YAML 子集。"""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return read_simple_yaml_mapping(path)

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件必须是 YAML 对象: {path}")
    return resolve_placeholders(dict(data))


def read_simple_yaml_mapping(path: Path) -> dict[str, Any]:
    """解析两空格缩进的简单 YAML，避免本地环境缺少 PyYAML 时无法启动。"""
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if "\t" in raw_line:
            raise ValueError(f"配置文件不支持 Tab 缩进: {path}:{line_number}")
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent % 2 != 0:
            raise ValueError(f"配置文件请使用两个空格缩进: {path}:{line_number}")
        if ":" not in raw_line:
            raise ValueError(f"配置行缺少冒号: {path}:{line_number}")

        level = indent // 2
        key, raw_value = raw_line.strip().split(":", 1)
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1]
        value_text = raw_value.strip()
        if value_text == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((level, child))
        else:
            parent[key] = parse_simple_yaml_scalar(value_text)
    return resolve_placeholders(root)


def resolve_placeholders(value: Any) -> Any:
    """解析配置中的 ${ENV:默认值} 占位符，风格对齐 Spring Boot 配置。"""
    if isinstance(value, dict):
        return {key: resolve_placeholders(child) for key, child in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(item) for item in value]
    if isinstance(value, str):
        return PLACEHOLDER_PATTERN.sub(resolve_placeholder_match, value)
    return value


def resolve_placeholder_match(match: re.Match[str]) -> str:
    """读取环境变量；未配置时使用占位符冒号后的默认值。"""
    env_name = match.group(1)
    default_value = match.group(2) or ""
    return os.getenv(env_name, default_value)


def parse_simple_yaml_scalar(value: str) -> str | bool | int | float | None:
    """解析简单 YAML 标量，满足本地运行配置的常见类型。"""
    if value in {"''", '""'}:
        return ""
    if (value.startswith("'") and value.endswith("'")) or (value.startswith('"') and value.endswith('"')):
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        return value


def deep_merge(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    """递归合并配置，后加载文件覆盖先加载文件。"""
    for key, value in source.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            deep_merge(target[key], value)  # type: ignore[index]
        else:
            target[key] = dict(value) if isinstance(value, Mapping) else value


def build_env_defaults(config: Mapping[str, Any]) -> dict[str, str]:
    """将结构化配置转换为本服务使用的环境变量默认值。"""
    env_defaults: dict[str, str] = {}
    for path, env_name in CONFIG_ENV_MAPPING.items():
        value = nested_get(config, path)
        env_value = stringify_env_value(value)
        if env_value is not None:
            env_defaults[env_name] = env_value

    extra_environment = config.get("environment")
    if isinstance(extra_environment, Mapping):
        for key, value in extra_environment.items():
            env_value = stringify_env_value(value)
            if env_value is not None:
                env_defaults[str(key)] = env_value
    return env_defaults


def nested_get(config: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    """从嵌套配置中读取指定路径。"""
    current: Any = config
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def stringify_env_value(value: Any) -> str | None:
    """将配置值转换为环境变量字符串，空值不写入。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return text or None


def read_bool_env(name: str, default: bool) -> bool:
    """读取布尔环境变量，方便 PyCharm 本地调试时开关热重载。"""
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def read_port() -> int:
    """读取 Python AI 服务端口，默认对齐 Java 配置中的 8090。"""
    value = os.getenv("AI_SERVICE_PORT") or os.getenv("PORT") or "8090"
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"AI_SERVICE_PORT 必须是整数，当前值为: {value}") from exc


def main(argv: Sequence[str] | None = None) -> None:
    """启动本地 Python RAG FastAPI 服务。"""
    load_runtime_config(parse_args(argv))
    host = os.getenv("AI_SERVICE_HOST", "127.0.0.1")
    port = read_port()
    reload_enabled = read_bool_env("AI_SERVICE_RELOAD", True)
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload_enabled,
        reload_dirs=[str(AI_PYTHON_DIR)] if reload_enabled else None,
        app_dir=str(AI_PYTHON_DIR),
    )


if __name__ == "__main__":
    main()
