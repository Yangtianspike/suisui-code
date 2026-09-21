"""T17: Agent hook 集成测试 — _dispatch_hook 行为 + _execute_batched 拦截路径。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from suisuicode.agent import Agent
from suisuicode.agent.runtime import SessionRuntime
from suisuicode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from suisuicode.conversation import Conversation
from suisuicode.hook import Engine as HookEngine, Event as HookEvent
from suisuicode.hook.executor import ExecutionResult
from suisuicode.hook.rule import (
    Action,
    ActionType,
    AtomCondition,
    CombineMode,
    Condition,
    Rule,
    ShellAction,
)
from suisuicode.llm import Request, StreamEvent, ToolCall, ToolResult
from suisuicode.permission import Mode
from suisuicode.permission.engine import new_engine
from suisuicode.permission.matcher import ExactMatcher
from suisuicode.tool import Registry, Result


# ── Fake Provider（仅用于不涉及真实 LLM 调用的测试）──


class FakeProvider:
    def __init__(self):
        self.model = "fake"

    async def stream(self, req: Request):
        yield StreamEvent(text="ok", done=True)


# ── Fake Tool ──────────────────────────────────────


class FakeTool:
    def __init__(self, name, is_read_only=False):
        self._name = name
        self._ro = is_read_only

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return ""

    def parameters(self) -> dict:
        return {}

    @property
    def read_only(self) -> bool:
        return self._ro

    @property
    def is_system_tool(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        return Result(content=f"ok-{self._name}", is_error=False)


# ── helpers ────────────────────────────────────────


def _make_runtime():
    return SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context("."),
    )


def _make_agent(hook_rules=None):
    provider = FakeProvider()
    registry = Registry()
    engine, _ = new_engine(".")
    hook_engine = HookEngine(rules=hook_rules or [], sources=["test"])
    runtime = _make_runtime()
    agent = Agent(
        provider, registry, engine,
        runtime=runtime, hook_engine=hook_engine,
    )
    return agent, runtime


# ── Tests: _dispatch_hook ───────────────────────────


class TestDispatchHook:
    @pytest.mark.asyncio
    async def test_no_hook_engine_returns_empty(self):
        """hook_engine=None → 返回空 DispatchResult。"""
        agent, runtime = _make_agent()
        agent._hook_engine = None
        result = await agent._dispatch_hook(HookEvent.STOP, {})
        assert result.blocked is False
        assert result.injected_prompts == []

    @pytest.mark.asyncio
    async def test_prompt_injected_to_reminders(self):
        """prompt 动作 → injected_prompts 追加到 runtime。"""
        rules = [
            Rule(
                name="inj",
                event=HookEvent.SESSION_START,
                action=Action(
                    type=ActionType.PROMPT,
                    prompt=type("PA", (), {"text": "hello"})(),
                ),
            )
        ]
        agent, runtime = _make_agent(rules)
        await agent._dispatch_hook(HookEvent.SESSION_START, {})
        # runtime 应收到 injected_prompts
        taken = runtime.take_reminders()
        assert "hello" in taken

    @pytest.mark.asyncio
    async def test_blocking_intercepted(self):
        """PreToolUse 拦截 → DispatchResult.blocked=True。"""
        rules = [
            Rule(
                name="block",
                event=HookEvent.PRE_TOOL_USE,
                action=Action(
                    type=ActionType.SHELL,
                    shell=ShellAction(command="unused"),
                ),
                condition=Condition(
                    mode=CombineMode.ALL_OF,
                    atoms=[AtomCondition(
                        field="tool_name",
                        matcher=ExactMatcher("write_file"),
                    )],
                ),
            )
        ]
        agent, runtime = _make_agent(rules)

        with patch.object(agent._hook_engine._executor, "run",
                          return_value=ExecutionResult(
                              blocked=True, reason="blocked by hook"
                          )):
            result = await agent._dispatch_hook(
                HookEvent.PRE_TOOL_USE,
                {"tool_name": "write_file"},
            )

        assert result.blocked is True
        assert "blocked by hook" in result.reason


class TestReminderFlow:
    @pytest.mark.asyncio
    async def test_take_reminders_clears(self):
        """take_reminders 取出并清空。"""
        runtime = _make_runtime()
        runtime.append_reminders(["a", "b"])
        taken = runtime.take_reminders()
        assert taken == ["a", "b"]
        # 第二次为空
        assert runtime.take_reminders() == []

    @pytest.mark.asyncio
    async def test_reset_clears_reminders(self):
        """reset_for_new_session 清空 pending_reminders。"""
        runtime = _make_runtime()
        runtime.append_reminders(["x"])
        runtime.reset_for_new_session(new_session_context("."))
        assert runtime.take_reminders() == []


class TestExecuteBatchedBlock:
    """验证 _execute_batched 中 PreToolUse hook 拦截 → 跳过工具执行。"""

    @pytest.mark.asyncio
    async def test_hook_block_skips_tool(self):
        """PreToolUse blocked → 工具执行被跳过，result 含 hook reason。"""
        agent, runtime = _make_agent()

        # 注册一个假工具
        tool = FakeTool("write_file")
        agent._registry.register(tool)

        # 创建一条带 blocking 结果的 hook engine
        rules = [
            Rule(
                name="block",
                event=HookEvent.PRE_TOOL_USE,
                action=Action(
                    type=ActionType.SHELL,
                    shell=ShellAction(command="unused"),
                ),
                condition=Condition(
                    mode=CombineMode.ALL_OF,
                    atoms=[AtomCondition(
                        field="tool_name",
                        matcher=ExactMatcher("write_file"),
                    )],
                ),
            )
        ]
        agent._hook_engine = HookEngine(rules=rules, sources=["test"])

        # mock executor 返回 blocked
        with patch.object(agent._hook_engine._executor, "run",
                          return_value=ExecutionResult(
                              blocked=True, reason="blocked by hook"
                          )):
            # 直接调用 _execute_batched
            calls = [ToolCall(id="call_1", name="write_file",
                              input='{"path": "t.txt"}')]
            emitted = []

            async def fake_emit(ev):
                emitted.append(ev)

            results, completed = await agent._execute_batched(
                calls, Mode.DEFAULT, type("EV", (), {"is_set": lambda self: False})(), fake_emit
            )

        assert completed is True
        assert len(results) == 1
        assert results[0].is_error is True
        assert "blocked by hook" in results[0].content
        assert "[hook block]" in results[0].content

        # 应 emit 了 PhaseStart + PhaseEnd
        assert len(emitted) == 2

    @pytest.mark.asyncio
    async def test_no_block_tool_executes(self):
        """PreToolUse 未拦截 → 工具正常执行。"""
        agent, runtime = _make_agent()
        # 注册一个 grep 工具（非只读，hook 只拦截 write_file）
        tool = FakeTool("grep", is_read_only=False)
        agent._registry.register(tool)

        rules = [
            Rule(
                name="block-write",
                event=HookEvent.PRE_TOOL_USE,
                action=Action(
                    type=ActionType.SHELL,
                    shell=ShellAction(command="unused"),
                ),
                condition=Condition(
                    mode=CombineMode.ALL_OF,
                    atoms=[AtomCondition(
                        field="tool_name",
                        matcher=ExactMatcher("write_file"),
                    )],
                ),
            )
        ]
        agent._hook_engine = HookEngine(rules=rules, sources=["test"])

        with patch.object(agent._hook_engine._executor, "run",
                          return_value=ExecutionResult()):
            calls = [ToolCall(id="call_1", name="grep",
                              input='{"pattern": "hello"}')]
            emitted = []

            async def fake_emit(ev):
                emitted.append(ev)

            results, completed = await agent._execute_batched(
                calls, Mode.BYPASS, type("EV", (), {"is_set": lambda self: False})(), fake_emit
            )

        assert completed is True
        assert len(results) == 1
        assert results[0].is_error is False
        assert "ok-grep" in results[0].content
