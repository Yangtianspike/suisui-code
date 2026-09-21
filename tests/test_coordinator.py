"""Coordinator Mode 单测。"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from suisuicode.coordinator import (
    COORDINATOR_ALLOWED_TOOLS,
    allowed_tools,
    env_truthy,
    is_enabled,
    system_prompt_suffix,
)


@dataclass
class FakeFeatures:
    coordinator_mode: bool = False
    fork_teammate: bool = False


@dataclass
class FakeConfig:
    features: FakeFeatures = field(default_factory=FakeFeatures)


class TestIsEnabled:
    """双锁机制四种组合。"""

    def test_both_on(self, monkeypatch) -> None:
        cfg = FakeConfig(features=FakeFeatures(coordinator_mode=True))
        monkeypatch.setenv("SUISUICODE_COORDINATOR_MODE", "1")
        assert is_enabled(cfg)

    def test_feature_off(self, monkeypatch) -> None:
        cfg = FakeConfig(features=FakeFeatures(coordinator_mode=False))
        monkeypatch.setenv("SUISUICODE_COORDINATOR_MODE", "1")
        assert not is_enabled(cfg)

    def test_env_off(self, monkeypatch) -> None:
        cfg = FakeConfig(features=FakeFeatures(coordinator_mode=True))
        monkeypatch.delenv("SUISUICODE_COORDINATOR_MODE", raising=False)
        assert not is_enabled(cfg)

    def test_both_off(self, monkeypatch) -> None:
        cfg = FakeConfig(features=FakeFeatures(coordinator_mode=False))
        monkeypatch.delenv("SUISUICODE_COORDINATOR_MODE", raising=False)
        assert not is_enabled(cfg)

    def test_no_features_attr(self, monkeypatch) -> None:
        cfg = object()
        monkeypatch.setenv("SUISUICODE_COORDINATOR_MODE", "1")
        assert not is_enabled(cfg)


class TestEnvTruthy:
    """env_truthy 大小写不敏感。"""

    @pytest.mark.parametrize("val,expected", [
        ("1", True),
        ("true", True),
        ("True", True),
        ("TRUE", True),
        ("yes", True),
        ("YES", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("no", False),
    ])
    def test_truthy(self, val: str, expected: bool) -> None:
        assert env_truthy(val) == expected


class TestAllowedTools:
    """白名单内容检查。"""

    def test_bash_in(self) -> None:
        tools = allowed_tools()
        assert "bash" in tools

    def test_write_file_not_in(self) -> None:
        tools = allowed_tools()
        assert "write_file" not in tools
        assert "edit_file" not in tools

    def test_coordinator_tools_in(self) -> None:
        tools = allowed_tools()
        for t in ["Agent", "TaskCreate", "read_file", "glob", "grep"]:
            assert t in tools


class TestPrompt:
    """系统提示词。"""

    def test_nonempty(self) -> None:
        suffix = system_prompt_suffix()
        assert len(suffix) > 100
        assert "Coordinator Mode" in suffix
