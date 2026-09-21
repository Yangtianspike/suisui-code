"""task.tools 测试：TaskList / TaskGet / TaskStop / SendMessage 工具。"""

import asyncio
import json
import pytest

from suisuicode.task.manager import Manager, Status
from suisuicode.task.tools import (
    TaskListTool,
    TaskGetTool,
    TaskStopTool,
    SendMessageTool,
)
from suisuicode.agent import Agent
from suisuicode.conversation import Conversation
from suisuicode.llm import Request, StreamEvent
from suisuicode.permission import Mode
from suisuicode.permission.engine import Engine
from suisuicode.permission.rule import RuleSet
from suisuicode.tool import Registry


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


def make_agent(texts=None):
    if texts is None:
        texts = ["ok"]
    prov = MockProvider(texts)
    reg = Registry()
    engine = Engine(
        root=".",
        user=RuleSet(),
        project=RuleSet(),
        local=RuleSet(),
        local_path="",
        start_mode=Mode.DEFAULT,
    )
    return Agent(prov, reg, engine, "test")


@pytest.mark.asyncio
async def test_task_list():
    mgr = Manager()
    tool = TaskListTool(mgr)

    # 先 launch 几个任务
    prov = MockProvider(["r1"])
    ag = make_agent()
    await mgr.launch(ag, Conversation(), "t1", "task1")

    done_q = mgr.subscribe_done()
    await asyncio.wait_for(done_q.get(), timeout=5.0)

    result = await tool.execute("{}")
    items = json.loads(result.content)
    assert isinstance(items, list)
    assert len(items) >= 1
    assert items[0]["name"] == "t1"
    assert "status" in items[0]


@pytest.mark.asyncio
async def test_task_get_found():
    mgr = Manager()
    tool = TaskGetTool(mgr)

    prov = MockProvider(["result-text"])
    ag = make_agent(["result-text"])
    task_id = await mgr.launch(ag, Conversation(), "tg1", "task")

    done_q = mgr.subscribe_done()
    await asyncio.wait_for(done_q.get(), timeout=5.0)

    result = await tool.execute(json.dumps({"task_id": task_id}))
    data = json.loads(result.content)
    assert data["name"] == "tg1"
    assert data["status"] == "completed"
    assert "result-text" in data["result"]


@pytest.mark.asyncio
async def test_task_get_not_found():
    mgr = Manager()
    tool = TaskGetTool(mgr)

    result = await tool.execute(json.dumps({"task_id": "nonexistent"}))
    assert result.is_error is True
    assert "not found" in result.content


@pytest.mark.asyncio
async def test_task_stop():
    mgr = Manager()

    class SlowProvider:
        model = "mock"
        name = "mock"

        async def stream(self, req: Request):
            await asyncio.sleep(10)
            yield StreamEvent(text="slow", done=True)

    ag = make_agent()
    task_id = await mgr.launch(ag, Conversation(), "ts1", "slow")

    tool = TaskStopTool(mgr)
    result = await tool.execute(json.dumps({"task_id": task_id}))
    data = json.loads(result.content)
    assert data["status"] == "cancellation_requested"


@pytest.mark.asyncio
async def test_task_stop_not_found():
    mgr = Manager()
    tool = TaskStopTool(mgr)

    result = await tool.execute(json.dumps({"task_id": "nonexistent"}))
    assert result.is_error is True


@pytest.mark.asyncio
async def test_send_message_ok():
    mgr = Manager()
    ag = make_agent(["first"])
    task_id = await mgr.launch(ag, Conversation(), "sm1", "first task")

    done_q = mgr.subscribe_done()
    await asyncio.wait_for(done_q.get(), timeout=5.0)

    # 找到并更新 agent 的 provider
    bt = mgr.get(task_id)
    bt.sub_agent._provider._texts = ["second"]
    bt.sub_agent._provider._round = 0

    tool = SendMessageTool(mgr)
    result = await tool.execute(json.dumps({"name": "sm1", "message": "second task"}))
    data = json.loads(result.content)
    assert data["status"] == "resumed"
    assert "task_id" in data


@pytest.mark.asyncio
async def test_send_message_missing_name():
    mgr = Manager()
    tool = SendMessageTool(mgr)

    result = await tool.execute(json.dumps({"name": "", "message": "hi"}))
    assert result.is_error is True


def test_tool_is_system():
    mgr = Manager()
    assert TaskListTool(mgr).is_system_tool is True
    assert TaskGetTool(mgr).is_system_tool is True
    assert TaskStopTool(mgr).is_system_tool is True
    assert SendMessageTool(mgr).is_system_tool is True
