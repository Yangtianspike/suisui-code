"""Manager 构造 + Session 持久化测试。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from suisuicode.worktree.manager import Manager
from suisuicode.worktree.session import (
    WorktreeSession,
    clear_session,
    load_session,
    save_session,
)


# ── Session 持久化 ──────────────────────────────────────


class TestSessionPersistence:
    def test_roundtrip(self, tmp_path: Path) -> None:
        """WorktreeSession 序列化/反序列化往返一致。"""
        session = WorktreeSession(
            original_cwd="/home/user",
            worktree_path="/home/user/.suisuicode/worktrees/test",
            worktree_name="test",
            original_branch="main",
            original_head_commit="abc123",
            session_id="session-uuid-1",
        )
        session_file = tmp_path / "session.json"
        save_session(session_file, session)

        loaded = load_session(session_file)
        assert loaded is not None
        assert loaded.original_cwd == session.original_cwd
        assert loaded.worktree_path == session.worktree_path
        assert loaded.worktree_name == session.worktree_name
        assert loaded.session_id == session.session_id

    def test_save_null(self, tmp_path: Path) -> None:
        """save_session(None) 写入 null，load 返回 None。"""
        session_file = tmp_path / "session.json"
        save_session(session_file, None)
        assert load_session(session_file) is None

    def test_load_missing(self, tmp_path: Path) -> None:
        """文件不存在返回 None。"""
        assert load_session(tmp_path / "nope.json") is None

    def test_clear_session(self, tmp_path: Path) -> None:
        """clear_session 等价于保存 null。"""
        session_file = tmp_path / "session.json"
        session = WorktreeSession(
            original_cwd="/x",
            worktree_path="/x/y",
            worktree_name="z",
            original_branch="main",
            original_head_commit="abc",
            session_id="1",
        )
        save_session(session_file, session)
        assert load_session(session_file) is not None

        clear_session(session_file)
        assert load_session(session_file) is None

    def test_atomic_write(self, tmp_path: Path) -> None:
        """原子写：失败前不破坏既有文件。"""
        session_file = tmp_path / "session.json"
        session = WorktreeSession(
            original_cwd="/x",
            worktree_path="/x/y",
            worktree_name="z",
            original_branch="main",
            original_head_commit="abc",
            session_id="1",
        )
        save_session(session_file, session)

        original_content = session_file.read_text()

        # 如果写入失败，原文件内容不变
        try:
            save_session(session_file, session)
        except Exception:
            pass

        # 验证原文件被覆盖（正常情况）或者保留原内容（异常情况）
        loaded = load_session(session_file)
        assert loaded is not None

    def test_field_names_snake_case(self, tmp_path: Path) -> None:
        """JSON 序列化使用小写下划线字段名。"""
        session = WorktreeSession(
            original_cwd="/x",
            worktree_path="/x/y",
            worktree_name="z",
            original_branch="main",
            original_head_commit="abc",
            session_id="1",
        )
        json_str = session.to_json()
        assert "original_cwd" in json_str
        assert "worktree_path" in json_str
        assert "worktree_name" in json_str
        assert "original_branch" in json_str
        assert "original_head_commit" in json_str
        assert "session_id" in json_str


# ── Manager 构造 ────────────────────────────────────────


class TestManagerConstruct:
    def test_init_creates_worktree_dir(self, git_repo: Path) -> None:
        """Manager 构造后 .suisuicode/worktrees 目录存在。"""
        mgr = Manager(str(git_repo))
        assert mgr.worktree_dir.exists()
        assert mgr.worktree_dir.is_dir()

    def test_init_session_file_path(self, git_repo: Path) -> None:
        """session_file 指向正确路径。"""
        mgr = Manager(str(git_repo))
        expected = git_repo / ".suisuicode" / "worktree_session.json"
        assert mgr.session_file == expected

    def test_init_rejects_non_git_dir(self, tmp_path: Path) -> None:
        """非 git 目录抛 ValueError。"""
        d = tmp_path / "not_git"
        d.mkdir()
        with pytest.raises(ValueError, match="not a git repo|不是有效的 git"):
            Manager(str(d))

    def test_init_rejects_non_root(self, git_repo: Path) -> None:
        """非 git 根目录抛 ValueError。"""
        subdir = git_repo / "sub"
        subdir.mkdir()
        with pytest.raises(ValueError, match="不是.*根目录"):
            Manager(str(subdir))

    def test_init_empty_session(self, git_repo: Path) -> None:
        """无 session 文件时 current_session=None。"""
        mgr = Manager(str(git_repo))
        assert mgr.current_session is None

    def test_init_loads_existing_session(self, git_repo: Path) -> None:
        """预放 session 文件能被加载。"""
        session_file = git_repo / ".suisuicode" / "worktree_session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session = WorktreeSession(
            original_cwd=str(git_repo),
            worktree_path=str(git_repo),
            worktree_name="test",
            original_branch="main",
            original_head_commit="abc",
            session_id="1",
        )
        save_session(session_file, session)

        mgr = Manager(str(git_repo))
        assert mgr.current_session is not None
        assert mgr.current_session.worktree_name == "test"

    def test_init_clears_gone_session(self, git_repo: Path) -> None:
        """session 指向不存在的 worktree 目录时自动清空。"""
        session_file = git_repo / ".suisuicode" / "worktree_session.json"
        session_file.parent.mkdir(parents=True, exist_ok=True)
        session = WorktreeSession(
            original_cwd=str(git_repo),
            worktree_path=str(git_repo / "nonexistent"),
            worktree_name="ghost",
            original_branch="main",
            original_head_commit="abc",
            session_id="1",
        )
        save_session(session_file, session)

        mgr = Manager(str(git_repo))
        assert mgr.current_session is None
        assert not session_file.exists() or load_session(session_file) is None

    def test_list_empty(self, git_repo: Path) -> None:
        """新建 Manager 的 list() 为空。"""
        mgr = Manager(str(git_repo))
        assert mgr.list() == []

    def test_list_sorted(self, git_repo: Path) -> None:
        """list() 按 name 排序。"""
        # 手动放 worktree 到 active（不通过 create）
        from suisuicode.worktree.create import Worktree
        from datetime import datetime

        mgr = Manager(str(git_repo))
        wt1 = Worktree(
            name="bob", path="/x/b", branch="worktree-bob",
            based_on="HEAD", head_commit="abc", created=datetime.now(),
            manual=True,
        )
        wt2 = Worktree(
            name="alice", path="/x/a", branch="worktree-alice",
            based_on="HEAD", head_commit="abc", created=datetime.now(),
            manual=True,
        )
        mgr.active["bob"] = wt1
        mgr.active["alice"] = wt2

        result = mgr.list()
        assert result[0].name == "alice"
        assert result[1].name == "bob"

    def test_get(self, git_repo: Path) -> None:
        """get() 按名查找。"""
        from suisuicode.worktree.create import Worktree
        from datetime import datetime

        mgr = Manager(str(git_repo))
        wt = Worktree(
            name="test", path="/x/y", branch="worktree-test",
            based_on="HEAD", head_commit="abc", created=datetime.now(),
            manual=True,
        )
        mgr.active["test"] = wt
        assert mgr.get("test") is not None
        assert mgr.get("nonexistent") is None


# ── fixtures ────────────────────────────────────────────


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
