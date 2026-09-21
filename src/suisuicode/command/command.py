"""Kind 枚举、Command 与 Handler 类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from suisuicode.command.ui import UI

Handler = Callable[["UI", str], Awaitable[None]]


class Kind(Enum):
    """命令执行类型。"""

    LOCAL = "local"  # 纯本地：只打印，不改 App，不进 history
    UI = "ui"  # 影响界面：改 App 状态，不进 history
    PROMPT = "prompt"  # 提示词：注入 user 消息 + 触发回合，进 history


@dataclass(slots=True)
class Command:
    """一条 slash 命令的完整元数据与处理函数。"""

    name: str  # 不带 "/" 前缀，全小写，唯一
    description: str  # 一句话，用于 /help 与补全菜单
    kind: Kind
    handler: Handler
    aliases: list[str] = field(default_factory=list)  # 不带 "/" 前缀，全小写
    hidden: bool = False  # /help 与补全菜单都不显示
