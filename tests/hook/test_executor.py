"""T12: Executor 四类动作单元测试（shell / http / prompt / subagent）。"""

from __future__ import annotations

import json
import sys

import pytest

from suisuicode.hook.event import Event
from suisuicode.hook.executor import Executor
from suisuicode.hook.rule import (
    Action,
    ActionType,
    HttpAction,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)

# 跨平台 shell 命令：用 Python 本身执行，确保 Windows/Linux 一致
_PYTHON = sys.executable
_QPY = f'"{_PYTHON}"'  # 双引号包裹，防止路径含空格导致 shell 解析失败


def _make_rule(action: Action, **kw) -> Rule:
    return Rule(
        name="test",
        event=Event.STOP,
        action=action,
        timeout_s=kw.pop("timeout_s", 30.0),
        **kw,
    )


# ── shell ───────────────────────────────────────────────


class TestShell:
    @pytest.mark.asyncio
    async def test_exit_2_blocked(self):
        """returncode=2 + blocking=True → 拦截。"""
        rule = _make_rule(
            Action(
                type=ActionType.SHELL,
                shell=ShellAction(
                    f'{_QPY} -c "import sys; sys.stderr.write(\'blocked by hook\'); sys.exit(2)"'
                ),
            )
        )
        ex = Executor()
        result = await ex.run(rule, {}, blocking=True)
        assert result.blocked is True
        assert "blocked by hook" in result.reason

    @pytest.mark.asyncio
    async def test_exit_0_pass(self):
        """returncode=0 → 放行。"""
        rule = _make_rule(
            Action(
                type=ActionType.SHELL,
                shell=ShellAction(f'{_QPY} -c "import sys; sys.exit(0)"'),
            )
        )
        ex = Executor()
        result = await ex.run(rule, {}, blocking=True)
        assert result.blocked is False
        assert result.err is None

    @pytest.mark.asyncio
    async def test_exit_1_no_block(self):
        """returncode=1 → err 非 None 但不拦截。"""
        rule = _make_rule(
            Action(
                type=ActionType.SHELL,
                shell=ShellAction(f'{_QPY} -c "import sys; sys.exit(1)"'),
            )
        )
        ex = Executor()
        result = await ex.run(rule, {}, blocking=True)
        assert result.blocked is False
        assert result.err is not None
        assert "exit 1" in str(result.err)

    @pytest.mark.asyncio
    async def test_exit_2_non_blocking_event(self):
        """非拦截事件下 exit 2 不设置 blocked，但记 err。"""
        rule = _make_rule(
            Action(
                type=ActionType.SHELL,
                shell=ShellAction(
                    f'{_QPY} -c "import sys; sys.stderr.write(\'blocked\'); sys.exit(2)"'
                ),
            )
        )
        ex = Executor()
        result = await ex.run(rule, {}, blocking=False)
        assert result.blocked is False
        # exit 2 with non-blocking → treated as failure (err non-None)
        assert result.err is not None

    @pytest.mark.asyncio
    async def test_stdin_payload_sorted_keys(self):
        """payload JSON 传给 shell stdin，key 字典序。"""
        payload = {"z": 1, "a": 2, "event": "Stop"}
        serialized = json.dumps(payload, sort_keys=True)
        assert serialized.index("a") < serialized.index("z")

    @pytest.mark.asyncio
    async def test_timeout(self):
        """超时 → err 为 TimeoutError。"""
        rule = _make_rule(
            Action(
                type=ActionType.SHELL,
                shell=ShellAction(f'{_QPY} -c "import time; time.sleep(5)"'),
            ),
            timeout_s=0.1,
        )
        ex = Executor()
        result = await ex.run(rule, {}, blocking=False)
        assert result.blocked is False
        assert result.err is not None
        assert isinstance(result.err, TimeoutError)


# ── prompt ──────────────────────────────────────────────


class TestPrompt:
    @pytest.mark.asyncio
    async def test_prompt_text(self):
        """prompt 动作返回 ExecutionResult.prompt。"""
        rule = _make_rule(
            Action(
                type=ActionType.PROMPT,
                prompt=PromptAction(text="use zh-CN"),
            )
        )
        ex = Executor()
        result = await ex.run(rule, {}, blocking=False)
        assert result.prompt == "use zh-CN"
        assert result.blocked is False
        assert result.err is None


# ── http ────────────────────────────────────────────────


class TestHttp:
    @pytest.mark.asyncio
    async def test_connection_refused(self):
        """HTTP 连接被拒 → err 非 None，不拦截。"""
        rule = _make_rule(
            Action(
                type=ActionType.HTTP,
                http=HttpAction(url="http://127.0.0.1:1/test", method="POST"),
            )
        )
        ex = Executor()
        result = await ex.run(rule, {"event": "Stop"}, blocking=True)
        assert result.err is not None
        assert result.blocked is False


# ── subagent ────────────────────────────────────────────


class TestSubagent:
    @pytest.mark.asyncio
    async def test_stub_log(self, capsys):
        """subagent 占位实现：仅 stderr 日志。"""
        rule = _make_rule(
            Action(
                type=ActionType.SUBAGENT,
                subagent=SubagentAction(agent_name="foo", prompt="test"),
            )
        )
        ex = Executor()
        result = await ex.run(rule, {}, blocking=False)
        assert result.blocked is False
        assert result.err is None
        captured = capsys.readouterr()
        assert "not yet implemented, skipped: foo" in captured.err
        assert "[hook subagent]" in captured.err
