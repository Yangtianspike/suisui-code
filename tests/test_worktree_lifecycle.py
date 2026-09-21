"""Manager 生命周期测试：enter / exit / remove / auto_cleanup。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from suisuicode.worktree import Manager
from suisuicode.worktree.lifecycle import (
    AutoCleanupReport,
    ExitAction,
    ExitOptions,
    ExitReport,
    WorktreeHasChangesError,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """创建临时 git 仓库。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True,
                   capture_output=True)
    return repo


@pytest.fixture
def mgr(git_repo: Path) -> Manager:
    return Manager(str(git_repo))


# ── enter ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enter_does_not_change_process_cwd(mgr: Manager) -> None:
    """enter 不改变进程 cwd。"""
    await mgr.create("test", "HEAD", manual=True)
    original_cwd = str(Path.cwd())
    session = await mgr.enter("test")
    assert str(Path.cwd()) == original_cwd
    assert session.worktree_name == "test"
    assert session.worktree_path != ""
    assert session.session_id != ""
    await mgr.remove("test")


@pytest.mark.asyncio
async def test_enter_persists_session(mgr: Manager, git_repo: Path) -> None:
    """enter 持久化 session 到文件。"""
    await mgr.create("persist-test", "HEAD", manual=True)
    await mgr.enter("persist-test")

    # 读 session 文件
    from suisuicode.worktree.session import load_session

    loaded = load_session(mgr.session_file)
    assert loaded is not None
    assert loaded.worktree_name == "persist-test"

    await mgr.remove("persist-test")


@pytest.mark.asyncio
async def test_enter_nonexistent(mgr: Manager) -> None:
    """enter 不存在的 worktree 抛异常。"""
    with pytest.raises(ValueError, match="不存在"):
        await mgr.enter("ghost")


# ── exit ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exit_keep(mgr: Manager) -> None:
    """exit action=KEEP 保留 worktree。"""
    await mgr.create("keep-me", "HEAD", manual=True)
    await mgr.enter("keep-me")
    report = await mgr.exit("keep-me", ExitAction.KEEP, ExitOptions())
    assert report.removed is False
    assert mgr.get("keep-me") is not None  # 仍在 active
    await mgr.remove("keep-me")


@pytest.mark.asyncio
async def test_exit_remove_with_discard(mgr: Manager) -> None:
    """exit action=REMOVE + discard 成功删除。"""
    await mgr.create("bye", "HEAD", manual=True)
    await mgr.enter("bye")
    report = await mgr.exit(
        "bye", ExitAction.REMOVE, ExitOptions(discard_changes=True),
    )
    assert report.removed is True
    assert mgr.get("bye") is None


@pytest.mark.asyncio
async def test_exit_remove_with_changes_blocked(mgr: Manager) -> None:
    """exit REMOVE 有变更时抛 WorktreeHasChangesError。"""
    await mgr.create("dirty", "HEAD", manual=True)
    await mgr.enter("dirty")

    # 制造变更
    (Path(mgr.get("dirty").path) / "README.md").write_text("# changed")

    with pytest.raises(WorktreeHasChangesError):
        await mgr.exit("dirty", ExitAction.REMOVE, ExitOptions())

    # Worktree 仍在
    assert mgr.get("dirty") is not None
    await mgr.remove("dirty", ExitOptions(discard_changes=True))


@pytest.mark.asyncio
async def test_exit_wrong_name(mgr: Manager) -> None:
    """只能退出当前 session 的 worktree。"""
    await mgr.create("a", "HEAD", manual=True)
    await mgr.create("b", "HEAD", manual=True)
    await mgr.enter("a")

    with pytest.raises(ValueError, match="只能退出当前"):
        await mgr.exit("b", ExitAction.KEEP)

    await mgr.remove("a")
    await mgr.remove("b")


# ── remove ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_non_current(mgr: Manager) -> None:
    """remove 允许删除非当前 session 的 worktree。"""
    await mgr.create("a", "HEAD", manual=True)
    await mgr.enter("a")
    await mgr.create("b", "HEAD", manual=True)

    # 删除 b（非当前 session）
    await mgr.remove("b", ExitOptions(discard_changes=True))
    assert mgr.get("b") is None
    assert mgr.get("a") is not None

    await mgr.remove("a")


@pytest.mark.asyncio
async def test_remove_clears_current_session(mgr: Manager) -> None:
    """remove 当前 session 的 worktree 时也清除 session。"""
    await mgr.create("sess", "HEAD", manual=True)
    await mgr.enter("sess")
    assert mgr.current_session is not None

    await mgr.remove("sess", ExitOptions(discard_changes=True))
    assert mgr.current_session is None
    assert mgr.get("sess") is None


# ── auto_cleanup ───────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_cleanup_manual_kept(mgr: Manager) -> None:
    """manual=True 直接 kept。"""
    await mgr.create("manual-wt", "HEAD", manual=True)
    report = await mgr.auto_cleanup("manual-wt")
    assert report.kept is True
    assert report.path != ""
    assert mgr.get("manual-wt") is not None
    await mgr.remove("manual-wt")


@pytest.mark.asyncio
async def test_auto_cleanup_auto_no_changes(mgr: Manager) -> None:
    """manual=False + 无变更 → 直接删除。"""
    await mgr.create("auto-clean", "HEAD", manual=False)
    report = await mgr.auto_cleanup("auto-clean")
    assert report.kept is False
    assert mgr.get("auto-clean") is None


@pytest.mark.asyncio
async def test_auto_cleanup_auto_with_changes(mgr: Manager) -> None:
    """manual=False + 有变更 → 保留。"""
    await mgr.create("auto-dirty", "HEAD", manual=False)
    (Path(mgr.get("auto-dirty").path) / "README.md").write_text("# changed")
    report = await mgr.auto_cleanup("auto-dirty")
    assert report.kept is True
    assert mgr.get("auto-dirty") is not None
    await mgr.remove("auto-dirty", ExitOptions(discard_changes=True))
