"""T13: Engine dispatch / 拦截 / reminder / once 覆盖。"""

from __future__ import annotations

import asyncio
import sys

import pytest

from suisuicode.hook.engine import Engine
from suisuicode.hook.event import Event
from suisuicode.hook.rule import (
    Action,
    ActionType,
    AtomCondition,
    CombineMode,
    Condition,
    PromptAction,
    Rule,
    ShellAction,
)
from suisuicode.permission.matcher import ExactMatcher

_PYTHON = sys.executable
_QPY = f'"{_PYTHON}"'  # 双引号包裹，防止路径含空格


def _make_rule(
    name="test",
    event=Event.STOP,
    action_type=ActionType.SHELL,
    command=None,
    condition=None,
    only_once=False,
    asyncio_mode=False,
) -> Rule:
    if command is None:
        command = f'{_QPY} -c "import sys; sys.exit(0)"'
    action = Action(type=action_type)
    if action_type == ActionType.SHELL:
        action.shell = ShellAction(command=command)
    elif action_type == ActionType.PROMPT:
        action.prompt = PromptAction(text=command)
    return Rule(
        name=name,
        event=event,
        action=action,
        condition=condition,
        only_once=only_once,
        asyncio_mode=asyncio_mode,
    )


# ── dispatch basics ────────────────────────────────────


class TestEngineDispatch:
    @pytest.mark.asyncio
    async def test_no_rules(self):
        """空规则集返回空 DispatchResult。"""
        engine = Engine([], [])
        result = await engine.dispatch(Event.STOP, {})
        assert result.blocked is False
        assert result.injected_prompts == []

    @pytest.mark.asyncio
    async def test_event_filter(self):
        """只分派匹配事件的 rule。"""
        r1 = _make_rule(
            name="r1", event=Event.STOP,
            command=f'{_QPY} -c "import sys; sys.exit(0)"',
        )
        r2 = _make_rule(
            name="r2", event=Event.SESSION_START,
            command=f'{_QPY} -c "import sys; sys.stderr.write(\'stop\'); sys.exit(2)"',
        )
        engine = Engine([r1, r2], [])
        result = await engine.dispatch(Event.STOP, {})
        assert result.blocked is False  # r2 未触发

    @pytest.mark.asyncio
    async def test_order_preserved(self):
        """按声明顺序执行，首个 blocked 中断后续。"""
        r1 = _make_rule(
            name="r1", event=Event.PRE_TOOL_USE,
            command=f'{_QPY} -c "import sys; sys.stderr.write(\'stop\'); sys.exit(2)"',
        )
        r2 = _make_rule(
            name="r2", event=Event.PRE_TOOL_USE,
            command=f'{_QPY} -c "import sys; sys.stderr.write(\'second\'); sys.exit(0)"',
        )
        engine = Engine([r1, r2], [])
        result = await engine.dispatch(
            Event.PRE_TOOL_USE, {"tool_name": "bash"}
        )
        assert result.blocked is True
        assert result.blocking_hook_name == "r1"

    @pytest.mark.asyncio
    async def test_non_blocking_no_intercept(self):
        """非拦截事件下 shell exit 2 不产生 blocked（仅 err 日志）。"""
        r1 = _make_rule(
            name="r1", event=Event.STOP,
            command=f'{_QPY} -c "import sys; sys.stderr.write(\'stop\'); sys.exit(2)"',
        )
        engine = Engine([r1], [])
        result = await engine.dispatch(Event.STOP, {})
        assert result.blocked is False

    @pytest.mark.asyncio
    async def test_prompt_accumulation(self):
        """多个 prompt hook 的 text 累加到 injected_prompts。"""
        r1 = _make_rule(
            name="r1", event=Event.SESSION_START,
            action_type=ActionType.PROMPT, command="text1",
        )
        r2 = _make_rule(
            name="r2", event=Event.SESSION_START,
            action_type=ActionType.PROMPT, command="text2",
        )
        engine = Engine([r1, r2], [])
        result = await engine.dispatch(Event.SESSION_START, {})
        assert result.injected_prompts == ["text1", "text2"]
        assert result.blocked is False


# ── condition ──────────────────────────────────────────


