"""Git helper 单测：_run_git / _has_worktree_changes / _resolve_head_sha_from_fs。

需要真实 git 仓库 — 用 pytest fixtures 在临时目录创建。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from suisuicode.worktree.git import (
    _has_worktree_changes,
    _resolve_head_sha_from_fs,
    _run_git,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """创建临时 git 仓库并返回路径。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True,
                   capture_output=True)
    # 设置 git user（避免 commit 失败）
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo,
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo,
                   check=True, capture_output=True)
    # 创建初始 commit
    (repo / "README.md").write_text("# test")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True,
                   capture_output=True)
    return repo


@pytest.fixture
def git_repo_with_worktree(git_repo: Path, tmp_path: Path) -> tuple[Path, Path]:
    """创建含 Worktree 的临时 git 仓库，返回 (repo_path, wt_path)。"""
    wt_path = tmp_path / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-B", "worktree-test", str(wt_path), "main"],
        cwd=git_repo, check=True, capture_output=True,
    )
    return git_repo, wt_path


# ── _run_git ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_git_success(git_repo: Path) -> None:
    """_run_git 在 git 仓库执行成功。"""
    out = await _run_git(str(git_repo), "rev-parse", "--abbrev-ref", "HEAD")
    assert out == "main"


@pytest.mark.asyncio
async def test_run_git_env_vars(git_repo: Path) -> None:
    """_run_git 设置 GIT_TERMINAL_PROMPT=0 + GIT_ASKPASS=""。"""
    out = await _run_git(str(git_repo), "rev-parse", "HEAD")
    assert len(out) == 40
    assert out.isalnum()


@pytest.mark.asyncio
async def test_run_git_failure(git_repo: Path) -> None:
    """_run_git 执行失败时抛 RuntimeError。"""
    with pytest.raises(RuntimeError):
        await _run_git(str(git_repo), "nonexistent-command")


# ── _has_worktree_changes ─────────────────────────────────


@pytest.mark.asyncio
async def test_has_changes_clean(git_repo: Path) -> None:
    """干净工作区返回 False。"""
    head = await _run_git(str(git_repo), "rev-parse", "HEAD")
    assert await _has_worktree_changes(str(git_repo), head) is False


@pytest.mark.asyncio
async def test_has_changes_dirty(git_repo: Path) -> None:
    """有未提交修改时返回 True。"""
    head = await _run_git(str(git_repo), "rev-parse", "HEAD")
    (git_repo / "README.md").write_text("# modified")
    assert await _has_worktree_changes(str(git_repo), head) is True


@pytest.mark.asyncio
async def test_has_changes_new_commit(git_repo: Path) -> None:
    """有本地新 commit 时返回 True。"""
    head = await _run_git(str(git_repo), "rev-parse", "HEAD")
    (git_repo / "new.txt").write_text("data")
    subprocess.run(["git", "add", "new.txt"], cwd=git_repo, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "new"], cwd=git_repo, check=True,
                   capture_output=True)
    assert await _has_worktree_changes(str(git_repo), head) is True


@pytest.mark.asyncio
async def test_has_changes_fail_closed() -> None:
    """非 git 目录 → fail-closed 返回 True（不抛异常）。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        assert await _has_worktree_changes(td, "HEAD") is True


# ── _resolve_head_sha_from_fs ─────────────────────────────


def test_resolve_head_sha_from_fs_returns_sha(
    git_repo_with_worktree: tuple[Path, Path],
) -> None:
    """从真实 Worktree 读 HEAD SHA 且长度=40。"""
    _, wt_path = git_repo_with_worktree
    sha = _resolve_head_sha_from_fs(str(wt_path))
    assert sha is not None
    assert len(sha) == 40
    assert sha.isalnum()


def test_resolve_head_sha_from_fs_nonexistent(tmp_path: Path) -> None:
    """不存在的目录返回 None。"""
    assert _resolve_head_sha_from_fs(str(tmp_path / "nope")) is None


def test_resolve_head_sha_from_fs_plain_dir(tmp_path: Path) -> None:
    """普通目录（无 .git）返回 None。"""
    d = tmp_path / "plain"
    d.mkdir()
    assert _resolve_head_sha_from_fs(str(d)) is None
