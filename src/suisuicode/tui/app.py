from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import time as _time
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.message import Message as TMessage
from textual.theme import Theme
from textual.widgets import Markdown, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from suisuicode import __version__
from suisuicode.agent import (
    Agent,
    SessionRuntime,
)
from suisuicode.conversation import Conversation
from suisuicode.hook import Event as HookEvent, Engine as HookEngine
from suisuicode.llm import new_provider
from suisuicode.permission import Mode
from suisuicode.permission.engine import Engine
from suisuicode.tool import Registry
from suisuicode.tui.at_file import expand_at_refs, scan_files_for_at
from suisuicode.tui.completion_popup import CompletionPopup
from suisuicode.tui.event_adapter import (
    AppCompactNotification,
    AppErrorEvent,
    AppHookEvent,
    AppLoopComplete,
    AppPermissionRequest,
    AppRetryEvent,
    AppStreamText,
    AppToolResultEvent,
    AppToolUseEvent,
    AppTurnComplete,
    AppUsageEvent,
    adapt_events,
)
from suisuicode.tui.permission_dialog import InlinePermissionWidget
from suisuicode.tui.session_dialog import InlineResumeWidget

if TYPE_CHECKING:
    from suisuicode.config import ProviderConfig

# ── 常量 ─────────────────────────────────────────────

C_BRAND = "#7C5CFC"
MAX_TRUNCATED_LINES = 20

COLLAPSIBLE_TOOLS = {"read_file", "glob", "grep"}

SPINNER_FRAMES = "⠋⠉⠏⠇⠆⠄⠠⠦⠧⠏"

THINKING_VERBS = [
    "Accomplishing", "Architecting", "Baking", "Beboppin'", "Befuddling",
    "Bloviating", "Boogieing", "Boondoggling", "Bootstrapping", "Brewing",
    "Calculating", "Canoodling", "Caramelizing", "Cascading", "Cerebrating",
    "Choreographing", "Churning", "Coalescing", "Cogitating", "Combobulating",
    "Composing", "Computing", "Concocting", "Considering", "Contemplating",
    "Cooking", "Crafting", "Creating", "Crunching", "Crystallizing",
    "Cultivating", "Deciphering", "Deliberating", "Dilly-dallying",
    "Discombobulating", "Doodling", "Elucidating", "Enchanting", "Envisioning",
    "Fermenting", "Finagling", "Flambeing", "Flibbertigibbeting", "Flummoxing",
    "Forging", "Frolicking", "Gallivanting", "Garnishing", "Generating",
    "Germinating", "Grooving", "Harmonizing", "Hatching", "Honking",
    "Hullaballooing", "Ideating", "Imagining", "Improvising", "Incubating",
    "Inferring", "Infusing", "Kneading", "Lollygagging", "Manifesting",
    "Marinating", "Meandering", "Metamorphosing", "Mewing", "Moonwalking",
    "Moseying", "Mulling", "Musing", "Noodling", "Orbiting",
    "Orchestrating", "Percolating", "Philosophising", "Pondering",
    "Pontificating", "Pouncing", "Purring", "Puzzling", "Razzle-dazzling",
    "Ruminating", "Scampering", "Simmering", "Sketching", "Spelunking",
    "Spinning", "Sprouting", "Synthesizing", "Thinking", "Tinkering",
    "Transfiguring", "Transmuting", "Undulating", "Unfurling", "Unravelling",
    "Vibing", "Wandering", "Whisking", "Working", "Wrangling", "Zigzagging",
]

_MODE_CYCLE = [Mode.DEFAULT, Mode.ACCEPT_EDITS, Mode.PLAN, Mode.BYPASS]

_MODE_COLORS = {
    Mode.DEFAULT: "dim",
    Mode.ACCEPT_EDITS: "green",
    Mode.PLAN: "yellow",
    Mode.BYPASS: "red",
}

_MODE_DISPLAY = {
    Mode.DEFAULT: "default",
    Mode.ACCEPT_EDITS: "accept-edits",
    Mode.PLAN: "plan",
    Mode.BYPASS: "YOLO",
}

_SUISUICODE_THEME = Theme(
    name="suisuicode",
    primary="#7C5CFC",
    background="#1a1a1a",
    surface="#1a1a1a",
    panel="#1a1a1a",
    dark=True,
)


# ── Helper 函数 ──────────────────────────────────────


def _is_subagent_tool(tool_name: str) -> bool:
    return tool_name == "Agent"


def _tool_title(tool_name: str, arguments: dict) -> str:
    if tool_name == "read_file":
        path = os.path.basename(arguments.get("file_path", "") or arguments.get("path", ""))
        return f"Read {path}" if path else "Read"
    if tool_name == "write_file":
        path = os.path.basename(arguments.get("file_path", "") or arguments.get("path", ""))
        content = arguments.get("content", "")
        lines = content.count("\n") + 1 if content else 0
        return f"Write {path} ({lines} lines)" if path else "Write"
    if tool_name == "edit_file":
        path = os.path.basename(arguments.get("file_path", "") or arguments.get("path", ""))
        return f"Edit {path}" if path else "Edit"
    if tool_name == "bash":
        cmd = arguments.get("command", "")
        short = cmd[:50] + "..." if len(cmd) > 50 else cmd
        return f"Bash: {short}" if short else "Bash"
    if tool_name == "glob":
        return f"Glob: {arguments.get('pattern', '')}"
    if tool_name == "grep":
        return f"Grep: {arguments.get('pattern', '')}"
    return tool_name


def _format_detail(tool_name: str, arguments: dict, output: str) -> str:
    parts: list[str] = []

    if tool_name == "bash":
        parts.append(f"  IN   {arguments.get('command', '')}")
        parts.append("")
        for line in output.splitlines():
            parts.append(f"  OUT  {line}")
    elif tool_name == "edit_file":
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            escaped = escape(line)
            if line.startswith("+ "):
                parts.append(f"  [green]{escaped}[/]")
            elif line.startswith("- "):
                parts.append(f"  [red]{escaped}[/]")
            else:
                parts.append(f"  [dim]{escaped}[/]")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  [dim]... ({total - MAX_TRUNCATED_LINES} more lines)[/]")
    elif tool_name in ("read_file", "write_file"):
        parts.append(f"  {arguments.get('file_path', arguments.get('path', ''))}")
        parts.append("")
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f"  {line}")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  ... ({total - MAX_TRUNCATED_LINES} more lines)")
    else:
        for line in output.splitlines()[:MAX_TRUNCATED_LINES]:
            parts.append(f"  {line}")
        total = output.count("\n") + 1
        if total > MAX_TRUNCATED_LINES:
            parts.append(f"  ... ({total - MAX_TRUNCATED_LINES} more lines)")

    return "\n".join(parts)


