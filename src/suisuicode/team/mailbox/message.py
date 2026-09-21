"""邮箱消息类型定义。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class MessageType(StrEnum):
    """消息类型枚举。"""

    TEXT = "text"
    SHUTDOWN_REQUEST = "shutdown_request"
    SHUTDOWN_RESPONSE = "shutdown_response"
    PLAN_APPROVAL_RESPONSE = "plan_approval_response"


@dataclass
class Message:
    """一条邮箱消息。"""

    from_: str  # json key "from"
    to: str
    type: MessageType = MessageType.TEXT
    summary: str = ""
    content: str = ""
    payload: dict[str, Any] | None = None
    timestamp: int = 0
    read: bool = False

    def to_dict(self) -> dict:
        return {
            "from": self.from_,
            "to": self.to,
            "type": str(self.type),
            "summary": self.summary,
            "content": self.content,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "read": self.read,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Message:
        msg_type = MessageType.TEXT
        raw_type = d.get("type", "text")
        try:
            msg_type = MessageType(raw_type)
        except ValueError:
            pass
        return cls(
            from_=str(d.get("from", "")),
            to=str(d.get("to", "")),
            type=msg_type,
            summary=str(d.get("summary", "")),
            content=str(d.get("content", "")),
            payload=d.get("payload"),
            timestamp=int(d.get("timestamp", 0)),
            read=bool(d.get("read", False)),
        )
