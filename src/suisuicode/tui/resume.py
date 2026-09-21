"""/resume 会话恢复：Screen 弹窗 + OptionList 选择。"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import OptionList, RichLog, Static
from textual.widgets.option_list import Option

from suisuicode import llm
from suisuicode.compact import (
    ManageInput,
    TriggerKind,
    manage_context,
    open_session_context,
)
from suisuicode.compact.token import estimate_tokens
from suisuicode.conversation import Conversation
from suisuicode.session import Writer, list_sessions, load_session
from suisuicode.tui.view import notice_block

if TYPE_CHECKING:
    from suisuicode.tui.app import SuisuiCodeApp

TIME_GAP_THRESHOLD = 6 * 3600


class ResumeScreen(Screen):
    """会话选择弹窗：↑↓ + Enter 确认，鼠标双击确认，Esc 取消。"""

    def __init__(self, sessions: list) -> None:
        super().__init__()
        self._sessions = {s.id: s for s in sessions}
        self._options = [_build_option(s) for s in sessions]
        self._last_click_id: str = ""
        self._last_click_time: float = 0.0

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Select a session to resume", id="resume-title"),
            OptionList(*self._options, id="resume-list"),
            Static("↑↓ navigate  ·  ⏎ select  ·  double-click to select  ·  esc cancel", id="resume-hint"),
            id="resume-container",
        )

    def on_mount(self) -> None:
        self.query_one("#resume-list", OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        now = time.monotonic()
        sid = event.option.id
        # 双击同一项（500ms 内）→ 确认选择
        if sid == self._last_click_id and now - self._last_click_time < 0.5:
            if sid and sid in self._sessions:
                self.dismiss(self._sessions[sid])
        self._last_click_id = sid
        self._last_click_time = now

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
        elif event.key == "enter":
            # Enter 键直接确认当前高亮项
            event.stop()
            selector = self.query_one("#resume-list", OptionList)
            if selector.highlighted is not None:
                option = selector.get_option_at_index(selector.highlighted)
                if option.id and option.id in self._sessions:
                    self.dismiss(self._sessions[option.id])


async def begin_resume(app: "SuisuiCodeApp") -> None:
    """推送 ResumeScreen 弹窗，等待用户选择或取消。"""
    sessions_dir = os.path.join(os.getcwd(), ".suisuicode", "sessions")
    sessions = list_sessions(sessions_dir)

    if not sessions:
        app.query_one("#log", RichLog).write(notice_block("没有可恢复的历史会话。"))
        return

    info = await app.push_screen(ResumeScreen(sessions), wait_for_dismiss=True)

    if info is None:
        app.query_one("#log", RichLog).write(notice_block("已取消"))
        return

    await do_resume_session(app, info)
    app.query_one("#input-area").focus()


async def do_resume_session(app: "SuisuiCodeApp", info) -> None:
    """执行恢复流程：加载 → 截断 → 压缩 → 时间跨度 → 重建上下文。"""
    log = app.query_one("#log", RichLog)

    # ch12: SessionEnd（离开旧会话）
    if app.hook_engine is not None:
        await app._dispatch_session_end()

    msgs = load_session(info.dir)

    est = estimate_tokens(0, msgs, 0)
    cw = app.runtime.context_window
    from suisuicode.compact.const import AUTO_SAFETY_MARGIN, SUMMARY_RESERVE

    if est >= cw - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN and app.agent is not None:
        log.write(notice_block("会话较大，正在压缩..."))
        temp_conv = Conversation.from_messages(msgs)
        in_ = ManageInput(
            conv=temp_conv,
            provider=app.agent._provider,
            model=app.agent._provider.model,
            context_window=cw,
            tool_defs=[],
            replacement=app.runtime.replacement,
            recovery=app.runtime.recovery,
            auto_tracking=app.runtime.auto_tracking,
            session=app.runtime.session,
            usage_anchor=0,
            anchor_msg_len=0,
            estimated_token=est,
            trigger=TriggerKind.MANUAL,
        )
        try:
            out = await manage_context(in_)
            msgs = temp_conv.messages()
            log.write(
                notice_block(f"Compacted: {est} → {out.after_tokens} estimated tokens")
            )
        except Exception:
            pass

    if info.modified_at.timestamp():
        gap = time.time() - info.modified_at.timestamp()
        if gap > TIME_GAP_THRESHOLD:
            hours = gap / 3600
            duration = f"{hours:.0f} 小时" if hours < 48 else f"{hours / 24:.0f} 天"
            msgs.append(
                llm.Message(
                    role="user",
                    content=(
                        f"[系统提示] 本会话已暂停 {duration}。"
                        "部分上下文可能已过时，如需最新信息请重新读取相关文件。"
                    ),
                )
            )

    on_append = (
        app.writer._on_append_callback
        if hasattr(app.writer, "_on_append_callback")
        else None
    )
    on_replace = (
        app.writer._on_replace_callback
        if hasattr(app.writer, "_on_replace_callback")
        else None
    )

    new_conv = Conversation.from_messages(
        msgs, on_append=on_append, on_replace=on_replace
    )
    new_ses_ctx = open_session_context(os.getcwd(), info.id)
    new_writer = Writer.open_existing(info.dir)

    app.conv = new_conv
    app.writer = new_writer
    app.runtime.session = new_ses_ctx

    from suisuicode.tui.view import (
        assistant_block,
        tool_line,
        tool_result_summary,
        user_block,
    )

    for msg in msgs:
        if msg.role == "user":
            log.write(user_block(msg.content or ""))
        elif msg.role == "assistant":
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    log.write(tool_line(tc.name, getattr(tc, "input", "") or ""))
            if msg.content:
                log.write(assistant_block(msg.content))
        elif msg.role == "tool":
            if msg.tool_results:
                for tr in msg.tool_results:
                    log.write(
                        tool_result_summary(
                            getattr(tr, "content", "") or "",
                            getattr(tr, "is_error", False),
                        )
                    )

    log.write(notice_block(f"已恢复会话 {info.id}，共 {len(msgs)} 条消息"))

    # ch12: SessionResume（进入恢复的会话）+ hook only_once 重置
    if app.hook_engine is not None:
        await app.hook_engine.reset_for_new_session()
        await app._dispatch_session_resume()


def _build_option(s) -> Option:
    relative = _relative_time(s.modified_at.timestamp())
    size_str = _format_size(s.size)
    parts = [s.title or "(untitled)", relative]
    if s.model:
        parts.append(s.model)
    parts.append(size_str)
    return Option(" · ".join(parts), id=s.id)


def _relative_time(ts: float) -> str:
    diff = time.time() - ts
    if diff < 60:
        return "just now"
    elif diff < 3600:
        return f"{int(diff / 60)} min ago"
    elif diff < 86400:
        return f"{int(diff / 3600)} hours ago"
    elif diff < 2592000:
        return f"{int(diff / 86400)} days ago"
    else:
        return f"{int(diff / 2592000)} months ago"


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    else:
        return f"{size / (1024 * 1024):.1f}MB"