class TestEngineCondition:
    @pytest.mark.asyncio
    async def test_condition_match(self):
        """条件匹配 → hook 执行并被拦截。"""
        condition = Condition(
            mode=CombineMode.ALL_OF,
            atoms=[
                AtomCondition(
                    field="tool_name",
                    matcher=ExactMatcher("write_file"),
                )
            ],
        )
        r1 = _make_rule(
            name="r1", event=Event.PRE_TOOL_USE,
            command=f'{_QPY} -c "import sys; sys.stderr.write(\'blocked\'); sys.exit(2)"',
            condition=condition,
        )
        engine = Engine([r1], [])
        result = await engine.dispatch(
            Event.PRE_TOOL_USE, {"tool_name": "write_file"}
        )
        assert result.blocked is True

    @pytest.mark.asyncio
    async def test_condition_no_match(self):
        """条件不匹配 → hook 跳过。"""
        condition = Condition(
            mode=CombineMode.ALL_OF,
            atoms=[
                AtomCondition(
                    field="tool_name",
                    matcher=ExactMatcher("write_file"),
                )
            ],
        )
        r1 = _make_rule(
            name="r1", event=Event.PRE_TOOL_USE,
            command=f'{_QPY} -c "import sys; sys.stderr.write(\'blocked\'); sys.exit(2)"',
            condition=condition,
        )
        engine = Engine([r1], [])
        result = await engine.dispatch(
            Event.PRE_TOOL_USE, {"tool_name": "read_file"}
        )
        assert result.blocked is False

    @pytest.mark.asyncio
    async def test_any_of(self):
        """any_of 任一条件满足即触发。"""
        condition = Condition(
            mode=CombineMode.ANY_OF,
            atoms=[
                AtomCondition(field="tool_name", matcher=ExactMatcher("write_file")),
                AtomCondition(field="tool_name", matcher=ExactMatcher("edit_file")),
            ],
        )
        r1 = _make_rule(
            name="r1", event=Event.PRE_TOOL_USE,
            command=f'{_QPY} -c "import sys; sys.stderr.write(\'blocked\'); sys.exit(2)"',
            condition=condition,
        )
        engine = Engine([r1], [])
        result = await engine.dispatch(
            Event.PRE_TOOL_USE, {"tool_name": "edit_file"}
        )
        assert result.blocked is True


# ── only_once ──────────────────────────────────────────


class TestEngineOnlyOnce:
    @pytest.mark.asyncio
    async def test_only_once_fires_once(self):
        """only_once: 第一次触发后被标记。"""
        r1 = _make_rule(
            name="once", event=Event.SESSION_START,
            command=f'{_QPY} -c "import sys; sys.exit(0)"',
            only_once=True,
        )
        engine = Engine([r1], [])
        await engine.dispatch(Event.SESSION_START, {})
        assert "once" in engine._once_fired

    @pytest.mark.asyncio
    async def test_only_once_reset(self):
        """reset_for_new_session 后 only_once 重置。"""
        r1 = _make_rule(
            name="once", event=Event.SESSION_START,
            command=f'{_QPY} -c "import sys; sys.exit(0)"',
            only_once=True,
        )
        engine = Engine([r1], [])
        await engine.dispatch(Event.SESSION_START, {})
        assert "once" in engine._once_fired
        await engine.reset_for_new_session()
        assert "once" not in engine._once_fired


# ── async ─────────────────────────────────────────────


class TestEngineAsync:
    @pytest.mark.asyncio
    async def test_async_not_enters_blocked(self):
        """async hook 不进入 blocked 判定。"""
        r1 = _make_rule(
            name="async-hook", event=Event.PRE_TOOL_USE,
            command=f'{_QPY} -c "import sys; sys.stderr.write(\'blocked\'); sys.exit(2)"',
            asyncio_mode=True,
        )
        engine = Engine([r1], [])
        result = await engine.dispatch(
            Event.PRE_TOOL_USE, {"tool_name": "bash"}
        )
        # async hook 被后台执行跳过，不参与 blocked
        assert result.blocked is False
        # only_once=False，所以不加入 _once_fired
        assert "async-hook" not in engine._once_fired
        # 等后台 task 完成
        await asyncio.sleep(0.5)

    @pytest.mark.asyncio
    async def test_async_with_only_once(self):
        """async + only_once: 第一次 dispatch 后就被标记。"""
        r1 = _make_rule(
            name="async-once", event=Event.SESSION_START,
            command=f'{_QPY} -c "import sys; sys.exit(0)"',
            asyncio_mode=True, only_once=True,
        )
        engine = Engine([r1], [])
        await engine.dispatch(Event.SESSION_START, {})
        assert "async-once" in engine._once_fired


# ── properties ────────────────────────────────────────


class TestEngineProperties:
    def test_sources(self):
        engine = Engine([], ["/tmp/a.yaml", "/tmp/b.yaml"])
        assert engine.sources == ["/tmp/a.yaml", "/tmp/b.yaml"]

    def test_rules(self):
        r1 = _make_rule(
            name="r1", event=Event.STOP,
            command=f'{_QPY} -c "import sys; sys.exit(0)"',
        )
        engine = Engine([r1], [])
        assert len(engine.rules) == 1
        assert engine.rules[0].name == "r1"
        # 返回的是副本
        engine.rules.pop()
        assert len(engine.rules) == 1  # 原列表未变
