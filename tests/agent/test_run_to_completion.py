"""Agent.run_to_completion 测试：子 Agent 循环、max_turns、dont_ask、approval_upgrader。"""

import asyncio
import pytest

from suisuicode.agent import Agent
from suisuicode.agent.run_to_completion import MaxTurnsReached
from suisuicode.conversation import Conversation
from suisuicode.llm import Request, StreamEvent, Usage as LlmUsage
from suisuicode.permission import Mode, Outcome
from suisuicode.permission.engine import Engine
from suisuicode.permission.rule import RuleSet
from suisuicode.tool import Registry


# ── Mock Provider ─────────────────────────────────────────


class MockProvider:
    """可编程 Provider：按预设列表返回 StreamEvent。"""

    def __init__(self, events: list[list[StreamEvent]]):
        """
        Args:
            events: 每轮（一次 stream 调用）返回的事件列表。
                    每个元素是一轮的所有事件。
        """
        self._events = events
        self._round = 0
        self.model = "mock-model"
        self.name = "mock"

    async def stream(self, req: Request):
        if self._round >= len(self._events):
            yield StreamEvent(done=True)
            return
        # 先递增 round，再 yield——防止 _stream_once break 导致递增被跳过
        batch = self._events[self._round]
        self._round += 1
        for ev in batch:
            yield ev


def _text_ev(text: str) -> StreamEvent:
    return StreamEvent(text=text, done=True)


def _text_usage(text: str, inp: int = 100, out: int = 50) -> StreamEvent:
    return StreamEvent(text=text, usage=LlmUsage(input_tokens=inp, output_tokens=out,
                                                  cache_write=0, cache_read=0), done=True)


def _tool_call(tool_name: str, tool_id: str = "tc1") -> StreamEvent:
    from suisuicode.llm import ToolCall
    return StreamEvent(
        tool_calls=[ToolCall(id=tool_id, name=tool_name, input="{}")],
        done=True,
    )


def _tool_text_call(text: str, tool_name: str, tool_id: str = "tc1") -> StreamEvent:
    from suisuicode.llm import ToolCall
    return StreamEvent(
        text=text,
        tool_calls=[ToolCall(id=tool_id, name=tool_name, input="{}")],
        done=True,
    )


# ── Helpers ───────────────────────────────────────────────


def make_agent(provider, registry=None, **kwargs):
    """构造一个最小 Agent 实例。"""
    if registry is None:
        registry = Registry()
    engine = Engine(
        root=".",
        user=RuleSet(),
        project=RuleSet(),
        local=RuleSet(),
        local_path="",
        start_mode=Mode.DEFAULT,
    )
    return Agent(
        provider=provider,
        registry=registry,
        engine=engine,
        version="test",
        runtime=None,
        **kwargs,
    )


# ── Tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_simple_text_response():
    """一轮返回纯文本 → run_to_completion 返回该文本。"""
    prov = MockProvider([[_text_usage("hello world")]])
    agent = make_agent(prov)
    conv = Conversation()

    result = await agent.run_to_completion(conv, "say hi")
    assert "hello world" in result


@pytest.mark.asyncio
async def test_tool_then_text():
    """一轮调工具，下一轮返回文本 → 工具被执行、最终文本正确。"""
    from suisuicode.tool import Tool, Result

    class EchoTool(Tool):
        def name(self): return "echo"
        def description(self): return "echo tool"
        def parameters(self): return {}
        @property
        def read_only(self): return True
        async def execute(self, args): return Result(content="echoed")

    reg = Registry()
    reg.register(EchoTool())

    prov = MockProvider([
        [_tool_text_call("calling tool", "echo")],
        [_text_usage("done after tool")],
    ])
    agent = make_agent(prov, reg)
    conv = Conversation()

    result = await agent.run_to_completion(conv, "do it")
    assert "done after tool" in result


