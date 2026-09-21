"""队员工具白名单 — 队员 spawn 时注入的 5 个协作工具名。"""

from __future__ import annotations

TEAMMATE_TOOL_NAMES: list[str] = [
    "TaskCreate",
    "TeamTaskGet",
    "TeamTaskList",
    "TaskUpdate",
    "TeamSendMessage",
]


def build_teammate_allowed_tools(base_allowed: list[str]) -> list[str]:
    """在 base_allowed 基础上追回 5 个协作工具（如果被通用过滤移除）。

    返回新的 list，不修改原列表。
    """
    result = list(base_allowed)
    for t in TEAMMATE_TOOL_NAMES:
        if t not in result:
            result.append(t)
    return result
