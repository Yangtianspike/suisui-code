from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Static

from suisuicode.permission import Outcome

_PERM_OPTIONS = [
    ("Yes", Outcome.ALLOW_ONCE),
    ("Yes, and don't ask again for this command", Outcome.ALLOW_FOREVER),
    ("No", Outcome.DENY_ONCE),
]


class InlinePermissionWidget(Vertical, can_focus=True):
    """渲染在聊天区域内部的内联权限确认提示。"""

    BINDINGS = [
        Binding("up", "cursor_up", "Up", priority=True),
        Binding("down", "cursor_down", "Down", priority=True),
        Binding("enter", "select", "Select", priority=True),
        Binding("escape", "deny", "Deny", priority=True),
    ]

    class Responded(Message):
        def __init__(self, outcome: Outcome) -> None:
            super().__init__()
            self.outcome = outcome

    def __init__(self, tool_name: str, description: str, **kwargs) -> None:
        super().__init__(id="perm-inline", **kwargs)
        self._tool_name = tool_name
        self._description = description
        self._cursor = 0

    def compose(self) -> ComposeResult:
        yield Static(self._build_content(), id="perm-content")

    def on_mount(self) -> None:
        self.focus()

    def _build_content(self) -> str:
        lines = []
        lines.append(f"\n  [bold yellow]{self._tool_name} command[/bold yellow]\n")
        lines.append(f"    {self._description}\n")
        lines.append("  [dim]This command requires approval[/dim]\n")
        lines.append("  Do you want to proceed?\n")

        for i, (label, _outcome) in enumerate(_PERM_OPTIONS):
            if i == self._cursor:
                lines.append(f" [bold cyan]>[/bold cyan] {i + 1}. [bold]{label}[/bold]")
            else:
                lines.append(f"   {i + 1}. [dim]{label}[/dim]")

        return "\n".join(lines)

    def _refresh(self) -> None:
        content = self.query_one("#perm-content", Static)
        content.update(self._build_content())

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._refresh()

    def action_cursor_down(self) -> None:
        if self._cursor < len(_PERM_OPTIONS) - 1:
            self._cursor += 1
            self._refresh()

    def action_select(self) -> None:
        _, outcome = _PERM_OPTIONS[self._cursor]
        self.post_message(self.Responded(outcome))

    def action_deny(self) -> None:
        self.post_message(self.Responded(Outcome.DENY_ONCE))
