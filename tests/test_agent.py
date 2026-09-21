from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from suisuicode.agent import (
    Agent,
    CompactEvent,
    CompactPhase,
    Event,
    Phase,
    MAX_ITERATIONS,
    MAX_UNKNOWN_RUN,
    NOTICE_CANCELLED,
    NOTICE_MAX_ITER,
    NOTICE_UNKNOWN_TOOLS,
    _all_unknown,
)
from suisuicode.conversation import Conversation
from suisuicode.llm import (
    PromptTooLongError,
    Request,
    StreamEvent,
    ToolCall,
    Usage,
    ROLE_USER,
    ROLE_ASSISTANT,
    ROLE_TOOL,
)
from suisuicode.permission import Mode
from suisuicode.permission.engine import new_engine
from suisuicode.tool import Result, Registry, Tool


# ── Helpers ────────────────────────────────────────────


def _make_engine(tmp_path) -> object:
    """Create a permission engine rooted at tmp_path."""
    engine, _ = new_engine(str(tmp_path))
    return engine


# ── Fake Provider ─────────────────────────────────────


class _FakeProvider:
    """Fake provider with scripted multi-turn responses."""

    def __init__(self):
        self.name = "fake"
        self.model = "fake-model"
        self.call_count = 0
        self.requests: list[Request] = []
        # scripts: list of per-call response scripts
        self.scripts: list[list[StreamEvent]] = []

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        self.call_count += 1
        self.requests.append(req)
        idx = self.call_count - 1
        if idx < len(self.scripts):
            for ev in self.scripts[idx]:
                yield ev
        else:
            # Default: exhaust remaining with a tool call then final text
            yield StreamEvent(
                tool_calls=[
                    ToolCall(id=f"tc-{idx}", name="read_file", input='{"path":"x"}'),
                ]
            )
            yield StreamEvent(done=True)


# ── Fake Tools ────────────────────────────────────────


class _ReadOnlyTool(Tool):
    _delay: float = 0

    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "read"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, args: str) -> Result:
        if self._delay:
            await asyncio.sleep(self._delay)
        return Result(content="file content")


class _ReadOnlyInstrumented(_ReadOnlyTool):
    """Extended read-only tool that records concurrency level."""

    def __init__(self):
        super().__init__()
        self.concurrency_peak = 0
        self._concurrent = 0

    async def execute(self, args: str) -> Result:
        self._concurrent += 1
        self.concurrency_peak = max(self.concurrency_peak, self._concurrent)
        await asyncio.sleep(0.05)  # ensure overlap
        self._concurrent -= 1
        return Result(content=f"ro:{args}")


class _WriteTool(Tool):
    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "write"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        }

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        return Result(content="written")


class _WriteInstrumented(_WriteTool):
    def __init__(self):
        super().__init__()
        self.start_time: float = 0.0

    async def execute(self, args: str) -> Result:
        import time

        self.start_time = time.monotonic()
        await asyncio.sleep(0.01)
        return Result(content="rw:done")


class _SlowTool(Tool):
    """Tool that blocks until cancelled."""

    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        return "slow"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        }

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        await asyncio.sleep(10)  # long enough to be cancelled
        return Result(content="done")


class _BlockingTool(Tool):
    """Tool that blocks for a given time and can be externally signalled."""

    def __init__(self, delay: float = 0.5):
        self._delay = delay

    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        return "blocking"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        }

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        await asyncio.sleep(self._delay)
        return Result(content="blocked done")


# ── Scene A: Multi-turn link (AC1) ────────────────────


