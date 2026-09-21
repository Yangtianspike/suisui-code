"""Hook 生命周期事件枚举 + 拦截判定。"""

from __future__ import annotations

import enum


class Event(str, enum.Enum):
    """11 个生命周期事件，取值即 YAML 字面量。

    支持 PascalCase 和 snake_case 两种写法：:

        >>> Event("SessionStart")
        <Event.SESSION_START: 'SessionStart'>
        >>> parse_event("pre_tool_use")
        <Event.PRE_TOOL_USE: 'PreToolUse'>
    """

    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    SESSION_RESUME = "SessionResume"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    STOP = "Stop"
    PRE_USER_MESSAGE = "PreUserMessage"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    PRE_COMPACT = "PreCompact"
    POST_COMPACT = "PostCompact"
    NOTIFICATION = "Notification"


# snake_case → PascalCase 映射
_SNAKE_TO_PASCAL: dict[str, str] = {
    "session_start": "SessionStart",
    "session_end": "SessionEnd",
    "session_resume": "SessionResume",
    "user_prompt_submit": "UserPromptSubmit",
    "stop": "Stop",
    "pre_user_message": "PreUserMessage",
    "pre_tool_use": "PreToolUse",
    "post_tool_use": "PostToolUse",
    "pre_compact": "PreCompact",
    "post_compact": "PostCompact",
    "notification": "Notification",
}


# 可拦截事件的集合（阻断主流程 + 给用户反馈）
BLOCKING_EVENTS: frozenset[Event] = frozenset(
    {Event.PRE_TOOL_USE, Event.USER_PROMPT_SUBMIT}
)


def is_blocking(e: Event) -> bool:
    """事件是否支持拦截语义。

    >>> is_blocking(Event.PRE_TOOL_USE)
    True
    >>> is_blocking(Event.STOP)
    False
    """
    return e in BLOCKING_EVENTS


def parse_event(s: str) -> Event | None:
    """安全解析事件名字符串（PascalCase 或 snake_case）；未知返回 None。

    >>> parse_event("SessionStart")
    <Event.SESSION_START: 'SessionStart'>
    >>> parse_event("pre_tool_use")
    <Event.PRE_TOOL_USE: 'PreToolUse'>
    >>> parse_event("UnknownEvent") is None
    True
    """
    # 先试 PascalCase 直接匹配
    try:
        return Event(s)
    except ValueError:
        pass
    # 再试 snake_case 映射
    pascal = _SNAKE_TO_PASCAL.get(s.lower())
    if pascal is not None:
        return Event(pascal)
    return None
