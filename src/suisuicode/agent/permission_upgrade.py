"""子 Agent 把审批请求升级到父 TUI 的回调类型。

返回 (outcome, ok) —— ok=False 时调用方应走默认 emit Approval 路径。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from suisuicode.permission import Outcome

# 前向引用：ApprovalRequest 由 agent.agent 定义
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from suisuicode.agent.agent import ApprovalRequest

ApprovalUpgrader = Callable[
    ["ApprovalRequest"],
    Awaitable[tuple[Outcome, bool]],
]
