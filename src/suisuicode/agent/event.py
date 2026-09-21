"""Agent 事件流类型：CompactPhase、CompactEvent、以及扩展后的 Event。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class CompactPhase(Enum):
    BEFORE_AUTO = "before_auto"
    AFTER_AUTO = "after_auto"
    BEFORE_EMERGENCY = "before_emergency"
    AFTER_EMERGENCY = "after_emergency"


@dataclass
class CompactEvent:
    phase: CompactPhase
    before: int = 0  # after 状态有意义；before 状态置 0
    after: int = 0
    err: Exception | None = None