def _to_past_tense(verb: str) -> str:
    if verb.endswith("ing"):
        stem = verb[:-3]
        if stem.endswith("e"):
            return stem + "d"
        if stem and stem[-1] in "atutitet":
            return stem + "ed"
        return stem + "ed"
    return verb + "ed"


# ── ChatInput ────────────────────────────────────────


class ChatInput(TextArea):
    """自定义输入框：Enter 发送，Ctrl+J 换行，Up/Down 历史，Tab 补全。

    提交后清空文本并强制刷新布局，避免 TextArea 内部多行状态残留导致高度不缩回。
    """

    class Submitted(TMessage):
        """用户提交了文本。"""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class TabComplete(TMessage):
        """用户请求 Tab 补全。"""
        def __init__(self, text: str) -> None:
            super().__init__()
            self.text = text

    class AtFileRequest(TMessage):
        """用户输入 @ 需要文件补全。"""
        def __init__(self, prefix: str) -> None:
            super().__init__()
            self.prefix = prefix

    class SlashMenuUpdate(TMessage):
        """用户输入 / 需要命令补全。"""
        def __init__(self, prefix: str | None) -> None:
            super().__init__()
            self.prefix = prefix

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.cursor_blink = False
        self._history: list[str] = []
        self._history_index: int = -1
        self._history_draft: str = ""

    def load_history(self, work_dir: str) -> None:
        p = Path(work_dir) / ".suisuicode" / "history"
        if p.exists():
            try:
                self._history = [line for line in p.read_text("utf-8").splitlines() if line.strip()]
            except Exception:
                pass

    def _persist(self, text: str) -> None:
        try:
            d = Path(os.getcwd()) / ".suisuicode"
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "history", "a", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass

    def _popup(self) -> CompletionPopup | None:
        try:
            return self.app.query_one(CompletionPopup)
        except Exception:
            return None

    def _submit(self, text: str) -> None:
        """清除输入并提交消息，并强制缩回单行高度。"""
        self.clear()
        self.styles.height = "1"
        self.post_message(self.Submitted(text))

    def _on_key(self, event: Key) -> None:
        key = event.key

        if key == "enter":
            event.stop()
            event.prevent_default()
            popup = self._popup()
            if popup is not None and popup.is_visible:
                selected = popup.get_selected()
                popup.hide()
                if selected:
                    self._history.append(selected)
                    self._persist(selected)
                    self._history_index = -1
                    self._history_draft = ""
                    self._submit(selected)
                return
            text = self.text
            if text.strip():
                self._history.append(text)
                self._persist(text)
                self._history_index = -1
                self._history_draft = ""
                self._submit(text)
            return

        if key == "ctrl+j":
            event.stop()
            self.insert("\n")
            return

        if key == "tab":
            event.stop()
            popup = self._popup()
            if popup is not None and popup.is_visible:
                selected = popup.get_selected()
                if selected:
                    popup.hide()
                    self.clear()
                    self.insert(selected + " ")
                return
            text = self.text.strip()
            if text.startswith("/"):
                self.post_message(self.TabComplete(text))
            return

        if key == "escape":
            popup = self._popup()
            if popup is not None and popup.is_visible:
                event.stop()
                popup.hide()
            return

        if key == "up":
            popup = self._popup()
            if popup is not None and popup.is_visible:
                event.stop()
                popup.move_up()
                return
            if not self._history:
                return
            event.stop()
            if self._history_index == -1:
                self._history_draft = self.text
                self._history_index = len(self._history) - 1
            elif self._history_index > 0:
                self._history_index -= 1
            else:
                return
            self.text = self._history[self._history_index]
            return

        if key == "down":
            popup = self._popup()
            if popup is not None and popup.is_visible:
                event.stop()
                popup.move_down()
                return
            if self._history_index == -1:
                return
            event.stop()
            if self._history_index < len(self._history) - 1:
                self._history_index += 1
            else:
                self._history_index = -1
            self.text = self._history[self._history_index] if self._history_index >= 0 else self._history_draft
            return

        super()._on_key(event)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        # 每次内容变化时恢复 height: auto（_submit 会设为 height: 1）
        self.styles.height = "auto"

        text = self.text
        if text.startswith("/") and self._history_index < 0:
            prefix = text[1:]
            if " " not in prefix and "\n" not in prefix:
                self.post_message(self.SlashMenuUpdate(prefix))
            else:
                self.post_message(self.SlashMenuUpdate(None))
        else:
            self.post_message(self.SlashMenuUpdate(None))

        at_idx = text.rfind("@")
        if at_idx >= 0:
            after = text[at_idx + 1:]
            if " " not in after and "\n" not in after and after:
                self.post_message(self.AtFileRequest(after))


# ── ToolCallBlock ────────────────────────────────────


