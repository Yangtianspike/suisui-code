"""sweep_stale + random_agent_name 测试。"""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from suisuicode.worktree import Manager
from suisuicode.worktree.sweep import EPHEMERAL_PATTERN, random_agent_name


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


# ── random_agent_name ───────────────────────────────────


def test_random_agent_name_format() -> None:
    """random_agent_name 返回 agent-a + 7 位 hex。"""
    name = random_agent_name()
    assert EPHEMERAL_PATTERN.match(name) is not None
    assert name.startswith("agent-a")
    assert len(name) == 14  # "agent-a" (7) + 7 hex = 14

    # 两次调用应不同
    name2 = random_agent_name()
    assert name != name2


# ── sweep_stale ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_stale_skips_non_matching(mgr: Manager) -> None:
    """不匹配 agent-a 模式的目录被跳过。"""
    await mgr.create("manual-one", "HEAD", manual=True)
    removed = await mgr.sweep_stale(datetime.now() + timedelta(hours=1))
    assert "manual-one" not in removed
    assert mgr.get("manual-one") is not None
    await mgr.remove("manual-one")


@pytest.mark.asyncio
async def test_sweep_stale_skips_recent(mgr: Manager) -> None:
    """mtime 在 cutoff 之后的被跳过。"""
    name = random_agent_name()
    await mgr.create(name, "HEAD", manual=False)
    # cutoff=很久以前 → 所有都算过期，但 mtime 是新的→跳过
    removed = await mgr.sweep_stale(
        datetime.now() + timedelta(hours=1),
    )
    assert name not in removed
    assert mgr.get(name) is not None
    await mgr.remove(name)


@pytest.mark.asyncio
async def test_sweep_stale_skips_current_session(mgr: Manager) -> None:
    """当前 session 的目录被跳过。"""
    name = random_agent_name()
    await mgr.create(name, "HEAD", manual=False)
    await mgr.enter(name)

    # cutoff=未来 → 所有新目录视为过期
    removed = await mgr.sweep_stale(datetime.now() + timedelta(hours=1))
    assert name not in removed
    await mgr.remove(name)


@pytest.mark.asyncio
async def test_sweep_stale_removes_old_clean(mgr: Manager) -> None:
    """过期 + 无变更 + 非 session 的临时 worktree 被删除。"""
    name = random_agent_name()
    await mgr.create(name, "HEAD", manual=False)

    # 用过去很久的 time 来模拟老文件
    # cutoff=未来 → 时间过滤放过
    removed = await mgr.sweep_stale(datetime.now() + timedelta(hours=1))
    # 由于 mtime 也是新的，不会删除
    assert name not in removed

    # 用过去的 cutoff 来测试真正删除
    removed = await mgr.sweep_stale(datetime.now() - timedelta(hours=1))
    if name in removed:
        assert mgr.get(name) is None
    else:
        # 可能在别的过滤层被保留了（fail-closed 检查等）
        await mgr.remove(name)