@pytest.mark.asyncio
async def test_multi_turn(tmp_path):
    """AC1: Multi-turn tool calls — agent reads file, then writes summary."""
    fp = _FakeProvider()
    fp.scripts = [
        # Turn 1: read_file
        [
            StreamEvent(
                tool_calls=[
                    ToolCall(id="tc1", name="read_file", input='{"path":"doc.md"}'),
                ]
            ),
            StreamEvent(done=True),
        ],
        # Turn 2: write_file
        [
            StreamEvent(
                tool_calls=[
                    ToolCall(
                        id="tc2",
                        name="write_file",
                        input='{"path":"summary.md","content":"ok"}',
                    ),
                ]
            ),
            StreamEvent(done=True),
        ],
        # Turn 3: done
        [StreamEvent(text="完成，已读完并写入摘要。"), StreamEvent(done=True)],
    ]

    reg = Registry()
    reg.register(_ReadOnlyTool())
    reg.register(_WriteTool())

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("读 doc.md 然后写 summary.md")

    events: list[Event] = []
    async for ev in agent.run(conv, mode=Mode.BYPASS):
        events.append(ev)

    # Check event types
    iters = [e for e in events if e.iter > 0]
    tool_starts = [e for e in events if e.tool and e.tool.phase == Phase.START]
    tool_ends = [e for e in events if e.tool and e.tool.phase == Phase.END]
    done_events = [e for e in events if e.done]

    assert len(iters) == 3, f"expected 3 iter events, got {len(iters)}"
    assert len(tool_starts) == 2, f"expected 2 tool starts, got {len(tool_starts)}"
    assert len(tool_ends) == 2
    assert len(done_events) == 1

    # Check conversation history
    msgs = conv.messages()
    assert (
        len(msgs) == 6
    )  # user + assistant(tc1) + tool + assistant(tc2) + tool + assistant(final)
    assert msgs[-1].role == ROLE_ASSISTANT
    assert "完成" in msgs[-1].content
    # Verify tool call IDs are preserved
    assert msgs[1].tool_calls[0].id == "tc1"
    assert msgs[3].tool_calls[0].id == "tc2"

    # Request should have system + environment
    assert fp.requests[0].system.stable != ""
    assert fp.requests[0].system.environment != ""


# ── Scene B: Max iterations (AC3) ─────────────────────


@pytest.mark.asyncio
async def test_max_iterations(tmp_path):
    """AC3: Agent stops at MAX_ITERATIONS when model keeps requesting tools."""
    fp = _FakeProvider()
    fp.scripts = []  # empty → default handler always returns a tool call

    reg = Registry()
    reg.register(_ReadOnlyTool())

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("keep going")

    events: list[Event] = []
    async for ev in agent.run(conv, mode=Mode.BYPASS):
        events.append(ev)

    # Should have stopped at MAX_ITERATIONS
    notices = [e for e in events if e.notice]
    assert len(notices) >= 1
    assert notices[-1].notice == NOTICE_MAX_ITER

    done_events = [e for e in events if e.done]
    assert len(done_events) == 1

    # Should have made exactly MAX_ITERATIONS provider calls
    assert fp.call_count == MAX_ITERATIONS, (
        f"expected {MAX_ITERATIONS} calls, got {fp.call_count}"
    )

    # History should end with assistant
    assert conv.last_role() == ROLE_ASSISTANT


# ── Scene C: Unknown tools (AC4) ──────────────────────


@pytest.mark.asyncio
async def test_unknown_tools_stops(tmp_path):
    """AC4: Consecutive unknown tools stop the agent."""
    fp = _FakeProvider()
    fp.scripts = []  # default handler calls "read_file" which IS registered

    # Override default to call unknown tool
    async def unknown_stream(req: Request) -> AsyncIterator[StreamEvent]:
        fp.call_count += 1
        yield StreamEvent(
            tool_calls=[
                ToolCall(id="uk1", name="nonexistent_tool", input="{}"),
            ]
        )
        yield StreamEvent(done=True)

    fp.stream = unknown_stream  # type: ignore[assignment]

    reg = Registry()
    reg.register(_ReadOnlyTool())

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("do something")

    events: list[Event] = []
    async for ev in agent.run(conv, mode=Mode.BYPASS):
        events.append(ev)

    notices = [e for e in events if e.notice]
    assert len(notices) >= 1
    assert notices[-1].notice == NOTICE_UNKNOWN_TOOLS

    # Should stop after MAX_UNKNOWN_RUN iterations
    iter_count = sum(1 for e in events if e.iter > 0)
    assert iter_count <= MAX_UNKNOWN_RUN, (
        f"expected ≤{MAX_UNKNOWN_RUN} iters, got {iter_count}"
    )

    assert conv.last_role() == ROLE_ASSISTANT