class ToolCallBlock(Static, can_focus=True):
    """可折叠的工具调用块。EditFile 默认展开并 diff 着色。"""

    def __init__(self, tool_name: str, arguments: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.tool_name = tool_name
        self._arguments = arguments
        self._title = _tool_title(tool_name, arguments)
        self._full_output = ""
        self._is_error = False
        self._elapsed = 0.0
        self._collapsed = True
        self._loading = True
        self._render_loading()

    def _render_loading(self) -> None:
        self.update(f"[dim]●[/dim] {self._title} ...")
        self.add_class("tool-block-loading")

    def set_result(self, output: str, is_error: bool, elapsed: float) -> None:
        self._full_output = output
        self._is_error = is_error
        self._elapsed = elapsed
        self._loading = False
        self.remove_class("tool-block-loading")
        if is_error:
            self.add_class("tool-block-error")
        if self.tool_name == "edit_file" and not is_error:
            self._collapsed = False
            self._render_expanded()
        else:
            self._collapsed = True
            self._render_collapsed()

    def _render_collapsed(self) -> None:
        if self._is_error:
            self.update(f"[bold red]✗[/bold red] [dim]{self._title} · {self._elapsed:.1f}s[/dim]")
        else:
            self.update(f"[bold green]●[/bold green] [dim]{self._title} · {self._elapsed:.1f}s[/dim]")

    def _render_expanded(self) -> None:
        if self._is_error:
            header = f"[bold red]✗[/bold red] [dim]{self._title} · {self._elapsed:.1f}s[/dim]"
        else:
            header = f"[bold green]●[/bold green] [dim]{self._title} · {self._elapsed:.1f}s[/dim]"
        detail = _format_detail(self.tool_name, self._arguments, self._full_output)
        self.update(f"{header}\n{detail}")

    def on_click(self) -> None:
        if self._loading:
            return
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._render_collapsed()
        else:
            self._render_expanded()


# ── ToolGroupSummary ─────────────────────────────────


class ToolGroupSummary(Static, can_focus=True):
    """折叠 2+ 个可折叠工具为一行摘要。"""

    def __init__(self, count: int, total_elapsed: float, **kwargs) -> None:
        label = f"[bold green]●[/bold green] Done ({count} tool uses · {total_elapsed:.1f}s)  (ctrl+o to expand)"
        super().__init__(label, **kwargs)
        self._count = count
        self._total = total_elapsed
        self._expanded = False

    def _refresh_display(self) -> None:
        if self._expanded:
            self.update(f"[bold green]▼[/bold green] Done ({self._count} tool uses · {self._total:.1f}s)")
        else:
            self.update(
                f"[bold green]●[/bold green] Done ({self._count} tool uses · {self._total:.1f}s)"
                "  (ctrl+o to expand)"
            )

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._refresh_display()

    def on_click(self) -> None:
        self.toggle()


# ── SubAgentBlock ────────────────────────────────────


class SubAgentBlock(Static, can_focus=True):
    """SubAgent 执行状态块。"""

    def __init__(self, agent_type: str, description: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._agent_type = agent_type or "agent"
        self._description = description[:60] if description else ""
        self._done = False
        self._is_error = False
        self._elapsed = 0.0
        self._collapsed = True
        self._result_preview = ""
        self._tool_count = 0
        self._render_running()

    def _render_running(self) -> None:
        desc = f"({self._description})" if self._description else ""
        self.update(f"● {self._agent_type}{desc}\n     Running...")

    def set_result(self, output: str, is_error: bool, elapsed: float) -> None:
        self._done = True
        self._is_error = is_error
        self._elapsed = elapsed
        self._result_preview = output[:300] if output else ""
        self._parse_stats(output)
        self._render_done()

    def _parse_stats(self, output: str) -> None:
        m = re.search(r"(\d+)\s+tool", output[:200])
        if m:
            self._tool_count = int(m.group(1))

    def _render_done(self) -> None:
        desc = f"({self._description})" if self._description else ""
        tool_info = f"{self._tool_count} tool uses · " if self._tool_count else ""
        if self._collapsed:
            self.update(
                f"[bold green]●[/bold green] {self._agent_type}{desc}\n"
                f"    L  Done ({tool_info}{self._elapsed:.1f}s)  (ctrl+o to expand)"
            )
        else:
            self.update(
                f"[bold green]●[/bold green] {self._agent_type}{desc}\n"
                f"    L  Done ({tool_info}{self._elapsed:.1f}s)\n"
                f"  {self._result_preview}"
            )

    def on_click(self) -> None:
        if not self._done:
            return
        self._collapsed = not self._collapsed
        self._render_done()


# ── SuisuiCodeApp ────────────────────────────────────


class SuisuiCodeApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "SuisuiCode"
    INLINE_PADDING = 0

    BINDINGS = [
        Binding("ctrl+c", "handle_ctrl_c", "Cancel/Quit", priority=False),
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("shift+tab", "cycle_mode", "Cycle mode", priority=True),
        Binding("ctrl+o", "toggle_tool_blocks", "Toggle tools", priority=True),
    ]

    def __init__(
        self,
        providers: list[ProviderConfig],
        version: str,
        registry: Registry,
        engine: Engine,
        mcp_servers: dict | None = None,
        *,
        runtime: SessionRuntime | None = None,
        providers_config: list[ProviderConfig] | None = None,
        writer=None,
        mem_mgr=None,
        instruction_text: str = "",
        memory_text: str = "",
        hook_engine: HookEngine | None = None,
        cmd_registry=None,
        provider_instance=None,
        conv: Conversation | None = None,
        catalog=None,
        skill_executor=None,
        task_mgr=None,
        subagent_catalog=None,
        worktree_mgr=None,
        team_mgr=None,
        active_cwd: str = "",
        coordinator_mode: bool = False,
        driver_class: type | None = None,
        **kwargs,
    ) -> None:
        super().__init__(driver_class=driver_class)
        self.providers = providers
        self._version = version
        self._initial_permission_mode = Mode.DEFAULT
        self._mcp_server_configs = mcp_servers or {}
        self.hook_engine = hook_engine

        # Provider config for multi-provider selection
        self._providers_config = providers_config

        # suisuicode pre-built subsystems
        self._engine = engine
        self._tool_registry = registry
        self._cmd_registry = cmd_registry
        self._runtime = runtime
        self._provider_instance = provider_instance
        self._conv = conv
        self._catalog = catalog
        self._skill_executor = skill_executor
        self._task_mgr = task_mgr
        self._subagent_catalog = subagent_catalog
        self._worktree_mgr = worktree_mgr
        self._team_mgr = team_mgr
        self._writer = writer
        self._mem_mgr = mem_mgr
        self._instruction_text = instruction_text
        self._memory_text = memory_text
        self._active_cwd = active_cwd
        self._coordinator_mode = coordinator_mode

        # Agent
        self.agent: Agent | None = None
        self._selected_provider: ProviderConfig | None = None

        # Streaming state
        self._streaming = False
        self._thinking_start: float = 0.0
        self._thinking_verb: str = ""
        self._spinner_idx: int = 0
        self._spinner_timer = None
        self._spinner_label: Static | None = None
        self._agent_task: asyncio.Task | None = None
        self._subagent_task: asyncio.Task | None = None
        self._current_streaming_label: Static | None = None
        self._current_ai_row: Vertical | None = None
        self._current_accumulated_text: str = ""

        # MCP
        self._mcp_instructions: str = ""
        self._mcp_instructions_ok: bool = False
        self._mcp_connecting: bool = False
        self._mcp_manager: object | None = None
        self._mcp_init_task: asyncio.Task | None = None
        self._mcp_server_info: str = ""

        # Teammate
        self._teammate_tree = None
        self._teammate_timer = None

        # Plan mode
        self._pre_plan_mode: Mode | None = None
        self._has_exited_plan_mode: bool = False

        # Session
        self._session_writer = writer
        self._exit_requested: bool = False

        # Pending
        self._pending_perm_request: AppPermissionRequest | None = None
        self._pending_plan_choice: asyncio.Future | None = None
        self._notification_check_task: asyncio.Task | None = None

    # ── main_agent 属性（cli.py 兼容）───────────────────

    @property
    def main_agent(self) -> Agent | None:
        return self.agent

    # ── compose ──────────────────────────────────────

    @staticmethod
    def _make_banner(model: str = "", work_dir: str = "") -> str:
        lines = [
            f"[bold purple] /\\_/\\    [/bold purple][dim]SuisuiCode v{__version__}[/dim]",
            f"[bold purple]( o.o )   [/bold purple][dim]{model}[/dim]",
            f"[bold purple] > ^ <    [/bold purple][dim]{work_dir}[/dim]",
        ]
        return "\n".join(lines)

    def compose(self) -> ComposeResult:
        yield Static(self._make_banner(), id="title-bar")
        yield Static("", id="mcp-status")

        if len(self.providers) > 1:
            with Vertical(id="provider-select"):
                yield Static("Select a Provider", id="select-label")
                yield OptionList(
                    *[
                        Option(f"{p.name}  [{p.model}]", id=p.name)
                        for p in self.providers
                    ],
                    id="provider-list",
                )
        yield VerticalScroll(id="chat-area")
        # Status line: spinner / "✻ Worked for Xs" between chat and input
        yield Static("", id="status-line")
        with Vertical(id="input-area"):
            with Horizontal(id="input-row"):
                yield Static(">", id="input-prompt")
                yield ChatInput(id="chat-input")
            with Horizontal(id="status-bar"):
                yield Static("  default", id="mode-label")
                yield Static("", id="teammates-label")
                yield Static("", id="model-label")
            yield CompletionPopup()

    # ── on_mount ──────────────────────────────────────

    def on_mount(self) -> None:
        self.register_theme(_SUISUICODE_THEME)
        self.theme = "suisuicode"

        if len(self.providers) == 1:
            self._select_provider(self.providers[0])
        else:
            self.query_one("#chat-area").display = False
            self.query_one("#input-area").display = False

        # Dispatch SessionStart hook
        if self.hook_engine:
            asyncio.ensure_future(
                self.hook_engine.dispatch(
                    HookEvent.SESSION_START,
                    {"event": "SessionStart"},
                )
            )

    # ── Provider 选择 ────────────────────────────────

    def _select_provider(self, provider: ProviderConfig) -> None:
        self._selected_provider = provider
        work_dir = os.getcwd()

        # Create LLM provider if not pre-built
        if self._provider_instance is None:
            try:
                self._provider_instance = new_provider(provider)
            except Exception as e:
                self._show_error(str(e))
                return

        # Init command registry if not set
        if self._cmd_registry is None:
            from suisuicode.command.builtins import register_builtins
            from suisuicode.command.registry import Registry as CmdRegistry
            reg = CmdRegistry()
            register_builtins(reg)
            self._cmd_registry = reg

        # Create default Conversation if not provided
        if self._conv is None:
            self._conv = Conversation()
            if self._writer:
                self._conv.on_append = lambda msg: self._writer.append(msg)
                self._conv.on_replace = lambda msgs: self._writer.replace_all(msgs)

        # Ensure Agent exists
        self._ensure_agent()

        # Init skills
        self._init_skills()

        # Init skill slash commands
        self._init_skill_commands()

        # Update UI
        self.query_one("#model-label", Static).update(provider.model)
        self.query_one("#title-bar", Static).update(
            self._make_banner(provider.model, work_dir)
        )
        self._update_mcp_status()
        self._update_mode_label()

        # Show chat/input
        select = self.query("#provider-select")
        if select:
            select.first().display = False
        self.query_one("#chat-area").display = True
        self.query_one("#input-area").display = True
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.placeholder = "Send a message..."
        chat_input.load_history(work_dir)
        chat_input.focus()

        # Notification polling
        self._notification_check_task = asyncio.create_task(
            self._start_notification_polling()
        )

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "provider-list":
            provider = self.providers[event.option_index]
            self._select_provider(provider)

    # ── Agent 初始化 ─────────────────────────────────

    def _ensure_agent(self) -> None:
        if self.agent is not None:
            return
        if self._provider_instance is None:
            return

        self.agent = Agent(
            provider=self._provider_instance,
            registry=self._tool_registry,
            engine=self._engine,
            version=__version__,
            runtime=self._runtime,
            memory_manager=self._mem_mgr,
            instruction_text=self._instruction_text,
            memory_text=self._memory_text,
            catalog=self._catalog,
            hook_engine=self.hook_engine,
        )

    def _init_skills(self) -> None:
        """初始化 Skill 系统。"""
        if self._catalog is not None and self.agent is not None:
            try:
                skills_list = self._catalog.list()
                if skills_list:
                    lines = ["You can use the following Skills:", ""]
                    for s in skills_list:
                        lines.append(f"- {s.name}: {s.description}")
                    lines.append("")
                    lines.append(
                        "If the user's request matches a Skill, "
                        "call LoadSkill to activate it."
                    )
                    self.agent.set_skill_catalog("\n".join(lines))
            except Exception:
                pass

    def _init_skill_commands(self) -> None:
        """为每个 Skill 注册 slash 命令。"""
        if self._catalog is None or self._cmd_registry is None:
            return
        try:
            from suisuicode.command.skills import register_skills_as_commands
            register_skills_as_commands(
                self._cmd_registry,
                self._catalog,
                self._skill_executor,
            )
        except Exception:
            pass

    # ── on_chat_input_submitted ──────────────────────

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        text = event.text.strip()
        if self._streaming and not text.startswith("/"):
            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
                try:
                    await self._agent_task
                except (asyncio.CancelledError, Exception):
                    pass
            self._finish_streaming()
            self._show_system_message("(response interrupted)")
        await self._dispatch_command(text)

    def on_chat_input_tab_complete(self, event: ChatInput.TabComplete) -> None:
        if self._cmd_registry is None:
            return
        prefix = event.text.lstrip("/")
        matches = self._cmd_registry.prefix_match(prefix)
        if not matches:
            return
        popup = self.query_one(CompletionPopup)
        if len(matches) == 1:
            input_widget = self.query_one("#chat-input", ChatInput)
            input_widget.clear()
            input_widget.insert(f"/{matches[0].name} ")
        else:
            pairs = [(f"/{c.name}  [dim]{c.description}[/]", f"/{c.name}") for c in matches]
            popup.show_pairs(pairs)

    def on_chat_input_slash_menu_update(self, event: ChatInput.SlashMenuUpdate) -> None:
        popup = self.query_one(CompletionPopup)
        if event.prefix is None or self._cmd_registry is None:
            popup.hide()
            return
        matches = self._cmd_registry.prefix_match(event.prefix)
        if not matches:
            popup.hide()
            return
        pairs = [(f"/{c.name}  [dim]{c.description}[/]", f"/{c.name}") for c in matches]
        popup.show_pairs(pairs)

    def on_chat_input_at_file_request(self, event: ChatInput.AtFileRequest) -> None:
        work_dir = self._active_cwd or os.getcwd()
        matches = scan_files_for_at(event.prefix, work_dir)
        if matches:
            popup = self.query_one(CompletionPopup)
            popup.show([f"@{m}" for m in matches])

    def on_completion_popup_selected(self, event: CompletionPopup.Selected) -> None:
        input_widget = self.query_one("#chat-input", ChatInput)
        selected = event.value
        text = input_widget.text
        if selected.startswith("@"):
            at_idx = text.rfind("@")
            if at_idx >= 0:
                input_widget.clear()
                input_widget.insert(text[:at_idx] + selected + " ")
                input_widget.focus()
                return
        input_widget.clear()
        input_widget.insert(selected + " ")
        input_widget.focus()

    # ── 命令分发 ─────────────────────────────────────

    async def _dispatch_command(self, text: str) -> None:
        from suisuicode.command.dispatch import parse

        name, args, is_slash = parse(text)

        if not is_slash:
            if self._streaming or self.agent is None:
                return
            self._agent_task = asyncio.create_task(self._send_message(text))
            return

        if name == "" or self._cmd_registry is None:
            if self._cmd_registry:
                commands = self._cmd_registry.visible()
                lines = ["Available commands:"]
                for cmd in commands:
                    aliases_str = ", ".join(f"/{a}" for a in cmd.aliases) if cmd.aliases else ""
                    name_part = f"/{cmd.name}"
                    if aliases_str:
                        name_part += f", {aliases_str}"
                    lines.append(f"  {name_part:<24} {cmd.description}")
                self._show_system_message("\n".join(lines))
            return

        cmd = self._cmd_registry.lookup(name)
        if cmd is None:
            self._show_system_message(f"Unknown command: /{name}  (type /help for available commands)")
            return

        # Handle special UI commands
        if name == "resume":
            self._open_resume_menu()
            return
        if name == "clear":
            await self._do_clear_and_new_session()
            return
        if name == "exit":
            self._exit_requested = True
            self.exit()
            return
        if name == "compact":
            self.ui_force_compact()
            return

        # Generic command handler
        try:
            await cmd.handler(self, args)
        except Exception as e:
            self._show_error(f"Command failed: {e}")

    # ── _send_message（核心事件循环）─────────────────

    async def _send_message(self, text: str, is_notification: bool = False) -> None:
        if self.agent is None:
            self._ensure_agent()
        if self.agent is None:
            return

        self._streaming = True
        chat = self.query_one("#chat-area", VerticalScroll)
        input_widget = self.query_one("#chat-input", ChatInput)

        # Expand @file references
        work_dir = self._active_cwd or os.getcwd()
        if text and "@" in text:
            text = expand_at_refs(text, work_dir)

        # Mount user message
        if text:
            user_row = Vertical(classes="user-row")
            await chat.mount(user_row)
            user_bubble = Static(
                f"[bold #7C5CFC]❯[/bold #7C5CFC] [bold]{text}[/bold]",
                classes="message user-message"
            )
            await user_row.mount(user_bubble)
            self.call_after_refresh(chat.scroll_end, animate=False)

            self._conv.add_user(text)
            if self._writer:
                from suisuicode.llm import Message
                self._writer.append(Message(role="user", content=text))

        # MCP instructions
        if self._mcp_instructions and not self._mcp_instructions_ok:
            self._conv.add_system_message(self._mcp_instructions)
            self._mcp_instructions_ok = True

        # AI row / streaming label — created on first text or TurnComplete
        ai_row: Vertical | None = None
        streaming_label: Static | None = None

        accumulated_text = ""
        tool_blocks: dict[str, ToolCallBlock] = {}

        # Spinner — shown in status-line between chat and input
        self._thinking_start = _time.monotonic()
        self._thinking_verb = random.choice(THINKING_VERBS)
        self._spinner_idx = 0
        self._spinner_label = self.query_one("#status-line", Static)
        self._spinner_label.update(
            f"[dim]{SPINNER_FRAMES[0]} {self._thinking_verb}...[/dim]"
        )
        self._start_spinner()

        try:
            mode = self.agent._engine.start_mode if self.agent._engine else Mode.DEFAULT
            async for adapted in adapt_events(self.agent.run(self._conv, mode=mode)):
                if isinstance(adapted, AppStreamText):
                    if streaming_label is None:
                        ai_row = Vertical(classes="ai-row")
                        await chat.mount(ai_row)
                        streaming_label = Static("", classes="message ai-message")
                        await ai_row.mount(streaming_label)
                    accumulated_text += adapted.text
                    streaming_label.update(accumulated_text)
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(adapted, AppToolUseEvent):
                    if streaming_label is None:
                        ai_row = Vertical(classes="ai-row")
                        await chat.mount(ai_row)
                    if accumulated_text:
                        if streaming_label is not None:
                            await streaming_label.remove()
                        prefix = Static("", classes="message")
                        await ai_row.mount(prefix)
                        md = Markdown(accumulated_text, classes="message ai-message")
                        await ai_row.mount(md)
                        streaming_label = None
                        accumulated_text = ""
                    elif streaming_label is not None:
                        await streaming_label.remove()
                        streaming_label = None

                    if _is_subagent_tool(adapted.tool_name):
                        agent_type = adapted.arguments.get("subagent_type", "")
                        desc = adapted.arguments.get("description", "")
                        block = SubAgentBlock(
                            agent_type or "agent",
                            desc,
                            classes="tool-block subagent-block",
                        )
                    else:
                        block = ToolCallBlock(
                            adapted.tool_name, adapted.arguments, classes="tool-block"
                        )
                    await ai_row.mount(block)
                    tool_blocks[adapted.tool_id] = block
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(adapted, AppToolResultEvent):
                    block = tool_blocks.get(adapted.tool_id)
                    if block:
                        block.set_result(adapted.output, adapted.is_error, adapted.elapsed)
                    self.call_after_refresh(chat.scroll_end, animate=False)

                elif isinstance(adapted, AppPermissionRequest):
                    # 挂载权限确认 widget，事件循环自然暂停等待下一个 Agent 事件
                    # Agent.run() 内部在等待 future 解析，用户响应后 Agent 继续产出事件
                    asyncio.create_task(self._handle_permission_request(adapted))

                elif isinstance(adapted, AppTurnComplete):
                    # Group collapsible tools from the turn that just finished
                    collapsible = [
                        (tid, blk) for tid, blk in tool_blocks.items()
                        if isinstance(blk, ToolCallBlock)
                        and blk.tool_name in COLLAPSIBLE_TOOLS
                        and not blk._loading
                    ]
                    if len(collapsible) >= 2 and ai_row is not None:
                        total_elapsed = sum(b._elapsed for _, b in collapsible)
                        summary = ToolGroupSummary(
                            len(collapsible), total_elapsed,
                            classes="tool-block",
                        )
                        for _, blk in collapsible:
                            blk.display = False
                        await ai_row.mount(summary)

                    tool_blocks.clear()
                    # Prepare fresh row for next turn's output
                    ai_row = Vertical(classes="ai-row")
                    await chat.mount(ai_row)
                    streaming_label = Static("", classes="message ai-message")
                    await ai_row.mount(streaming_label)
                    accumulated_text = ""

                elif isinstance(adapted, AppLoopComplete):
                    # Flush accumulated text as Markdown first
                    if accumulated_text and streaming_label is not None:
                        await streaming_label.remove()
                        streaming_label = None
                        md = Markdown(accumulated_text, classes="message ai-message")
                        await ai_row.mount(md)
                        accumulated_text = ""
                    elif streaming_label is not None and not accumulated_text:
                        await streaming_label.remove()
                        streaming_label = None
                    # Then mount done label at the end
                    total_time = _time.monotonic() - self._thinking_start
                    done_text = f"[bold green]●[/bold green] [dim]{_to_past_tense(self._thinking_verb)} · {total_time:.0f}s[/dim]"
                    done_label = Static(
                        done_text,
                        classes="message thinking-done",
                    )
                    if ai_row is None:
                        ai_row = Vertical(classes="ai-row")
                        await chat.mount(ai_row)
                    await ai_row.mount(done_label)
                    # Plan mode: show approval widget
                    if self.agent._engine.start_mode == Mode.PLAN:
                        await self._show_plan_approval()

                elif isinstance(adapted, AppErrorEvent):
                    if accumulated_text:
                        if streaming_label is not None:
                            await streaming_label.remove()
                        if ai_row is None:
                            ai_row = Vertical(classes="ai-row")
                            await chat.mount(ai_row)
                        md = Markdown(accumulated_text, classes="message ai-message")
                        await ai_row.mount(md)
                        streaming_label = None
                        accumulated_text = ""
                    self._show_error(adapted.message)

                elif isinstance(adapted, AppCompactNotification):
                    self._show_system_message(adapted.message)

                elif isinstance(adapted, AppRetryEvent):
                    self._show_system_message(f"↻ {adapted.reason}")

                elif isinstance(adapted, AppUsageEvent):
                    pass  # Token info handled separately

                elif isinstance(adapted, AppHookEvent):
                    status = "v" if adapted.success else "x"
                    self._show_system_message(
                        f"Hook [{adapted.hook_id}] {status} {adapted.output}"
                    )

            # Flush remaining text (fallback for cancel/error paths)
            if accumulated_text:
                if streaming_label is not None:
                    await streaming_label.remove()
                if ai_row is None:
                    ai_row = Vertical(classes="ai-row")
                    await chat.mount(ai_row)
                md = Markdown(accumulated_text, classes="message ai-message")
                await ai_row.mount(md)
            elif streaming_label is not None:
                await streaming_label.remove()
            if ai_row is None:
                ai_row = Vertical(classes="ai-row")
                await chat.mount(ai_row)

            self.call_after_refresh(chat.scroll_end, animate=False)

        except asyncio.CancelledError:
            if accumulated_text:
                if streaming_label is not None:
                    await streaming_label.remove()
                if ai_row is None:
                    ai_row = Vertical(classes="ai-row")
                    await chat.mount(ai_row)
                md = Markdown(
                    accumulated_text + "\n\n*Interrupted*",
                    classes="message ai-message",
                )
                await ai_row.mount(md)
            self._show_system_message("Operation cancelled")
        except Exception as e:
            self._show_error(str(e))
        finally:
            self._finish_streaming()
            input_widget.focus()

        # Process task notifications
        await self._process_task_notifications()

    # ── 权限处理 ─────────────────────────────────────

    async def _handle_permission_request(self, request: AppPermissionRequest) -> None:
        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlinePermissionWidget(request.tool_name, request.description)
        self._pending_perm_request = request
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_permission_widget_responded(
        self, event: InlinePermissionWidget.Responded
    ) -> None:
        req = self._pending_perm_request
        if req is not None and not req.respond.done():
            req.respond.set_result(event.outcome)
            self._pending_perm_request = None
        try:
            self.query_one("#perm-inline", InlinePermissionWidget).remove()
        except Exception:
            pass
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

    # ── Spinner ──────────────────────────────────────

    def _start_spinner(self) -> None:
        if self._spinner_timer is not None:
            return
        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)

    def _stop_spinner(self) -> None:
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None

    def _tick_spinner(self) -> None:
        self._spinner_idx += 1
        frame = SPINNER_FRAMES[self._spinner_idx % len(SPINNER_FRAMES)]
        elapsed = _time.monotonic() - self._thinking_start
        if self._spinner_label is not None:
            self._spinner_label.update(
                f"[dim]{frame}[/dim] [dim]{self._thinking_verb}... ({elapsed:.0f}s)[/dim]"
            )

    def _finish_streaming(self) -> None:
        self._streaming = False
        self._stop_spinner()
        self._agent_task = None
        # Clear status-line (spinner area)
        try:
            self.query_one("#status-line", Static).update("")
        except Exception:
            pass
        self._spinner_label = None

    # ── 系统消息 / 错误 ──────────────────────────────

    def _show_system_message(self, text: str) -> None:
        try:
            chat = self.query_one("#chat-area", VerticalScroll)
            msg = Static(f"[dim]{text}[/dim]", classes="message system-message")
            chat.mount(msg)
            self.call_after_refresh(chat.scroll_end, animate=False)
        except Exception:
            pass

    def _update_mcp_status(self) -> None:
        """更新 MCP 连接状态显示。"""
        try:
            status_widget = self.query_one("#mcp-status", Static)
            servers = self._mcp_server_configs
            if isinstance(servers, dict) and servers:
                parts = []
                for name, tool_count in servers.items():
                    parts.append(f"[dim]{name}[/dim] [green]({tool_count} tools)[/green]")
                status = "[bold]MCP:[/bold] " + " | ".join(parts)
                status_widget.update(status)
            else:
                status_widget.update("")
        except Exception:
            pass

    def _show_error(self, text: str) -> None:
        logger = logging.getLogger(__name__)
        logger.error(f"TUI error: {text}")
        try:
            chat = self.query_one("#chat-area", VerticalScroll)
            error_widget = Static(f"[bold red]✗[/bold red] [red]{text}[/red]", classes="message error-message")
            chat.mount(error_widget)
            self.call_after_refresh(chat.scroll_end, animate=False)
        except Exception as e:
            logger.debug(f"Failed to show error widget: {e}")

    def _update_mode_label(self) -> None:
        if self.agent:
            try:
                pass
            except Exception:
                pass
        try:
            label = self.query_one("#mode-label", Static)
            if self.agent:
                perm = self.agent._engine.start_mode if self.agent._engine else Mode.DEFAULT
                display = _MODE_DISPLAY.get(perm, perm.value)
                color = _MODE_COLORS.get(perm, "dim")
                label.update(f"[{color}]{display}[/{color}]  (shift+tab to cycle)")
            else:
                label.update("[dim]default[/dim]  (shift+tab to cycle)")
        except Exception:
            pass
        try:
            model_label = self.query_one("#model-label", Static)
            model_text = self._selected_provider.model if self._selected_provider else ""
            if self._mcp_connecting:
                model_label.update(f"[yellow]MCP connecting...[/yellow]  {model_text}")
            else:
                model_label.update(model_text)
        except Exception:
            pass

    # ── 模式切换 ─────────────────────────────────────

    def action_cycle_mode(self) -> None:
        if self.agent is None or self.agent._engine is None:
            return
        current = self.agent._engine.start_mode
        try:
            idx = _MODE_CYCLE.index(current)
        except ValueError:
            idx = 0
        next_mode = _MODE_CYCLE[(idx + 1) % len(_MODE_CYCLE)]
        self.agent._engine.start_mode = next_mode
        self._update_mode_label()

    def action_toggle_tool_blocks(self) -> None:
        for block in self.query(ToolCallBlock):
            if block._loading:
                continue
            block._collapsed = not block._collapsed
            if block._collapsed:
                block._render_collapsed()
            else:
                block._render_expanded()

        for summary in self.query(ToolGroupSummary):
            summary.toggle()
            parent = summary.parent
            if parent:
                for child in parent.children:
                    if isinstance(child, ToolCallBlock) and child.tool_name in COLLAPSIBLE_TOOLS:
                        child.display = summary._expanded

        for block in self.query(SubAgentBlock):
            if block._done:
                block._collapsed = not block._collapsed
                block._render_done()

    def action_cancel(self) -> None:
        popup = self.query_one(CompletionPopup)
        if popup.is_visible:
            popup.hide()
            self.query_one("#chat-input", ChatInput).focus()
            return
        if self._agent_task and not self._agent_task.done():
            self._agent_task.cancel()

    async def action_handle_ctrl_c(self) -> None:
        if self._streaming:
            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
            self._show_system_message("(response interrupted)")
            self._finish_streaming()
            try:
                inp = self.query_one("#chat-input", ChatInput)
                inp.disabled = False
                inp.focus()
            except Exception:
                pass
            return

        if getattr(self, "_exit_requested", False):
            # Dispatch SessionEnd
            if self.hook_engine:
                try:
                    await self.hook_engine.dispatch(
                        HookEvent.SESSION_END,
                        {"event": "SessionEnd"},
                    )
                except Exception:
                    pass
            if self._writer:
                try:
                    self._writer.close()
                except Exception:
                    pass
            self.exit()
            return
        self._exit_requested = True
        self._show_system_message("Press Ctrl+C again to exit")

    # ── 通知轮询 ─────────────────────────────────────

    async def _start_notification_polling(self) -> None:
        while True:
            await asyncio.sleep(2)
            if not self._streaming and self.agent is not None:
                await self._process_task_notifications()

    async def _process_task_notifications(self) -> None:
        if self._task_mgr is None:
            return
        try:
            completed = self._task_mgr.poll_completed()
        except Exception:
            return
        if not completed or self.agent is None:
            return

        for task in completed:
            status_icon = "v" if task.status == "completed" else "x"
            self._show_system_message(
                f"{status_icon} background task done: [{task.id}] {getattr(task, 'name', '')}"
            )

            from suisuicode.tui.tasks import build_task_notification
            note_text = build_task_notification(task)
            if note_text:
                self._conv.add_system_message(note_text)

        self._agent_task = asyncio.create_task(
            self._send_message("", is_notification=True)
        )

    # ── MCP ──────────────────────────────────────────

    async def _init_mcp(self) -> None:
        self._mcp_connecting = True
        self._update_mode_label()
        try:
            from suisuicode.mcp.manager import MCPManager

            manager = MCPManager()
            manager.load_configs(self._mcp_server_configs)
            await manager.register_all_tools(self._tool_registry)
            self._mcp_manager = manager
        except Exception as e:
            self._show_system_message(f"MCP init error: {e}")
        finally:
            self._mcp_connecting = False
            self._update_mode_label()

    # ── Plan Approval ────────────────────────────────

    async def _show_plan_approval(self) -> None:
        from suisuicode.tui.plan_dialog import InlinePlanWidget

        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlinePlanWidget()
        await chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_plan_widget_responded(self, event) -> None:
        from suisuicode.tui.plan_dialog import InlinePlanWidget, PlanChoice

        try:
            self.query_one("#plan-inline", InlinePlanWidget).remove()
        except Exception:
            pass
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

        if self.agent is None:
            return

        choice = event.choice
        if choice == PlanChoice.YOLO:
            self.agent._engine.start_mode = Mode.BYPASS
            self._update_mode_label()
            self._show_system_message("Plan approved — entering YOLO mode.")
        elif choice == PlanChoice.MANUAL:
            self.agent._engine.start_mode = Mode.ACCEPT_EDITS
            self._update_mode_label()
            self._show_system_message("Plan approved — manually approve edits.")
        elif choice == PlanChoice.FEEDBACK:
            if event.feedback:
                self._conv.add_user(event.feedback)
                self._agent_task = asyncio.create_task(
                    self._send_message(event.feedback)
                )

    # ── Session Resume ───────────────────────────────

    def _open_resume_menu(self) -> None:
        from suisuicode.session.list import list_sessions

        sessions = list_sessions(Path(os.getcwd()) / ".suisuicode" / "sessions")
        if not sessions:
            self._show_system_message("No past sessions found.")
            return

        chat = self.query_one("#chat-area", VerticalScroll)
        widget = InlineResumeWidget(sessions)
        self._pending_resume_widget = widget
        chat.mount(widget)
        self.call_after_refresh(chat.scroll_end, animate=False)
        try:
            self.query_one("#chat-input").disabled = True
        except Exception:
            pass

    def on_inline_resume_widget_selected(
        self, event: InlineResumeWidget.Selected
    ) -> None:
        try:
            widget = self.query_one("#resume-inline", InlineResumeWidget)
            widget.remove()
        except Exception:
            pass
        try:
            self.query_one("#chat-input").disabled = False
            self.query_one("#chat-input").focus()
        except Exception:
            pass

        if event.session_id is None:
            return

        from suisuicode.tui.resume import do_resume_session
        asyncio.create_task(do_resume_session(self, event.session_id))

    # ── Clear ────────────────────────────────────────

    async def _do_clear_and_new_session(self) -> None:
        from suisuicode.compact import new_session_context

        # Dispatch SessionEnd
        if self.hook_engine:
            try:
                await self.hook_engine.dispatch(
                    HookEvent.SESSION_END,
                    {"event": "SessionEnd"},
                )
            except Exception:
                pass

        # Close old writer
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass

        # Create new session
        ses_ctx = new_session_context(os.getcwd())
        if self._runtime:
            self._runtime.reset_for_new_session(ses_ctx)

        from suisuicode.session.writer import Writer
        session_dir = Path(os.getcwd()) / ".suisuicode" / "sessions" / ses_ctx.id
        session_dir.mkdir(parents=True, exist_ok=True)
        self._writer = Writer(session_dir / "messages.jsonl")
        self._conv = Conversation()
        self._conv.on_append = lambda msg: self._writer.append(msg)
        self._conv.on_replace = lambda msgs: self._writer.replace_all(msgs)

        # Clear chat area
        try:
            chat = self.query_one("#chat-area", VerticalScroll)
            chat.remove_children()
        except Exception:
            pass

        # Reset agent
        if self.agent:
            self.agent.clear_active_skills()
            if self.agent.runtime:
                self.agent.runtime.reset_for_new_session(ses_ctx)

        # Dispatch SessionStart
        if self.hook_engine:
            asyncio.ensure_future(
                self.hook_engine.dispatch(
                    HookEvent.SESSION_START,
                    {"event": "SessionStart"},
                )
            )

        self._show_system_message("New session started.")

    # ── UI Protocol 方法 ─────────────────────────────

    def ui_println(self, msg: str) -> None:
        self._show_system_message(msg)

    def ui_error(self, msg: str) -> None:
        self._show_error(msg)

    def ui_mode(self) -> Mode:
        if self.agent and self.agent._engine:
            return self.agent._engine.start_mode
        return Mode.DEFAULT

    def ui_set_mode(self, m: Mode) -> None:
        if self.agent and self.agent._engine:
            self.agent._engine.start_mode = m
            self._update_mode_label()

    def ui_inject_and_send(self, display_label: str, preset_prompt: str) -> None:
        self._show_system_message(display_label)
        if not self._streaming and self.agent is not None:
            self._agent_task = asyncio.create_task(self._send_message(preset_prompt))

    def ui_usage_in(self) -> int:
        if self.agent and self.agent.runtime:
            return self.agent.runtime.usage_anchor
        return 0

    def ui_usage_out(self) -> int:
        return 0

    def ui_model_name(self) -> str:
        if self._selected_provider:
            return self._selected_provider.model
        return ""

    def ui_cwd(self) -> str:
        return self._active_cwd or os.getcwd()

    def ui_tool_count(self) -> int:
        if self._tool_registry:
            return self._tool_registry.count()
        return 0

    def ui_memory_files(self) -> list[str]:
        if self._mem_mgr:
            return self._mem_mgr.list_files()
        return []

    def ui_session_path(self) -> str:
        if self._writer:
            return str(self._writer.path)
        return ""

    def ui_session_id(self) -> str:
        if self._runtime and self._runtime.session:
            return self._runtime.session.id
        return ""

    def ui_quit(self) -> None:
        self._exit_requested = True
        self.exit()

    def ui_force_compact(self) -> None:
        if self.agent:
            defs = self._tool_registry.definitions() if self._tool_registry else []
            before, after = asyncio.ensure_future(
                self.agent.run_force_compact(self._conv, defs)
            )
            self._show_system_message("Compacting...")

    def ui_open_resume_menu(self) -> None:
        self._open_resume_menu()

    def ui_clear_and_new_session(self) -> None:
        asyncio.create_task(self._do_clear_and_new_session())

    def ui_idle(self) -> bool:
        return not self._streaming

    def append_assistant_message(self, text: str) -> None:
        if self._conv:
            self._conv.add_assistant(text)
        self._show_system_message(text)

    def list_catalog_skills(self) -> list:
        if self._catalog:
            return self._catalog.list()
        return []

    def list_active_skills(self) -> list[str]:
        if self.agent:
            return self.agent.active_skill_names
        return []

    def clear_active_skills(self) -> None:
        if self.agent:
            self.agent.clear_active_skills()

    def hook_sources(self) -> list[str]:
        if self.hook_engine:
            return getattr(self.hook_engine, "sources", [])
        return []

    def hook_rules(self) -> list:
        if self.hook_engine:
            return getattr(self.hook_engine, "rules", [])
        return []

    def worktree_accessor(self):
        if self._worktree_mgr:
            from suisuicode.tui.worktree_adapter import WorktreeAdapter
            return WorktreeAdapter(self._worktree_mgr, self)
        return None


# ── new_app 工厂 ─────────────────────────────────────


def new_app(
    providers: list[ProviderConfig],
    version: str,
    registry: Registry,
    engine: Engine,
    mcp_servers: dict | None = None,
    *,
    runtime: SessionRuntime | None = None,
    providers_config: list[ProviderConfig] | None = None,
    writer=None,
    mem_mgr=None,
    instruction_text: str = "",
    memory_text: str = "",
    hook_engine: HookEngine | None = None,
    cmd_registry=None,
    provider_instance=None,
    conv: Conversation | None = None,
    catalog=None,
    skill_executor=None,
    task_mgr=None,
    subagent_catalog=None,
    worktree_mgr=None,
    team_mgr=None,
    active_cwd: str = "",
    coordinator_mode: bool = False,
    **kwargs,
) -> SuisuiCodeApp:
    """构造 SuisuiCodeApp，注入所有预构建子系统。"""
    app = SuisuiCodeApp(
        providers=providers,
        version=version,
        registry=registry,
        engine=engine,
        mcp_servers=mcp_servers,
        runtime=runtime,
        providers_config=providers_config,
        writer=writer,
        mem_mgr=mem_mgr,
        instruction_text=instruction_text,
        memory_text=memory_text,
        hook_engine=hook_engine,
        cmd_registry=cmd_registry,
        provider_instance=provider_instance,
        conv=conv,
        catalog=catalog,
        skill_executor=skill_executor,
        task_mgr=task_mgr,
        subagent_catalog=subagent_catalog,
        worktree_mgr=worktree_mgr,
        team_mgr=team_mgr,
        active_cwd=active_cwd,
        coordinator_mode=coordinator_mode,
    )
    # ch13: 注入 task_mgr / subagent_catalog
    app.task_mgr = task_mgr
    app.subagent_catalog = subagent_catalog
    # ch14: 注入 worktree_mgr
    app.worktree_mgr = worktree_mgr
    # ch15: 注入 team_mgr / coordinator_mode
    app.team_mgr = team_mgr
    app.coordinator_mode = coordinator_mode
    # ch15: 注册 /team 命令
    if team_mgr is not None:
        from suisuicode.command.builtin_team import _build_team_handler
        from suisuicode.command.command import Command, Kind
        if app._cmd_registry is not None:
            app._cmd_registry.register(Command(
                name="team",
                description="团队管理：list / info <name> / delete <name> / kill <member>",
                kind=Kind.LOCAL,
                handler=_build_team_handler(team_mgr),
            ))
    return app
