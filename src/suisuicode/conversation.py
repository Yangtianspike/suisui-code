from __future__ import annotations

import copy
import threading
from collections.abc import Callable

from suisuicode.llm import (
    Message,
    ToolCall,
    ToolResult,
    ROLE_USER,
    ROLE_ASSISTANT,
    ROLE_TOOL,
)


class Conversation:
    def __init__(
        self,
        on_append: Callable[[Message], None] | None = None,
        on_replace: Callable[[list[Message]], None] | None = None,
    ) -> None:
        self._messages: list[Message] = []
        self._lock = threading.RLock()
        self._on_append = on_append
        self._on_replace = on_replace

    @classmethod
    def from_messages(
        cls,
        msgs: list[Message],
        on_append: Callable[[Message], None] | None = None,
        on_replace: Callable[[list[Message]], None] | None = None,
    ) -> "Conversation":
        """从已有消息列表创建会话（恢复场景），可选回调。"""
        conv = cls(on_append=on_append, on_replace=on_replace)
        conv._messages = list(msgs)
        return conv

    def add_system_message(self, text: str) -> None:
        """添加一条 system 消息（fork skill 结果回流用）。"""
        msg = Message(role=ROLE_USER, content=text)
        with self._lock:
            self._messages.append(msg)
        if self._on_append is not None:
            self._on_append(msg)

    def add_user(self, text: str) -> None:
        msg = Message(role=ROLE_USER, content=text)
        with self._lock:
            self._messages.append(msg)
        if self._on_append is not None:
            self._on_append(msg)

    def add_assistant(self, text: str) -> None:
        msg = Message(role=ROLE_ASSISTANT, content=text)
        with self._lock:
            self._messages.append(msg)
        if self._on_append is not None:
            self._on_append(msg)

    def add_assistant_with_tool_calls(self, text: str, calls: list[ToolCall]) -> None:
        msg = Message(role=ROLE_ASSISTANT, content=text, tool_calls=list(calls))
        with self._lock:
            self._messages.append(msg)
        if self._on_append is not None:
            self._on_append(msg)

    def add_tool_results(self, results: list[ToolResult]) -> None:
        msg = Message(role=ROLE_TOOL, tool_results=list(results))
        with self._lock:
            self._messages.append(msg)
        if self._on_append is not None:
            self._on_append(msg)

    def messages(self) -> list[Message]:
        with self._lock:
            return list(self._messages)

    def length(self) -> int:
        with self._lock:
            return len(self._messages)

    def last_role(self) -> str:
        with self._lock:
            return self._messages[-1].role if self._messages else ""

    def replace_history(self, msgs: list[Message]) -> None:
        """把内存列表整体替换为传入的 msgs。

        compact 摘要后用这个方法一次性丢弃旧历史并装入"摘要 + 恢复 + 近期原文"。
        深拷贝防止外部继续持有旧列表引用。
        """
        with self._lock:
            self._messages = copy.deepcopy(msgs) if msgs else []
        if self._on_replace is not None:
            self._on_replace(list(self._messages))