@pytest.mark.asyncio
async def test_unknown_tools_reset_on_known(tmp_path):
    """Unknown count resets when a known tool appears among unknowns."""
    fp = _FakeProvider()
    fp.call_count = 0

    async def mixed_stream(req: Request) -> AsyncIterator[StreamEvent]:
        fp.call_count += 1
        idx = fp.call_count - 1
        if idx < 2:
            # First 2 calls: all unknown
            yield StreamEvent(
                tool_calls=[
                    ToolCall(id=f"uk{idx}", name="nonexistent", input="{}"),
                ]
            )
        elif idx == 2:
            # 3rd call: mix unknown + known — resets counter
            yield StreamEvent(
                tool_calls=[
                    ToolCall(id="uk3", name="nonexistent", input="{}"),
                    ToolCall(id="k1", name="read_file", input='{"path":"x"}'),
                ]
            )
        else:
            # After: all unknown again
            yield StreamEvent(
                tool_calls=[
                    ToolCall(id=f"uk{idx}", name="nonexistent", input="{}"),
                ]
            )
        yield StreamEvent(done=True)

    fp.stream = mixed_stream  # type: ignore[assignment]

    reg = Registry()
    reg.register(_ReadOnlyTool())

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("test")

    events: list[Event] = []
    async for ev in agent.run(conv, mode=Mode.BYPASS):
        events.append(ev)

    # Because the known tool on call 3 resets, we should have:
    # calls 0,1 (unknown run=2), call 2 (mixed, reset to 0), call 3,4 (unknown run=2)
    # So: stops around call 5 (unknown run=3 after call 4)
    iter_count = sum(1 for e in events if e.iter > 0)
    # After reset, it takes MAX_UNKNOWN_RUN more iterations of all-unknown
    # Call 2 resets → calls 3,4,5 → reaches MAX_UNKNOWN_RUN
    assert iter_count > MAX_UNKNOWN_RUN, (
        f"iter_count={iter_count} should exceed MAX_UNKNOWN_RUN={MAX_UNKNOWN_RUN}"
    )


# ── Scene D: Concurrent batch (AC8) ───────────────────


@pytest.mark.asyncio
async def test_concurrent_batch(tmp_path):
    """AC8: Read-only tools run concurrently, write tool runs after."""
    ro_tool = _ReadOnlyInstrumented()
    rw_tool = _WriteInstrumented()

    reg = Registry()
    reg.register(ro_tool)
    reg.register(rw_tool)

    fp = _FakeProvider()
    fp.scripts = [
        [
            StreamEvent(
                tool_calls=[
                    ToolCall(id="tc1", name="read_file", input='{"path":"a"}'),
                    ToolCall(id="tc2", name="read_file", input='{"path":"b"}'),
                    ToolCall(
                        id="tc3", name="write_file", input='{"path":"c","content":"x"}'
                    ),
                ]
            ),
            StreamEvent(done=True),
        ],
        [StreamEvent(text="done"), StreamEvent(done=True)],
    ]

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("do 3 things")

    events: list[Event] = []
    async for ev in agent.run(conv, mode=Mode.BYPASS):
        events.append(ev)

    # Concurrency peak should be ≥ 2 (both reads ran together)
    assert ro_tool.concurrency_peak >= 2, f"concurrency_peak={ro_tool.concurrency_peak}"

    # Write tool started after both reads completed
    tool_ends = [e for e in events if e.tool and e.tool.phase == Phase.END]
    # First two END events should be read_file, third should be write_file
    assert tool_ends[0].tool.name == "read_file"
    assert tool_ends[1].tool.name == "read_file"
    assert tool_ends[2].tool.name == "write_file"

    # Check history ordering
    msgs = conv.messages()
    for msg in msgs:
        if msg.role == ROLE_TOOL and msg.tool_results:
            # Results should be in original call order: ro, ro, rw
            ids = [tr.tool_call_id for tr in msg.tool_results]
            assert ids == ["tc1", "tc2", "tc3"], f"order mismatch: {ids}"


# ── Scene E: Cancel history consistency (AC9) ─────────


