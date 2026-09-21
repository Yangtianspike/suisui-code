"""Team Manager 单测 — create/get/delete 基本流程。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from suisuicode.team.manager import Manager
from suisuicode.team.persistence import sanitize
from suisuicode.team.types import (
    BackendType,
    TeamHasActiveMembersError,
    TeamNotFoundError,
    TeammateInfo,
)


class TestSanitize:
    """sanitize 函数。"""

    def test_simple(self) -> None:
        assert sanitize("hello") == "hello"

    def test_spaces(self) -> None:
        assert sanitize("foo bar/baz") == "foo-bar-baz"

    def test_chinese(self) -> None:
        assert sanitize("重构 auth") == "auth"

    def test_empty(self) -> None:
        assert sanitize("") == ""

    def test_special_chars(self) -> None:
        assert sanitize("a@b#c$d") == "a-b-c-d"

    def test_leading_trailing_dash(self) -> None:
        assert sanitize("-hello-") == "hello"


class TestManager:
    """Manager 基本流程。"""

    @pytest.fixture
    def tmp_home(self) -> str:
        with tempfile.TemporaryDirectory() as d:
            yield d

    @pytest.fixture
    def tmp_project(self) -> str:
        with tempfile.TemporaryDirectory() as d:
            yield d

    @pytest.fixture
    def mgr(self, tmp_home: str, tmp_project: str) -> Manager:
        return Manager(
            home_dir=tmp_home,
            project_root=tmp_project,
            wt_mgr=None,
            task_mgr=None,
            registry=None,
        )

    @pytest.mark.asyncio
    async def test_create_team(self, mgr: Manager, tmp_home: str) -> None:
        team = await mgr.create("demo", "a test team")
        assert team.sanitized_name == "demo"
        assert team.description == "a test team"
        assert team.backend == BackendType.IN_PROCESS
        assert len(team.members) == 1  # lead
        assert team.members[0].name == "lead"

        # 检查 config.json 落地
        config_path = Path(tmp_home) / ".suisuicode" / "teams" / "demo" / "config.json"
        assert config_path.exists()
        raw = json.loads(config_path.read_text())
        assert raw["sanitized_name"] == "demo"

    @pytest.mark.asyncio
    async def test_create_sanitized_name(self, mgr: Manager, tmp_home: str) -> None:
        team = await mgr.create("foo bar/baz", "")
        assert team.sanitized_name == "foo-bar-baz"

        config_dir = Path(tmp_home) / ".suisuicode" / "teams" / "foo-bar-baz"
        assert config_dir.exists()

    @pytest.mark.asyncio
    async def test_create_duplicate_suffix(self, mgr: Manager) -> None:
        t1 = await mgr.create("demo", "")
        assert t1.sanitized_name == "demo"

        t2 = await mgr.create("demo", "")
        assert t2.sanitized_name == "demo-2"

    @pytest.mark.asyncio
    async def test_get(self, mgr: Manager) -> None:
        await mgr.create("demo", "")
        team = mgr.get("demo")
        assert team is not None
        assert team.sanitized_name == "demo"

        assert mgr.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_list(self, mgr: Manager) -> None:
        await mgr.create("a-team", "")
        await mgr.create("b-team", "")
        teams = mgr.list_()
        assert len(teams) == 2

    @pytest.mark.asyncio
    async def test_delete(self, mgr: Manager, tmp_home: str) -> None:
        await mgr.create("demo", "")
        await mgr.delete("demo", force=True)

        assert mgr.get("demo") is None
        config_dir = Path(tmp_home) / ".suisuicode" / "teams" / "demo"
        assert not config_dir.exists()

    @pytest.mark.asyncio
    async def test_delete_not_found(self, mgr: Manager) -> None:
        with pytest.raises(TeamNotFoundError):
            await mgr.delete("nonexistent")

    @pytest.mark.asyncio
    async def test_delete_active_members(self, mgr: Manager) -> None:
        team = await mgr.create("demo", "")
        # 添加一个活跃队员
        await mgr.add_member("demo", TeammateInfo(
            name="alice",
            agent_id="agent-1",
            is_active=True,
        ))
        with pytest.raises(TeamHasActiveMembersError):
            await mgr.delete("demo", force=False)

        # force=True 应该成功
        await mgr.delete("demo", force=True)
        assert mgr.get("demo") is None

    @pytest.mark.asyncio
    async def test_add_member(self, mgr: Manager) -> None:
        await mgr.create("demo", "")
        await mgr.add_member("demo", TeammateInfo(
            name="alice",
            agent_id="agent-1",
            backend_type=BackendType.IN_PROCESS,
        ))
        team = mgr.get("demo")
        assert team is not None
        alice = team.member_by_name("alice")
        assert alice is not None
        assert alice.agent_id == "agent-1"

    @pytest.mark.asyncio
    async def test_set_member_active(self, mgr: Manager) -> None:
        await mgr.create("demo", "")
        await mgr.add_member("demo", TeammateInfo(
            name="alice", agent_id="agent-1", is_active=True,
        ))
        await mgr.set_member_active("demo", "alice", False)

        team = mgr.get("demo")
        alice = team.member_by_name("alice")
        assert alice is not None
        assert alice.is_active is False

    @pytest.mark.asyncio
    async def test_remove_member(self, mgr: Manager) -> None:
        await mgr.create("demo", "")
        await mgr.add_member("demo", TeammateInfo(
            name="alice", agent_id="agent-1",
        ))
        await mgr.remove_member("demo", "alice")

        team = mgr.get("demo")
        assert team.member_by_name("alice") is None

    @pytest.mark.asyncio
    async def test_scan_existing(self, tmp_home: str, tmp_project: str) -> None:
        """启动扫描还原已有 Team。"""
        # 先创建一个 mgr 并建 Team
        mgr1 = Manager(tmp_home, tmp_project)
        await mgr1.create("demo", "test")

        # 新建第二个 mgr（模拟重启）
        mgr2 = Manager(tmp_home, tmp_project)
        team = mgr2.get("demo")
        assert team is not None
        assert team.sanitized_name == "demo"
        assert team.description == "test"
