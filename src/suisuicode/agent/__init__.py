# agent 包：ReAct 多轮循环 + ch08 上下文管理 + ch13 SubAgent

from suisuicode.agent.agent import (
    MAX_ITERATIONS,
    MAX_UNKNOWN_RUN,
    NOTICE_CANCELLED,
    NOTICE_MAX_ITER,
    NOTICE_UNKNOWN_TOOLS,
    Agent,
    ApprovalRequest,
    Event,
    Phase,
    ToolEvent,
    Usage,
    _all_unknown,
)
from suisuicode.agent.event import CompactEvent, CompactPhase
from suisuicode.agent.runtime import SessionRuntime
from suisuicode.agent.run_to_completion import MaxTurnsReached  # ch13: 确保 monkey-patch 生效

# ch13: 触发 monkey-patch（将 run_to_completion 挂到 Agent 上）
import suisuicode.agent.run_to_completion as _rtc  # noqa: F401

__all__ = [
    "Agent",
    "ApprovalRequest",
    "CompactEvent",
    "CompactPhase",
    "Event",
    "MAX_ITERATIONS",
    "MAX_UNKNOWN_RUN",
    "MaxTurnsReached",
    "NOTICE_CANCELLED",
    "NOTICE_MAX_ITER",
    "NOTICE_UNKNOWN_TOOLS",
    "Phase",
    "SessionRuntime",
    "ToolEvent",
    "Usage",
    "_all_unknown",
]