@pytest.mark.asyncio
async def test_max_turns_reached():
    """模型一直调工具不出文本 → raise MaxTurnsReached。"""
    from suisuicode.tool import Tool, Result

    class LoopTool(Tool):
        def name(self): return "loop"
        def description(self): return "loop"
        def parameters(self): return {}
        @property
        def read_only(self): return True
        async def execute(self, args): return Result(content="looped")

    reg = Registry()
    reg.register(LoopTool())

    # 每轮都调工具，共 4 轮
    events = [[_tool_call("loop")] for _ in range(4)]
    prov = MockProvider(events)
    agent = make_agent(prov, reg, max_turns=3)
    conv = Conversation()

    with pytest.raises(MaxTurnsReached) as exc_info:
        await agent.run_to_completion(conv, "loop forever")
    assert exc_info.value.final_text is not None


@pytest.mark.asyncio
async def test_dont_ask_auto_allows():
    """dont_ask=True 时，需要 Ask 的工具自动放行执行。"""
    from suisuicode.tool import Tool, Result

    class BashTool(Tool):
        def name(self): return "bash"
        def description(self): return "run command"
        def parameters(self): return {"type": "object", "properties": {"command": {"type": "string"}}}
        @property
        def read_only(self): return False
        async def execute(self, args): return Result(content="bash output")

    reg = Registry()
    reg.register(BashTool())

    prov = MockProvider([
        [_tool_text_call("running bash", "bash")],
        [_text_usage("result is bash output")],
    ])
    agent = make_agent(prov, reg, dont_ask=True)
    conv = Conversation()

    result = await agent.run_to_completion(conv, "run a command")
    # dont_ask 放行 bash → 正常执行 → 拿到最终文本
    assert "result is bash output" in result


@pytest.mark.asyncio
async def test_approval_upgrader_called():
    """approval_upgrader 回调在 Ask 决策时被命中。"""
    from suisuicode.tool import Tool, Result

    class BashTool(Tool):
        def name(self): return "bash"
        def description(self): return "run command"
        def parameters(self): return {"type": "object", "properties": {"command": {"type": "string"}}}
        @property
        def read_only(self): return False
        async def execute(self, args): return Result(content="bash output")

    reg = Registry()
    reg.register(BashTool())

    upgrader_called = []

    async def mock_upgrader(req):
        upgrader_called.append(req.name)
        return (Outcome.ALLOW_ONCE, True)

    prov = MockProvider([
        [_tool_text_call("running bash", "bash")],
        [_text_usage("done")],
    ])
    agent = make_agent(prov, reg, approval_upgrader=mock_upgrader)
    conv = Conversation()

    result = await agent.run_to_completion(conv, "run a command")
    assert len(upgrader_called) > 0
    assert upgrader_called[0] == "bash"
    assert "done" in result


@pytest.mark.asyncio
async def test_events_forwarded():
    """外部 events 队列收到 Tool / Text 事件。"""
    from suisuicode.tool import Tool, Result

    class EchoTool(Tool):
        def name(self): return "echo"
        def description(self): return "echo"
        def parameters(self): return {}
        @property
        def read_only(self): return True
        async def execute(self, args): return Result(content="echoed")

    reg = Registry()
    reg.register(EchoTool())

    prov = MockProvider([
        [_tool_text_call("using echo", "echo")],
        [_text_usage("all done")],
    ])
    agent = make_agent(prov, reg)
    conv = Conversation()
    ev_queue: asyncio.Queue = asyncio.Queue(maxsize=32)

    result = await agent.run_to_completion(conv, "do it", events=ev_queue)
    assert "all done" in result

    # 收集事件
    collected = []
    while not ev_queue.empty():
        collected.append(ev_queue.get_nowait())

    # 应有 text 事件和 tool 事件
    text_events = [e for e in collected if e.text]
    tool_events = [e for e in collected if e.tool is not None]
    assert len(text_events) > 0
    assert len(tool_events) > 0