@pytest.mark.asyncio
async def test_cancel_history_consistency(tmp_path):
    """AC9: After cancel, history has tool_results + assistant tail."""
    slow_tool = _SlowTool()
    reg = Registry()
    reg.register(slow_tool)

    fp = _FakeProvider()
    fp.scripts = [
        [
            StreamEvent(
                tool_calls=[
                    ToolCall(id="tc1", name="bash", input='{"command":"sleep 10"}'),
                ]
            ),
            StreamEvent(done=True),
        ],
    ]

    cancel = asyncio.Event()

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("do slow thing")

    async def run_agent():
        events = []
        async for ev in agent.run(conv, mode=Mode.BYPASS, cancel=cancel):
            events.append(ev)
        return events

    # Run agent and cancel after a short delay
    async def delayed_cancel():
        await asyncio.sleep(0.05)
        cancel.set()

    task = asyncio.create_task(run_agent())
    await asyncio.create_task(delayed_cancel())
    await task

    # History should end with assistant
    assert conv.last_role() == ROLE_ASSISTANT, f"last role: {conv.last_role()}"

    # Should have tool_results from the cancelled tool
    msgs = conv.messages()
    has_tool_msg = any(
        msg.role == ROLE_TOOL and len(msg.tool_results) > 0 for msg in msgs
    )
    assert has_tool_msg, "No tool results found in history"

    # Verify last message is assistant with cancellation notice
    assert NOTICE_CANCELLED in msgs[-1].content, f"Last msg: {msgs[-1].content!r}"

    # Verify conversation can continue (no role alternation broken)
    conv.add_user("continue")
    assert conv.last_role() == ROLE_USER


# ── Scene F: Plan tools (AC13) ────────────────────────


@pytest.mark.asyncio
async def test_plan_mode_tools(tmp_path):
    """AC13: In PLAN mode, only read-only tools are injected."""
    fp = _FakeProvider()

    reg = Registry()
    reg.register(_ReadOnlyTool())  # read_only=True
    reg.register(_WriteTool())  # read_only=False

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("plan something")

    events: list[Event] = []
    async for ev in agent.run(conv, mode=Mode.PLAN):
        events.append(ev)

    # FakeProvider should have received only read-only tools
    assert len(fp.requests) >= 1
    plan_tools = fp.requests[0].tools
    tool_names = {t.name for t in plan_tools}
    assert "read_file" in tool_names, f"got tools: {tool_names}"
    assert "write_file" not in tool_names, f"write_file should not appear: {tool_names}"

    # Should have received stable system text (same as normal mode)
    assert fp.requests[0].system.stable != ""

    # Should have plan reminder on first iteration
    assert "<system-reminder>" in fp.requests[0].reminder
    assert "计划模式" in fp.requests[0].reminder


# ── Plan mode: system stable consistency ──────────────


@pytest.mark.asyncio
async def test_plan_mode_stable_consistent(tmp_path):
    """F7/N1: Stable system text is identical between normal and plan modes."""
    fp_plan = _FakeProvider()
    fp_normal = _FakeProvider()

    reg = Registry()
    reg.register(_ReadOnlyTool())
    reg.register(_WriteTool())

    # Plan mode
    agent_plan = Agent(fp_plan, reg, _make_engine(tmp_path), version="0.1.0")
    conv_plan = Conversation()
    conv_plan.add_user("plan")
    async for _ in agent_plan.run(conv_plan, mode=Mode.PLAN):
        pass

    # Normal mode
    agent_normal = Agent(fp_normal, reg, _make_engine(tmp_path), version="0.1.0")
    conv_normal = Conversation()
    conv_normal.add_user("normal")
    async for _ in agent_normal.run(conv_normal, mode=Mode.BYPASS):
        pass

    assert len(fp_plan.requests) >= 1
    assert len(fp_normal.requests) >= 1
    assert fp_plan.requests[0].system.stable == fp_normal.requests[0].system.stable, (
        "稳定系统提示在两种模式下应相同"
    )


# ── Plan mode: reminder round-by-round ────────────────


