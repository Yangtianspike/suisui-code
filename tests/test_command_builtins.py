"""T8: 13 条内置命令注册 + NopUI/RecordingUI 调用测试。"""

import pytest

from suisuicode.command.builtins import register_builtins
from suisuicode.command.command import Kind
from suisuicode.command.registry import Registry
from suisuicode.command.ui import NopUI
from suisuicode.permission import Mode


ALL_NAMES = [
    "clear",
    "compact",
    "do",
    "exit",
    "help",
    "hooks",
    "memory",
    "permission",
    "plan",
    "resume",
    "review",
    "session",
    "skill",
    "status",
    "worktree",
]


class RecordingUI(NopUI):
    """记录 println / error / set_mode / inject_and_send 调用的测试桩。"""

    def __init__(self):
        super().__init__()
        self.prints: list[str] = []
        self.errors: list[str] = []
        self.modes: list[Mode] = []
        self.injects: list[tuple[str, str]] = []
        self.force_compacts: int = 0
        self.clears: int = 0
        self._idle: bool = True

    def ui_println(self, msg: str) -> None:
        self.prints.append(msg)

    def ui_error(self, msg: str) -> None:
        self.errors.append(msg)

    def ui_set_mode(self, m: Mode) -> None:
        self.modes.append(m)

    def ui_inject_and_send(self, label: str, preset: str) -> None:
        self.injects.append((label, preset))

    def ui_force_compact(self) -> None:
        self.force_compacts += 1

    def ui_clear_and_new_session(self) -> None:
        self.clears += 1

    def ui_idle(self) -> bool:
        return self._idle

    def hook_sources(self) -> list[str]:
        return []

    def hook_rules(self) -> list:
        return []


# ── 注册完整性 ──────────────────────────────────────


def test_register_builtins_all_registered():
    reg = Registry()
    register_builtins(reg)
    visible = reg.visible()
    assert len(visible) == 15
    names = [c.name for c in visible]
    for n in ALL_NAMES:
        assert n in names, f"缺少命令: {n}"


def test_register_builtins_no_collision():
    reg = Registry()
    register_builtins(reg)  # 不抛


# ── Kind 分类 ───────────────────────────────────────


def test_register_builtins_kind_counts():
    reg = Registry()
    register_builtins(reg)
    kinds = {c.name: c.kind for c in reg.visible()}
    # 6 LOCAL
    local = ["help", "hooks", "memory", "permission", "session", "status"]
    for n in local:
        assert kinds[n] == Kind.LOCAL, f"{n} 应为 LOCAL"
    # 5 UI
    ui = ["clear", "compact", "exit", "plan", "resume"]
    for n in ui:
        assert kinds[n] == Kind.UI, f"{n} 应为 UI"
    # 2 PROMPT
    prompt = ["do", "review"]
    for n in prompt:
        assert kinds[n] == Kind.PROMPT, f"{n} 应为 PROMPT"


# ── handler 在 NopUI 上不抛 ──────────────────────────


@pytest.mark.asyncio
async def test_all_handlers_run_on_nop_ui():
    reg = Registry()
    register_builtins(reg)
    ui = NopUI()
    for cmd in reg.visible():
        await cmd.handler(ui, "")


# ── 行为断言（RecordingUI）───────────────────────────


@pytest.mark.asyncio
async def test_handle_status_prints_all_keys():
    reg = Registry()
    register_builtins(reg)
    ui = RecordingUI()
    cmd = reg.lookup("status")
    assert cmd is not None
    await cmd.handler(ui, "")
    assert len(ui.prints) == 1
    text = ui.prints[0]
    for key in ["Mode:", "Tokens:", "Tools:", "Memories:", "Model:", "Directory:"]:
        assert key in text, f"缺少 key: {key}"


@pytest.mark.asyncio
async def test_handle_compact_blocks_when_busy():
    reg = Registry()
    register_builtins(reg)
    ui = RecordingUI()
    ui._idle = False
    cmd = reg.lookup("compact")
    assert cmd is not None
    await cmd.handler(ui, "")
    assert len(ui.errors) == 1
    assert "等待" in ui.errors[0]
    assert ui.force_compacts == 0


@pytest.mark.asyncio
async def test_handle_do_sets_mode_and_injects():
    reg = Registry()
    register_builtins(reg)
    ui = RecordingUI()
    cmd = reg.lookup("do")
    assert cmd is not None
    await cmd.handler(ui, "")
    assert Mode.DEFAULT in ui.modes
    assert len(ui.injects) == 1
    assert ui.injects[0][0] == "/do"


@pytest.mark.asyncio
async def test_handle_help_lists_all_names():
    reg = Registry()
    register_builtins(reg)
    ui = RecordingUI()
    cmd = reg.lookup("help")
    assert cmd is not None
    await cmd.handler(ui, "")
    assert len(ui.prints) == 1
    text = ui.prints[0]
    for n in ALL_NAMES:
        assert f"/{n}" in text, f"缺少 /{n}"


@pytest.mark.asyncio
async def test_handle_review_injects():
    reg = Registry()
    register_builtins(reg)
    ui = RecordingUI()
    cmd = reg.lookup("review")
    assert cmd is not None
    await cmd.handler(ui, "")
    assert len(ui.injects) == 1
    assert ui.injects[0][0] == "/review"
    assert "审查" in ui.injects[0][1]
