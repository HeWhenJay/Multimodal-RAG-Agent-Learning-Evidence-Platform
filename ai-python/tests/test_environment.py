"""敏感环境变量读取边界测试。"""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from app.core import environment


def test_process_environment_has_highest_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """进程已继承变量时不得访问 Windows 用户环境变量。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "process-key")
    monkeypatch.setattr(
        environment,
        "read_windows_user_environment",
        lambda _name: pytest.fail("不应读取 Windows 用户环境变量"),
    )

    assert environment.read_process_or_windows_user_environment("DEEPSEEK_API_KEY") == "process-key"


def test_windows_user_environment_is_cached_for_child_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    """PyCharm 环境快照过期时从 HKCU 读取，并缓存到当前进程供 worker 继承。"""
    calls: list[tuple[object, str, int, object]] = []

    class FakeKey:
        """模拟 winreg 注册表键上下文。"""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_READ=object(),
        OpenKey=lambda root, path, reserved, access: calls.append((root, path, reserved, access)) or FakeKey(),
        QueryValueEx=lambda _key, name: ("  user-key  " if name == "DEEPSEEK_API_KEY" else "", 1),
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(environment, "is_windows", lambda: True)
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    result = environment.read_process_or_windows_user_environment("DEEPSEEK_API_KEY")

    assert result == "user-key"
    assert os.environ["DEEPSEEK_API_KEY"] == "user-key"
    assert calls and calls[0][1] == "Environment"


def test_non_windows_missing_environment_does_not_use_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux 与容器环境只使用标准进程环境。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(environment, "is_windows", lambda: False)
    monkeypatch.setattr(
        environment,
        "read_windows_user_environment",
        lambda _name: pytest.fail("非 Windows 不应读取注册表"),
    )

    assert environment.read_process_or_windows_user_environment("DEEPSEEK_API_KEY") == ""


def test_invalid_environment_name_is_rejected() -> None:
    """拒绝把任意注册表路径当作环境变量名称。"""
    with pytest.raises(ValueError, match="环境变量名称不合法"):
        environment.read_process_or_windows_user_environment("../DEEPSEEK_API_KEY")
