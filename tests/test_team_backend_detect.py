"""后端检测逻辑单测。"""

from __future__ import annotations

import pytest

from suisuicode.team.backend.detect import detect
from suisuicode.team.types import BackendType


class TestDetect:
    """detect_backend 四种路径。"""

    def test_tmux_env(self, monkeypatch) -> None:
        """$TMUX 已设 → tmux。"""
        monkeypatch.setenv("TMUX", "/tmp/tmux-1234/default,1234,0")
        monkeypatch.setenv("TERM_PROGRAM", "")
        assert detect() == BackendType.TMUX

    def test_iterm2_env(self, monkeypatch) -> None:
        """$TERM_PROGRAM=iTerm.app 且 it2 可用 → iterm2。"""
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.setenv("TERM_PROGRAM", "iTerm.app")

        def _fake_which(cmd: str) -> str | None:
            if cmd == "it2":
                return "/usr/local/bin/it2"
            return None

        monkeypatch.setattr("shutil.which", _fake_which)
        assert detect() == BackendType.ITERM2

    def test_tmux_binary(self, monkeypatch) -> None:
        """没 TMUX 没 iTerm2 但 tmux 在 PATH → tmux。"""
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)

        def _fake_which(cmd: str) -> str | None:
            if cmd == "tmux":
                return "/usr/bin/tmux"
            return None

        monkeypatch.setattr("shutil.which", _fake_which)
        assert detect() == BackendType.TMUX

    def test_fallback_inprocess(self, monkeypatch) -> None:
        """全无 → in-process。"""
        monkeypatch.delenv("TMUX", raising=False)
        monkeypatch.delenv("TERM_PROGRAM", raising=False)

        def _fake_which(cmd: str) -> str | None:
            return None

        monkeypatch.setattr("shutil.which", _fake_which)
        assert detect() == BackendType.IN_PROCESS