@pytest.mark.asyncio
async def test_plan_reminder_rounds(tmp_path):
    """AC9/F7: Plan mode reminder is full on iter1, concise on iter2."""
    fp = _FakeProvider()

    # Script: return a tool call each round so we get multiple iterations
    fp.scripts = [
        # Iter 1: read_file → triggers round 2
        [
            StreamEvent(
                tool_calls=[ToolCall(id="t1", name="read_file", input='{"path":"a"}')]
            ),
            StreamEvent(done=True),
        ],
        # Iter 2: read_file → triggers round 3
        [
            StreamEvent(
                tool_calls=[ToolCall(id="t2", name="read_file", input='{"path":"b"}')]
            ),
            StreamEvent(done=True),
        ],
        # Iter 3: text done
        [StreamEvent(text="plan done"), StreamEvent(done=True)],
    ]

    reg = Registry()
    reg.register(_ReadOnlyTool())

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("plan multi-step")

    async for _ in agent.run(conv, mode=Mode.PLAN):
        pass

    # We should have 3 requests (3 iterations)
    assert len(fp.requests) == 3, f"expected 3 requests, got {len(fp.requests)}"

    # Iter 1: full reminder
    r1 = fp.requests[0].reminder
    assert "<system-reminder>" in r1
    assert "/do" in r1  # full version mentions /do

    # Iter 2: concise reminder (interval is 4, so 2 is not full)
    r2 = fp.requests[1].reminder
    assert "<system-reminder>" in r2
    # Full version is longer and mentions /do
    assert len(r2) < len(r1), "Concise reminder should be shorter"
    assert "只读" in r2  # concise mentions this

    # Iter 3: by interval (it == 3, 3-1=2 not divisible by 4), should be concise
    r3 = fp.requests[2].reminder
    assert "<system-reminder>" in r3
    assert len(r3) < len(r1)


# ── Reminder not in conversation history ──────────────


@pytest.mark.asyncio
async def test_reminder_not_in_history(tmp_path):
    """AC8: Reminder is not written to persistent conversation history."""
    fp = _FakeProvider()
    fp.scripts = [
        [
            StreamEvent(
                tool_calls=[ToolCall(id="t1", name="read_file", input='{"path":"a"}')]
            ),
            StreamEvent(done=True),
        ],
        [StreamEvent(text="final"), StreamEvent(done=True)],
    ]

    reg = Registry()
    reg.register(_ReadOnlyTool())

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("plan test")

    async for _ in agent.run(conv, mode=Mode.PLAN):
        pass

    # Check that no message in history contains the reminder text
    for msg in conv.messages():
        assert "<system-reminder>" not in msg.content, (
            f"Reminder tag found in history message: {msg.content!r}"
        )


# ── Cache usage passthrough ───────────────────────────


@pytest.mark.asyncio
async def test_cache_usage_passthrough(tmp_path):
    """Cache write/read fields in Usage are passed through to Event."""
    fp = _FakeProvider()
    fp.scripts = [
        [
            StreamEvent(
                tool_calls=[
                    ToolCall(id="tc1", name="read_file", input='{"path":"x"}'),
                ]
            ),
            StreamEvent(
                usage=Usage(
                    input_tokens=100, output_tokens=50, cache_write=300, cache_read=0
                )
            ),
            StreamEvent(done=True),
        ],
        [
            StreamEvent(text="done"),
            StreamEvent(
                usage=Usage(
                    input_tokens=200, output_tokens=80, cache_write=0, cache_read=150
                )
            ),
            StreamEvent(done=True),
        ],
    ]

    reg = Registry()
    reg.register(_ReadOnlyTool())

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("test cache")

    events: list[Event] = []
    async for ev in agent.run(conv, mode=Mode.BYPASS):
        events.append(ev)

    usage_events = [e for e in events if e.usage is not None]
    assert len(usage_events) == 2, f"expected 2 usage events, got {len(usage_events)}"

    assert usage_events[0].usage.cache_write == 300
    assert usage_events[0].usage.cache_read == 0
    assert usage_events[1].usage.cache_write == 0
    assert usage_events[1].usage.cache_read == 150


# ── Stream error (AC5) ────────────────────────────────


