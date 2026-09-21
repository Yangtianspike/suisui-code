"""Hook 生命周期挂钩系统。

提供 Agent 生命周期事件分派、YAML 配置加载、四类动作执行。
"""

from __future__ import annotations

from suisuicode.hook.engine import DispatchResult, Engine
from suisuicode.hook.event import BLOCKING_EVENTS, Event, is_blocking, parse_event
from suisuicode.hook.loader import load
from suisuicode.hook.rule import (
    Action,
    ActionType,
    AtomCondition,
    CombineMode,
    Condition,
    HttpAction,
    Payload,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)

__all__ = [
    "Action",
    "ActionType",
    "AtomCondition",
    "BLOCKING_EVENTS",
    "CombineMode",
    "Condition",
    "DispatchResult",
    "Engine",
    "Event",
    "HttpAction",
    "Payload",
    "PromptAction",
    "Rule",
    "ShellAction",
    "SubagentAction",
    "is_blocking",
    "load",
    "parse_event",
]
