"""跨运行方式读取敏感环境变量，不把密钥写入项目配置。"""

from __future__ import annotations

import os
import re


ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def read_process_or_windows_user_environment(name: str) -> str:
    """优先读取进程环境，Windows 下缺失时只读当前用户环境变量。"""
    if not ENVIRONMENT_NAME_PATTERN.fullmatch(name):
        raise ValueError("环境变量名称不合法")
    process_value = (os.getenv(name) or "").strip()
    if process_value:
        return process_value
    if not is_windows():
        return ""
    user_value = read_windows_user_environment(name)
    if user_value:
        # 缓存到当前进程，使 run.py 后续拉起的 API、cron 和 worker 使用同一份配置。
        os.environ[name] = user_value
    return user_value


def is_windows() -> bool:
    """集中判断 Windows，便于跨平台测试用户环境变量回退。"""
    return os.name == "nt"


def read_windows_user_environment(name: str) -> str:
    """从 HKCU\\Environment 读取当前 Windows 用户的字符串环境变量。"""
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Environment",
            0,
            winreg.KEY_READ,
        ) as key:
            raw_value, _value_type = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    return raw_value.strip() if isinstance(raw_value, str) else ""