@pytest.mark.asyncio
async def test_stream_error(tmp_path):
    """AC5: Provider stream error stops the loop, program doesn't crash."""
    fp = _FakeProvider()
    fp.scripts = [
        [StreamEvent(err=ValueError("API error"))],
    ]

    reg = Registry()
    reg.register(_ReadOnlyTool())

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("test")

    events: list[Event] = []
    async for ev in agent.run(conv, mode=Mode.BYPASS):
        events.append(ev)

    err_events = [e for e in events if e.err is not None]
    assert len(err_events) >= 1
    done_events = [e for e in events if e.done]
    assert len(done_events) >= 1

    # Conversation should still have valid history
    assert conv.last_role() == ROLE_ASSISTANT


# ── Usage events ──────────────────────────────────────


@pytest.mark.asyncio
async def test_usage_events(tmp_path):
    """F8: Usage events are emitted and accumulated."""
    fp = _FakeProvider()
    fp.scripts = [
        [
            StreamEvent(
                tool_calls=[
                    ToolCall(id="tc1", name="read_file", input='{"path":"x"}'),
                ]
            ),
            StreamEvent(usage=Usage(input_tokens=10, output_tokens=5)),
            StreamEvent(done=True),
        ],
        [
            StreamEvent(text="done"),
            StreamEvent(usage=Usage(input_tokens=20, output_tokens=8)),
            StreamEvent(done=True),
        ],
    ]

    reg = Registry()
    reg.register(_ReadOnlyTool())

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("test usage")

    events: list[Event] = []
    async for ev in agent.run(conv, mode=Mode.BYPASS):
        events.append(ev)

    usage_events = [e for e in events if e.usage is not None]
    assert len(usage_events) >= 1  # at least one turn emitted usage

    # With 2 provider calls, should have 2 usage events
    assert len(usage_events) == 2, f"got {len(usage_events)} usage events"
    assert usage_events[0].usage.input == 10
    assert usage_events[0].usage.output == 5
    assert usage_events[1].usage.input == 20
    assert usage_events[1].usage.output == 8


# ── Pure text (AC2) ───────────────────────────────────


@pytest.mark.asyncio
async def test_pure_text_stops(tmp_path):
    """AC2: Pure text response stops immediately without tools."""
    fp = _FakeProvider()
    fp.scripts = [
        [StreamEvent(text="Hello back!"), StreamEvent(done=True)],
    ]

    reg = Registry()
    reg.register(_ReadOnlyTool())

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("hi")

    events: list[Event] = []
    async for ev in agent.run(conv, mode=Mode.BYPASS):
        events.append(ev)

    done_events = [e for e in events if e.done]
    assert len(done_events) == 1

    iters = [e for e in events if e.iter > 0]
    assert len(iters) == 1, f"expected 1 iter, got {len(iters)}"  # only iter 1

    # Should have only 2 messages: user + assistant
    assert len(conv.messages()) == 2
    assert conv.messages()[-1].content == "Hello back!"


# ── Empty final text ──────────────────────────────────


@pytest.mark.asyncio
async def test_empty_final_text(tmp_path):
    """Empty final text should get placeholder."""
    fp = _FakeProvider()
    fp.scripts = [
        [StreamEvent(text=""), StreamEvent(done=True)],
    ]

    reg = Registry()
    reg.register(_ReadOnlyTool())

    agent = Agent(fp, reg, _make_engine(tmp_path), version="0.1.0")
    conv = Conversation()
    conv.add_user("say nothing")

    events: list[Event] = []
    async for ev in agent.run(conv, mode=Mode.BYPASS):
        events.append(ev)

    done_events = [e for e in events if e.done]
    assert len(done_events) == 1
    assert conv.messages()[-1].content != ""  # placeholder should be used


# ── _all_unknown helper ───────────────────────────────


@pytest.mark.asyncio
async def test_all_unknown():
    reg = Registry()
    reg.register(_ReadOnlyTool())

    assert _all_unknown([ToolCall(id="x", name="unknown", input="{}")], reg) is True
    assert _all_unknown([ToolCall(id="x", name="read_file", input="{}")], reg) is False
    assert (
        _all_unknown(
            [
                ToolCall(id="x", name="read_file", input="{}"),
                ToolCall(id="y", name="unknown", input="{}"),
            ],
            reg,
        )
        is False
    )  # mixed = not all unknown


# ── ch08 T29a: Compact 事件发射测试 ────────────────────


