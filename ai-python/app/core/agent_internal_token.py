from __future__ import annotations

import base64
import os
from pathlib import Path
import secrets


AI_PYTHON_DIR = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = AI_PYTHON_DIR.parent
TOKEN_FILE_ENV = "EVIDENCE_AGENT_INTERNAL_TOKEN_FILE"
TOKEN_ENV = "EVIDENCE_AGENT_INTERNAL_TOKEN"
LOCAL_TOKEN_FILE = Path(".local") / "agent-internal-token"
TOKEN_BYTES = 32


def resolve_agent_internal_token() -> str:
    """解析 Agent 内部共享令牌；环境变量优先，本地开发自动读取或创建共享文件。"""
    configured = os.getenv(TOKEN_ENV, "").strip()
    if configured:
        return configured
    token = read_or_create_local_token(resolve_token_file())
    if token:
        os.environ.setdefault(TOKEN_ENV, token)
    return token


def resolve_token_file() -> Path:
    """解析本地共享令牌文件路径，支持测试或部署脚本显式覆盖。"""
    configured_path = os.getenv(TOKEN_FILE_ENV, "").strip()
    if configured_path:
        return Path(configured_path).expanduser().resolve()
    return (REPOSITORY_ROOT / LOCAL_TOKEN_FILE).resolve()


def read_or_create_local_token(token_file: Path) -> str:
    """读取本地共享令牌；不存在时原子创建，避免 Java/Python 首次启动生成不同值。"""
    if token_file.exists():
        existing = token_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    token_file.parent.mkdir(parents=True, exist_ok=True)
    generated = new_token()
    try:
        with token_file.open("x", encoding="utf-8") as file:
            file.write(generated + "\n")
        return generated
    except FileExistsError:
        existing = token_file.read_text(encoding="utf-8").strip()
        if existing:
            return existing
        token_file.write_text(generated + "\n", encoding="utf-8")
        return generated


def new_token() -> str:
    """生成不可预测的服务间共享令牌。"""
    raw = secrets.token_bytes(TOKEN_BYTES)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
