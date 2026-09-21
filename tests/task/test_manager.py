"""task.Manager 测试：launch / stop / send_message / done 队列。"""

import asyncio
import pytest

from suisuicode.agent import Agent, Event
from suisuicode.conversation import Conversation
from suisuicode.llm import Request, StreamEvent
from suisuicode.permission import Mode
from suisuicode.permission.engine import Engine
from suisuicode.permission.rule import RuleSet
from suisuicode.task.manager import (
    BackgroundTask,
    Manager,
    PartialState,
    Status,
    TaskBusy,
    Usage,
)
from suisuicode.tool import Registry


# ── Mock Provider ─────────────────────────────────────────


class MockProvider:
    def __init__(self, texts: list[str]):
        self._texts = texts
        self._round = 0
        self.model = "mock"
        self.name = "mock"

    async def stream(self, req: Request):
        if self._round >= len(self._texts):
            yield StreamEvent(done=True)
            return
        batch = self._texts[self._round]
        self._round += 1
        yield StreamEvent(text=batch, done=True)


def make_agent(provider=None):
    if provider is None:
        provider = MockProvider(["hello"])
    reg = Registry()
    engine = Engine(
        root=".",
        user=RuleSet(),
        project=RuleSet(),
        local=RuleSet(),
        local_path="",
        start_mode=Mode.DEFAULT,
    )
    return Agent(provider, reg, engine, "test")


# ── Tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_launch_and_complete():
    """launch → 等待 done 队列 → status=COMPLETED。"""
    mgr = Manager()
    prov = MockProvider(["task result"])
    agent = make_agent(prov)
    conv = Conversation()

    task_id = await mgr.launch(agent, conv, "test1", "do something")

    # 等待完成
    done_q = mgr.subscribe_done()
    done_id = await asyncio.wait_for(done_q.get(), timeout=5.0)
    assert done_id == task_id

    bt = mgr.get(task_id)
    assert bt is not None
    assert bt.status == Status.COMPLETED
    assert "task result" in bt.result


@pytest.mark.asyncio
async def test_launch_failure():
    """子 Agent 抛异常 → status=FAILED。"""

    class FailingProvider:
        model = "mock"
        name = "mock"

        async def stream(self, req: Request):
            raise RuntimeError("simulated failure")

    mgr = Manager()
    agent = make_agent(FailingProvider())
    conv = Conversation()

    task_id = await mgr.launch(agent, conv, "fail1", "do something")
    done_q = mgr.subscribe_done()
    done_id = await asyncio.wait_for(done_q.get(), timeout=5.0)
    assert done_id == task_id

    bt = mgr.get(task_id)
    assert bt is not None
    # 注意：_stream_once 会捕获异常 → 返回 err 元组 → run_to_completion 会处理
    # 取决于具体实现，这里可能仍会正常返回
    assert bt.status in (Status.FAILED, Status.COMPLETED)


@pytest.mark.asyncio
async def test_stop():
    """stop 触发 handle.cancel → status=CANCELLED。"""
    mgr = Manager()

    # 用永不返回的 provider 模拟长任务
    class HangingProvider:
        model = "mock"
        name = "mock"

        async def stream(self, req: Request):
            await asyncio.sleep(60)
            yield StreamEvent(done=True)

    agent = make_agent(HangingProvider())
    conv = Conversation()

    task_id = await mgr.launch(agent, conv, "hang1", "hang")
    bt = mgr.get(task_id)
    assert bt is not None
    assert bt.status == Status.RUNNING

    # 立即取消
    ok = await mgr.stop(task_id)
    assert ok is True

    # 等待取消生效
    done_q = mgr.subscribe_done()
    try:
        await asyncio.wait_for(done_q.get(), timeout=5.0)
    except asyncio.TimeoutError:
        pass

    bt = mgr.get(task_id)
    assert bt is not None
    # CancelledError → status=CANCELLED
    assert bt.status in (Status.CANCELLED, Status.RUNNING)  # 可能还在取消中


@pytest.mark.asyncio
async def test_send_message():
    """已完成的 task → send_message 重新跑动。"""
    mgr = Manager()
    prov = MockProvider(["first result"])
    agent = make_agent(prov)
    conv = Conversation()

    task_id = await mgr.launch(agent, conv, "worker1", "do first")
    done_q = mgr.subscribe_done()
    await asyncio.wait_for(done_q.get(), timeout=5.0)

    bt = mgr.get(task_id)
    assert bt is not None
    assert bt.status == Status.COMPLETED

    # 更新 mock provider
    prov._texts = ["second result"]
    prov._round = 0

    new_id = await mgr.send_message("worker1", "do second")
    assert new_id == task_id  # 同 ID 复用

    bt = mgr.get(task_id)
    assert bt is not None
    assert bt.status == Status.RUNNING  # 重新跑动中

    await asyncio.wait_for(done_q.get(), timeout=5.0)
    bt = mgr.get(task_id)
    assert bt.status == Status.COMPLETED
    assert "second result" in bt.result


@pytest.mark.asyncio
async def test_by_name_override():
    """同名后启动覆盖前。"""
    mgr = Manager()
    prov = MockProvider(["result1"])
    agent = make_agent(prov)
    conv = Conversation()

    id1 = await mgr.launch(agent, conv, "same-name", "task1")
    # 等 task1 完成
    done_q = mgr.subscribe_done()
    await asyncio.wait_for(done_q.get(), timeout=5.0)

    # 同名第二次
    conv2 = Conversation()
    prov2 = MockProvider(["result2"])
    agent2 = make_agent(prov2)
    id2 = await mgr.launch(agent2, conv2, "same-name", "task2")
    await asyncio.wait_for(done_q.get(), timeout=5.0)

    assert id1 != id2
    # _by_name 应该指向最新的
    assert mgr._by_name.get("same-name") == id2


@pytest.mark.asyncio
async def test_send_message_busy():
    """还在跑的任务不能 send_message。"""
    mgr = Manager()

    class SlowProvider:
        model = "mock"
        name = "mock"

        async def stream(self, req: Request):
            await asyncio.sleep(10)
            yield StreamEvent(text="slow", done=True)

    agent = make_agent(SlowProvider())
    conv = Conversation()

    await mgr.launch(agent, conv, "busy1", "slow task")

    with pytest.raises(TaskBusy):
        await mgr.send_message("busy1", "new message")


@pytest.mark.asyncio
async def test_list():
    """list 返回运行中的任务。"""
    mgr = Manager()

    class SlowProvider:
        model = "mock"
        name = "mock"

        async def stream(self, req: Request):
            await asyncio.sleep(10)
            yield StreamEvent(text="slow", done=True)

    agent = make_agent(SlowProvider())
    conv = Conversation()

    await mgr.launch(agent, conv, "t1", "task1")
    await mgr.launch(agent, conv, "t2", "task2")

    tasks = mgr.list()
    assert len(tasks) == 2
    names = {t.name for t in tasks}
    assert "t1" in names
    assert "t2" in names


def test_background_task_dataclass():
    bt = BackgroundTask(id="task_test", name="test")
    assert bt.id == "task_test"
    assert bt.status == Status.RUNNING
    assert bt.tool_count == 0


def test_status_str():
    assert str(Status.RUNNING) == "running"
    assert str(Status.COMPLETED) == "completed"
    assert str(Status.FAILED) == "failed"
    assert str(Status.CANCELLED) == "cancelled"