@pytest.mark.asyncio
async def test_agent_emits_auto_compact_events(tmp_path):
    """自动压缩触发时 emit BEFORE_AUTO + AFTER_AUTO 事件对。"""
    conv = Conversation()
    cw = 70000
    # 用足够大的内容触发自动 layer2
    big_content = "x" * 130000
    conv.add_user(big_content)
    conv.add_assistant("ok")

    # summary response
    summary_text = "<analysis>x</analysis>\n<summary>## 1 主要请求和意图\ntest\n## 2 关键技术概念\nnone\n## 3 文件和代码段\nnone\n## 4 错误和修复\nnone\n## 5 问题解决过程\nnone\n## 6 所有用户消息原文\n" + big_content[:100] + "\n## 7 待办任务\nnone\n## 8 当前工作\ntesting\n## 9 可能的下一步\nmore</summary>"

    provider = _FakeProvider()
    provider.scripts = [
        [StreamEvent(text=summary_text), StreamEvent(usage=Usage(input_tokens=100, output_tokens=50)), StreamEvent(done=True)],
        [StreamEvent(text="final"), StreamEvent(done=True)],
    ]

    from suisuicode.agent.runtime import SessionRuntime
    from suisuicode.compact import (
        CompactCircuitBreaker,
        ContentReplacementState,
        RecoveryState,
        new_session_context,
    )

    engine = _make_engine(tmp_path)
    reg = Registry()
    reg.register(_ReadOnlyTool())
    runtime = SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context(str(tmp_path)),
        context_window=70000,  # 低窗口确保触发
    )
    agent = Agent(provider, reg, engine, "0.1.0", runtime=runtime)

    events = []
    async for ev in agent.run(conv, mode=Mode.DEFAULT):
        events.append(ev)
        if ev.done:
            break

    compact_events = [e for e in events if e.compact is not None]
    assert len(compact_events) == 2
    phases = [e.compact.phase for e in compact_events]
    assert CompactPhase.BEFORE_AUTO in phases
    assert CompactPhase.AFTER_AUTO in phases
    # AFTER_AUTO 的 before > 0
    after_ev = next(e for e in compact_events if e.compact.phase == CompactPhase.AFTER_AUTO)
    assert after_ev.compact.before > 0


# ── ch08 T31: 紧急压缩测试 ──────────────────────────────


class _ScriptedProvider:
    """按调用次数脚本化返迴，支持区分摘要请求与注入 PTL 错误。"""

    def __init__(self):
        self.name = "fake"
        self.model = "fake-model"
        self.scripts: list[list[StreamEvent]] = []
        self.requests: list[Request] = []
        self.call_count = 0

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        self.requests.append(req)
        idx = self.call_count
        self.call_count += 1
        if idx < len(self.scripts):
            for ev in self.scripts[idx]:
                yield ev
        else:
            yield StreamEvent(text="default"), StreamEvent(done=True)


@pytest.mark.asyncio
async def test_agent_emergency_compact_succeeds(tmp_path):
    """第 1 次 stream 返回 PTL → 紧急压缩 → 重试成功。"""
    conv = Conversation()
    conv.add_user("hello")
    conv.add_assistant("hi")

    summary_text = "<analysis>x</analysis>\n<summary>## 1 主要请求和意图\nt\n## 2 关键技术概念\nn\n## 3 文件和代码段\nn\n## 4 错误和修复\nn\n## 5 问题解决过程\nn\n## 6 所有用户消息原文\nhello\n## 7 待办任务\nn\n## 8 当前工作\nt\n## 9 可能的下一步\nn</summary>"

    provider = _ScriptedProvider()
    provider.scripts = [
        # Call 0: 主请求返回 PTL
        [StreamEvent(err=PromptTooLongError("ptl"))],
        # Call 1: 摘要请求（tools=None）返回成功
        [StreamEvent(text=summary_text), StreamEvent(usage=Usage(input_tokens=10, output_tokens=5)), StreamEvent(done=True)],
        # Call 2: 重试主请求返回成功
        [StreamEvent(text="all good"), StreamEvent(done=True)],
    ]

    engine = _make_engine(tmp_path)
    reg = Registry()
    reg.register(_ReadOnlyTool())
    agent = Agent(provider, reg, engine, "0.1.0")

    events = []
    async for ev in agent.run(conv, mode=Mode.DEFAULT):
        events.append(ev)
        if ev.done:
            break

    # 检查 emergency compact 事件
    compact_events = [e for e in events if e.compact is not None]
    before_emergency = [e for e in compact_events if e.compact.phase == CompactPhase.BEFORE_EMERGENCY]
    after_emergency = [e for e in compact_events if e.compact.phase == CompactPhase.AFTER_EMERGENCY]
    assert len(before_emergency) == 1
    assert len(after_emergency) == 1
    assert after_emergency[0].compact.err is None

    # 最终成功完成
    assert any(e.done for e in events)
    # 没有 error event
    assert not any(e.err is not None for e in events)


