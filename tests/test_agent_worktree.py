"""agent_worktree 单测：build_worktree_notice + _execute_with_worktree 集成。

使用真实临时 git 仓库 + stub Manager 验证完整链路。
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from suisuicode.agent.agent_worktree import (
    _execute_with_worktree,
    build_worktree_notice,
)


# ── build_worktree_notice ───────────────────────────────


def test_build_worktree_notice_contains_worktree_context_tag() -> None:
    """notice 包含 <worktree-context> 标签。"""
    notice = build_worktree_notice("/home/parent", "/tmp/wt")
    assert "<worktree-context>" in notice
    assert "</worktree-context>" in notice


def test_build_worktree_notice_contains_dirs() -> None:
    """notice 包含父目录和工作目录。"""
    notice = build_worktree_notice("/home/parent", "/tmp/wt")
    assert "/home/parent" in notice
    assert "/tmp/wt" in notice


def test_build_worktree_notice_chinese_prompt() -> None:
    """notice 使用中文提示。"""
    notice = build_worktree_notice("/a", "/b")
    assert "Worktree" in notice
    assert "隔离" in notice


# ── _execute_with_worktree ─────────────────────────────


class _StubAgent:
    """Stub Agent：直接返回预设文本。"""

    def __init__(self, return_text: str = "done") -> None:
        self._return_text = return_text

    async def run_to_completion(self, conv, task, events) -> str:
        """记录 task 并返回预设文本。"""
        self.last_task = task
        return self._return_text


class _StubManager:
    """Stub Manager：记录调用但不执行真实 git 操作。"""

    def __init__(self, *, has_changes: bool = False) -> None:
        self.created: list[str] = []
        self.cleaned: list[str] = []
        self._has_changes = has_changes
        self.next_wt = None

    async def create(self, name: str, base_ref: str, manual: bool):
        """创建 stub Worktree。"""
        self.created.append(name)

        from suisuicode.worktree.create import Worktree
        from datetime import datetime

        wt = Worktree(
            name=name,
            path=f"/fake/worktrees/{name}",
            branch=f"worktree-{name}",
            based_on=base_ref,
            head_commit="a" * 40,
            created=datetime.now(),
            manual=manual,
        )
        self.next_wt = wt
        return wt

    async def auto_cleanup(self, name: str):
        """stub auto_cleanup。"""
        self.cleaned.append(name)
        from suisuicode.worktree.lifecycle import AutoCleanupReport

        if self._has_changes:
            return AutoCleanupReport(kept=True, path=f"/fake/{name}", branch=f"br-{name}")
        return AutoCleanupReport(kept=False)


@pytest.mark.asyncio
async def test_execute_with_worktree_calls_create_and_cleanup() -> None:
    """_execute_with_worktree 调用 manager.create + auto_cleanup。"""
    mgr = _StubManager()
    agent = _StubAgent("hello world")
    conv = MagicMock()
    events = asyncio.Queue()

    final = await _execute_with_worktree(
        mgr, MagicMock(), agent, conv, "do something", events,
    )

    assert len(mgr.created) == 1
    assert len(mgr.cleaned) == 1
    assert "hello world" in final


@pytest.mark.asyncio
async def test_execute_with_worktree_injects_notice() -> None:
    """_execute_with_worktree 在 task 前注入 worktree notice。"""
    mgr = _StubManager()
    agent = _StubAgent("ok")
    conv = MagicMock()
    events = asyncio.Queue()

    await _execute_with_worktree(
        mgr, MagicMock(), agent, conv, "the real task", events,
    )

    assert agent.last_task is not None
    assert "<worktree-context>" in agent.last_task
    assert "the real task" in agent.last_task


@pytest.mark.asyncio
async def test_execute_with_worktree_kept_appends_info() -> None:
    """_execute_with_worktree 当 kept=True 时追加保留信息。"""
    mgr = _StubManager(has_changes=True)
    agent = _StubAgent("done")
    conv = MagicMock()
    events = asyncio.Queue()

    final = await _execute_with_worktree(
        mgr, MagicMock(), agent, conv, "task", events,
    )

    assert "[Worktree 保留" in final
