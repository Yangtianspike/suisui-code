"""协作工具单测。"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from suisuicode.team.manager import Manager
from suisuicode.team.tools.team_create import TeamCreateTool
from suisuicode.team.tools.team_delete import TeamDeleteTool
from suisuicode.team.tools.task_create import TaskCreateTool
from suisuicode.team.tools.task_get import TaskGetTool
from suisuicode.team.tools.task_list import TaskListTool
from suisuicode.team.tools.task_update import TaskUpdateTool
from suisuicode.team.tools.send_message import SendMessageTool
from suisuicode.team.tasks import Store, Status


class TestTeamCreateTool:
    """TeamCreate 工具。"""

    @pytest.fixture
    def mgr(self, tmp_path: Path) -> Manager:
        return Manager(
            home_dir=str(tmp_path / "home"),
            project_root=str(tmp_path / "project"),
        )

    @pytest.mark.asyncio
    async def test_create(self, mgr: Manager) -> None:
        tool = TeamCreateTool(mgr)
        result = await tool.execute(json.dumps({"team_name": "test-team"}))
        assert not result.is_error
        data = json.loads(result.content)
        assert data["team_name"] == "test-team"
        assert "config_path" in data

    @pytest.mark.asyncio
    async def test_missing_name(self, mgr: Manager) -> None:
        tool = TeamCreateTool(mgr)
        result = await tool.execute(json.dumps({}))
        assert result.is_error


class TestTeamDeleteTool:
    """TeamDelete 工具。"""

    @pytest.fixture
    def mgr(self, tmp_path: Path) -> Manager:
        return Manager(
            home_dir=str(tmp_path / "home"),
            project_root=str(tmp_path / "project"),
        )

    @pytest.mark.asyncio
    async def test_delete(self, mgr: Manager) -> None:
        await mgr.create("test-team", "")
        tool = TeamDeleteTool(mgr)
        result = await tool.execute(json.dumps({
            "team_name": "test-team",
            "force": True,
        }))
        assert not result.is_error


class TestTaskTools:
    """Task CRUD 工具。"""

    @pytest.fixture
    def store(self, tmp_path: Path) -> Store:
        p = str(tmp_path / "tasks.json")
        return Store(p)

    def _getter(self, store: Store):
        return lambda: store

    @pytest.mark.asyncio
    async def test_create_task(self, store: Store) -> None:
        tool = TaskCreateTool(self._getter(store))
        result = await tool.execute(json.dumps({"title": "my task"}))
        assert not result.is_error
        data = json.loads(result.content)
        assert data["task_id"].startswith("task_")
        assert data["title"] == "my task"

    @pytest.mark.asyncio
    async def test_get_task(self, store: Store) -> None:
        create = TaskCreateTool(self._getter(store))
        r = await create.execute(json.dumps({"title": "find me"}))
        tid = json.loads(r.content)["task_id"]

        get = TaskGetTool(self._getter(store))
        r2 = await get.execute(json.dumps({"task_id": tid}))
        assert not r2.is_error
        data = json.loads(r2.content)
        assert data["title"] == "find me"

    @pytest.mark.asyncio
    async def test_list_tasks(self, store: Store) -> None:
        create = TaskCreateTool(self._getter(store))
        await create.execute(json.dumps({"title": "t1"}))
        await create.execute(json.dumps({"title": "t2"}))

        list_tool = TaskListTool(self._getter(store))
        r = await list_tool.execute(json.dumps({}))
        data = json.loads(r.content)
        assert len(data["tasks"]) == 2

    @pytest.mark.asyncio
    async def test_update_task(self, store: Store) -> None:
        create = TaskCreateTool(self._getter(store))
        r = await create.execute(json.dumps({"title": "old"}))
        tid = json.loads(r.content)["task_id"]

        update = TaskUpdateTool(self._getter(store))
        r2 = await update.execute(json.dumps({
            "task_id": tid,
            "title": "new",
            "status": "in_progress",
        }))
        assert not r2.is_error

        get = TaskGetTool(self._getter(store))
        r3 = await get.execute(json.dumps({"task_id": tid}))
        data = json.loads(r3.content)
        assert data["title"] == "new"
        assert data["status"] == "in_progress"


class TestSendMessageTool:
    """SendMessage 工具。"""

    @pytest.fixture
    def mgr(self, tmp_path: Path) -> Manager:
        return Manager(
            home_dir=str(tmp_path / "home"),
            project_root=str(tmp_path / "project"),
        )

    @pytest.mark.asyncio
    async def test_send_message(self, mgr: Manager) -> None:
        team = await mgr.create("test-team", "")
        tool = SendMessageTool(mgr)
        result = await tool.execute(json.dumps({
            "to": "*",
            "summary": "hello everyone",
            "message": "team meeting at 3pm",
        }))
        assert not result.is_error
        data = json.loads(result.content)
        assert "delivered_to" in data