@pytest.mark.asyncio
async def test_agent_emergency_compact_re_raise_on_second_ptl(tmp_path):
    """紧急压缩后重试再次 PTL → 上抛异常，不做第三次。"""
    conv = Conversation()
    conv.add_user("hello")
    conv.add_assistant("hi")

    summary_text = "<analysis>x</analysis>\n<summary>## 1 主要请求和意图\nt\n## 2 关键技术概念\nn\n## 3 文件和代码段\nn\n## 4 错误和修复\nn\n## 5 问题解决过程\nn\n## 6 所有用户消息原文\nhello\n## 7 待办任务\nn\n## 8 当前工作\nt\n## 9 可能的下一步\nn</summary>"

    provider = _ScriptedProvider()
    provider.scripts = [
        # Call 0: 主请求 PTL
        [StreamEvent(err=PromptTooLongError("ptl"))],
        # Call 1: 摘要请求成功
        [StreamEvent(text=summary_text), StreamEvent(usage=Usage(input_tokens=10, output_tokens=5)), StreamEvent(done=True)],
        # Call 2: 重试主请求再次 PTL
        [StreamEvent(err=PromptTooLongError("ptl again"))],
    ]

    engine = _make_engine(tmp_path)
    reg = Registry()
    reg.register(_ReadOnlyTool())
    agent = Agent(provider, reg, engine, "0.1.0")

    events = []
    async for ev in agent.run(conv, mode=Mode.DEFAULT):
        events.append(ev)
        if ev.done or ev.err is not None:
            break

    # 应该有 error event（第二次 PTL 上抛）
    err_events = [e for e in events if e.err is not None]
    assert len(err_events) >= 1
    # 请求不应超过 3 次（原始 + 摘要 + 重试 = 3）
    assert provider.call_count <= 3


@pytest.mark.asyncio
async def test_agent_compact_then_final_answer(tmp_path):
    """自动压缩后 Agent 正常完成并输出最终回复。"""
    conv = Conversation()
    cw = 70000
    big_content = "y" * 130000
    conv.add_user(big_content)
    conv.add_assistant("waiting")

    summary_text = "<analysis>x</analysis>\n<summary>## 1 主要请求和意图\nt\n## 2 关键技术概念\nn\n## 3 文件和代码段\nn\n## 4 错误和修复\nn\n## 5 问题解决过程\nn\n## 6 所有用户消息原文\n" + big_content[:100] + "\n## 7 待办任务\nn\n## 8 当前工作\nt\n## 9 可能的下一步\nn</summary>"

    provider = _ScriptedProvider()
    provider.scripts = [
        # Call 0: 摘要请求
        [StreamEvent(text=summary_text), StreamEvent(usage=Usage(input_tokens=10, output_tokens=5)), StreamEvent(done=True)],
        # Call 1: 主请求（压缩后）返回最终答案（无工具调用）
        [StreamEvent(text="final answer"), StreamEvent(done=True)],
    ]

    engine = _make_engine(tmp_path)
    reg = Registry()
    reg.register(_ReadOnlyTool())
    agent = Agent(provider, reg, engine, "0.1.0")

    events = []
    async for ev in agent.run(conv, mode=Mode.DEFAULT):
        events.append(ev)
        if ev.done:
            break

    assert any(e.done for e in events)
    assert not any(e.err is not None for e in events)
