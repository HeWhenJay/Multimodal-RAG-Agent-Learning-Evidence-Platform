from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence

from app.workers.scheduler import has_enabled_jobs


AI_PYTHON_DIR = Path(__file__).resolve().parents[2]


@dataclass
class ManagedCronProcess:
    """由 API 启动入口监督的 cron 子进程。"""

    process: subprocess.Popen[object]

    def stop(self) -> None:
        """优先正常终止；超时后强制结束，避免 API 退出时遗留后台任务。"""
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def start_cron_process(config_args: Sequence[str]) -> ManagedCronProcess | None:
    """在独立进程启动 cron，防止 FastAPI reload 复制定时任务。"""
    if not has_enabled_jobs():
        print("Python cron 未启用任何任务，跳过启动")
        return None
    command = build_cron_command(config_args)
    process = subprocess.Popen(
        command,
        cwd=AI_PYTHON_DIR,
        env=os.environ.copy(),
    )
    return ManagedCronProcess(process=process)


def build_cron_command(config_args: Sequence[str]) -> list[str]:
    """复用当前解释器和配置参数启动 worker，确保 Conda 环境保持一致。"""
    return [sys.executable, "-m", "app.workers.scheduler", *config_args]
