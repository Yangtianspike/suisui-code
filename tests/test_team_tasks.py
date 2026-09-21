"""共享任务 Store 单测 — CRUD + 依赖关系。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from suisuicode.team.tasks import (
    Filter,
    Patch,
    Status,
    Store,
    Task,
)


@pytest.fixture
def store_path() -> str:
    with tempfile.TemporaryDirectory() as d:
        yield str(Path(d) / "tasks.json")


@pytest.fixture
def store(store_path: str) -> Store:
    return Store(store_path)


class TestCreate:
    """创建任务。"""

    @pytest.mark.asyncio
    async def test_create_returns_id(self, store: Store) -> None:
        t = Task(id="", title="test task")
        tid = await store.create(t)
        assert tid.startswith("task_")
        assert len(tid) == 11  # "task_" + 6 hex

    @pytest.mark.asyncio
    async def test_create_and_get(self, store: Store) -> None:
        t = Task(id="", title="test task", description="desc")
        tid = await store.create(t)
        got = await store.get(tid)
        assert got is not None
        assert got.title == "test task"
        assert got.description == "desc"
        assert got.status == Status.PENDING

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, store: Store) -> None:
        got = await store.get("task_nonexistent")
        assert got is None


class TestList:
    """list_ 过滤 + is_ready 计算。"""

    @pytest.mark.asyncio
    async def test_list_all(self, store: Store) -> None:
        await store.create(Task(id="", title="t1"))
        await store.create(Task(id="", title="t2"))
        tasks = await store.list_()
        assert len(tasks) == 2

    @pytest.mark.asyncio
    async def test_filter_by_status(self, store: Store) -> None:
        t1 = Task(id="", title="t1", status=Status.PENDING)
        t2 = Task(id="", title="t2", status=Status.COMPLETED)
        await store.create(t1)
        tid2 = await store.create(t2)

        # 标记完成
        await store.update(tid2, Patch(status=Status.COMPLETED))

        pending = await store.list_(Filter(status=Status.PENDING))
        assert len(pending) == 1
        assert pending[0].title == "t1"

    @pytest.mark.asyncio
    async def test_is_ready(self, store: Store) -> None:
        """is_ready: blocked_by 中所有任务 completed 才为 true。"""
        blocker_id = await store.create(Task(id="", title="blocker"))
        blocked_id = await store.create(Task(
            id="", title="blocked",
            blocked_by=[blocker_id],
        ))

        tasks = await store.list_()
        for t in tasks:
            if t.id == blocked_id:
                assert not t.is_ready  # blocker 还没完成
                break

        # 完成 blocker
        await store.update(blocker_id, Patch(status=Status.COMPLETED))

        tasks = await store.list_()
        for t in tasks:
            if t.id == blocked_id:
                assert t.is_ready  # 现在应该 ready
                break


class TestUpdate:
    """更新任务 + 依赖双向维护。"""

    @pytest.mark.asyncio
    async def test_update_fields(self, store: Store) -> None:
        tid = await store.create(Task(id="", title="original"))
        await store.update(tid, Patch(
            title="updated",
            status=Status.IN_PROGRESS,
            assignee="alice",
        ))
        t = await store.get(tid)
        assert t is not None
        assert t.title == "updated"
        assert t.status == Status.IN_PROGRESS
        assert t.assignee == "alice"

    @pytest.mark.asyncio
    async def test_bidirectional_block(self, store: Store) -> None:
        """add_blocked_by 自动维护双向依赖。"""
        a_id = await store.create(Task(id="", title="A"))
        b_id = await store.create(Task(id="", title="B"))

        # A 依赖 B：B 阻塞 A
        await store.update(a_id, Patch(add_blocked_by=[b_id]))

        a = await store.get(a_id)
        b = await store.get(b_id)
        assert a is not None and b is not None
        assert b_id in a.blocked_by
        assert a_id in b.blocks  # 双向

    @pytest.mark.asyncio
    async def test_remove_blocked_by(self, store: Store) -> None:
        a_id = await store.create(Task(id="", title="A"))
        b_id = await store.create(Task(id="", title="B"))
        await store.update(a_id, Patch(add_blocked_by=[b_id]))
        await store.update(a_id, Patch(remove_blocked_by=[b_id]))

        a = await store.get(a_id)
        b = await store.get(b_id)
        assert b_id not in a.blocked_by
        assert a_id not in b.blocks

    @pytest.mark.asyncio
    async def test_update_nonexistent(self, store: Store) -> None:
        with pytest.raises(LookupError):
            await store.update("task_nonexistent", Patch(title="x"))
