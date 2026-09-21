"""Manager.create + 快速恢复 + 创建后设置测试。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from suisuicode.worktree import Manager
from suisuicode.worktree.lifecycle import ExitOptions


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


# ── create 测试 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_simple(mgr: Manager, git_repo: Path) -> None:
    """基本 create：目录落地 + 分支正确。"""
    wt = await mgr.create("alice", "HEAD", manual=True)
    assert wt.name == "alice"
    assert wt.manual is True
    assert wt.branch == "worktree-alice"

    wt_path = Path(wt.path)
    assert wt_path.exists()
    assert wt_path.is_dir()
    assert (wt_path / "README.md").exists()

    # Mgmt active
    assert mgr.get("alice") is not None

    # Done
    await mgr.remove("alice", ExitOptions(discard_changes=True))


@pytest.mark.asyncio
async def test_create_nested_slug(mgr: Manager) -> None:
    """嵌套 slug：team/alice 落地 team+alice 目录 + worktree-team+alice 分支。"""
    wt = await mgr.create("team/alice", "HEAD", manual=True)
    assert wt.branch == "worktree-team+alice"
    assert "team+alice" in wt.path or "team+alice" in Path(wt.path).name
    await mgr.remove("team/alice", ExitOptions(discard_changes=True))


@pytest.mark.asyncio
async def test_create_duplicate_name(mgr: Manager) -> None:
    """同一 name 重复 create 抛异常。"""
    await mgr.create("dup", "HEAD", manual=True)
    with pytest.raises(ValueError, match="已存在"):
        await mgr.create("dup", "HEAD", manual=True)
    await mgr.remove("dup", ExitOptions(discard_changes=True))


@pytest.mark.asyncio
async def test_fast_recovery(mgr: Manager, git_repo: Path) -> None:
    """快速恢复：创建后删除 active 条目但不删目录，再次 create 走 fs 恢复路径。"""
    wt1 = await mgr.create("fast", "HEAD", manual=True)
    wt_path = wt1.path
    assert Path(wt_path).exists()

    # 从 active 删除（模拟进程重启）
    del mgr.active["fast"]

    # 再次 create → 走快速恢复（因为 wt_path.exists()）
    assert Path(wt_path).exists()

    wt2 = await mgr.create("fast", "HEAD", manual=True)
    assert wt2.path == wt_path
    assert mgr.get("fast") is not None

    await mgr.remove("fast", ExitOptions(discard_changes=True))


# ── Setup A ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_a_copy_config(mgr: Manager, git_repo: Path) -> None:
    """设置 A：主仓库 settings.local.yaml 被复制到 Worktree。"""
    cfg_dir = git_repo / ".suisuicode"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "settings.local.yaml").write_text("key: value")
    (cfg_dir / "config.yaml").write_text("providers: []")

    wt = await mgr.create("with-config", "HEAD", manual=True)
    wt_path = Path(wt.path)

    dst_cfg = wt_path / ".suisuicode" / "settings.local.yaml"
    assert dst_cfg.exists()
    assert dst_cfg.read_text() == "key: value"

    dst_main = wt_path / ".suisuicode" / "config.yaml"
    assert dst_main.exists()
    assert dst_main.read_text() == "providers: []"

    await mgr.remove("with-config", ExitOptions(discard_changes=True))


# ── Setup B ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_b_hooks_path(mgr: Manager, git_repo: Path) -> None:
    """设置 B：主仓库 .husky/ 存在时 Worktree core.hooksPath 已设置。"""
    (git_repo / ".husky").mkdir()
    (git_repo / ".husky" / "pre-commit").write_text("#!/bin/sh\necho hi")

    wt = await mgr.create("with-hooks", "HEAD", manual=True)

    # 读取 Worktree 的 core.hooksPath
    result = subprocess.run(
        ["git", "-C", wt.path, "config", "--get", "core.hooksPath"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    hooks_path = result.stdout.strip()
    assert ".husky" in hooks_path

    await mgr.remove("with-hooks", ExitOptions(discard_changes=True))


# ── Setup C ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_c_symlink(mgr: Manager, git_repo: Path) -> None:
    """设置 C：主仓库有 node_modules 时 Worktree 内为软链。"""
    (git_repo / "node_modules").mkdir()
    (git_repo / "node_modules" / "dummy").write_text("data")

    wt = await mgr.create("with-symlink", "HEAD", manual=True)
    link = Path(wt.path) / "node_modules"

    if os.name != "nt":  # POSIX symlink
        assert link.is_symlink()
    # On Windows, os.symlink may fail silently in the best-effort setup.
    # We accept that the link may not exist.

    await mgr.remove("with-symlink", ExitOptions(discard_changes=True))


# ── Setup D ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_setup_d_include_ignored(mgr: Manager, git_repo: Path) -> None:
    """设置 D：.worktreeinclude 模式命中的 ignored 文件被复制到 Worktree。"""
    # 创建 .worktreeinclude
    (git_repo / ".worktreeinclude").write_text("*.env\n")

    # 创建 .env 文件但让它被 gitignore
    (git_repo / ".env").write_text("SECRET=test")
    (git_repo / ".gitignore").write_text(".env\n")

    wt = await mgr.create("with-include", "HEAD", manual=True)
    env_file = Path(wt.path) / ".env"
    assert env_file.exists()
    assert env_file.read_text() == "SECRET=test"

    await mgr.remove("with-include", ExitOptions(discard_changes=True))
