"""T14: 命令分发测试 — 迁移到新 Registry 分发器。"""

import pytest

from suisuicode.agent.event import CompactEvent, CompactPhase
from suisuicode.command.builtins import register_builtins
from suisuicode.command.dispatch import parse
from suisuicode.command.registry import Registry
from suisuicode.command.ui import NopUI
from suisuicode.tui.commands import format_compact_notice


# ── Registry + parse + handler 链路测试 ──────────────


def test_dispatch_slash_compact():
    """parse("/compact") → lookup 命中 → handler 存在。"""
    reg = Registry()
    register_builtins(reg)
    name, args, is_slash = parse("/compact")
    assert is_slash is True
    assert name == "compact"
    cmd = reg.lookup(name)
    assert cmd is not None
    assert cmd.name == "compact"


def test_dispatch_slash_exit():
    reg = Registry()
    register_builtins(reg)
    name, args, is_slash = parse("/exit")
    assert is_slash is True
    assert name == "exit"
    assert reg.lookup(name) is not None


def test_dispatch_slash_plan():
    reg = Registry()
    register_builtins(reg)
    name, args, is_slash = parse("/plan")
    assert is_slash is True
    assert name == "plan"
    assert reg.lookup(name) is not None


def test_dispatch_slash_do():
    reg = Registry()
    register_builtins(reg)
    name, args, is_slash = parse("/do")
    assert is_slash is True
    assert name == "do"
    assert reg.lookup(name) is not None


def test_dispatch_unknown_command():
    """未知命令 → parse 返回 miss → lookup 返回 None。"""
    reg = Registry()
    register_builtins(reg)
    name, args, is_slash = parse("/foobar")
    assert is_slash is True
    assert name == "foobar"
    assert reg.lookup(name) is None


def test_dispatch_not_a_command():
    """非 / 开头 → is_slash=False。"""
    name, args, is_slash = parse("hello world")
    assert is_slash is False
    assert name == ""


def test_dispatch_empty():
    name, args, is_slash = parse("")
    assert is_slash is False
    assert name == ""


def test_dispatch_case_insensitive():
    """/Help 与 /help 同效。"""
    reg = Registry()
    register_builtins(reg)
    name1, _, _ = parse("/Help")
    name2, _, _ = parse("/help")
    assert name1 == name2 == "help"
    cmd = reg.lookup(name1)
    assert cmd is not None
    assert cmd.name == "help"


def test_builtin_commands_count():
    """注册后恰好 15 条命令（14 条 ch10-ch12 + /worktree）。"""
    reg = Registry()
    register_builtins(reg)
    assert len(reg.visible()) == 15
    names = [c.name for c in reg.visible()]
    expected = [
        "clear", "compact", "do", "exit", "help", "hooks",
        "memory", "permission", "plan", "resume", "review",
        "session", "skill", "status", "worktree",
    ]
    for n in expected:
        assert n in names


@pytest.mark.asyncio
async def test_help_handler_lists_all_builtins():
    """/help handler 输出含 15 个命令名。"""
    reg = Registry()
    register_builtins(reg)
    cmd = reg.lookup("help")
    assert cmd is not None

    class CaptureUI(NopUI):
        def __init__(self):
            super().__init__()
            self.output: list[str] = []

        def ui_println(self, msg: str) -> None:
            self.output.append(msg)

    ui = CaptureUI()
    await cmd.handler(ui, "")
    combined = "\n".join(ui.output)
    expected_names = [
        "clear", "compact", "do", "exit", "help", "hooks",
        "memory", "permission", "plan", "resume", "review",
        "session", "skill", "status",
    ]
    for n in expected_names:
        assert f"/{n}" in combined, f"缺少 /{n}"


# ── format_compact_notice 测试（保持不变）─────────────


def test_format_compact_before_auto():
    ev = CompactEvent(phase=CompactPhase.BEFORE_AUTO)
    assert "Compacting" in format_compact_notice(ev)


def test_format_compact_before_emergency():
    ev = CompactEvent(phase=CompactPhase.BEFORE_EMERGENCY)
    assert "limit exceeded" in format_compact_notice(ev)


def test_format_compact_after_success():
    ev = CompactEvent(phase=CompactPhase.AFTER_AUTO, before=167000, after=12000)
    msg = format_compact_notice(ev)
    assert "167000" in msg
    assert "12000" in msg
    assert "Compacted:" in msg


def test_format_compact_after_error():
    ev = CompactEvent(
        phase=CompactPhase.AFTER_EMERGENCY,
        before=1000,
        after=0,
        err=RuntimeError("boom"),
    )
    msg = format_compact_notice(ev)
    assert "Compact failed" in msg
    assert "boom" in msg
