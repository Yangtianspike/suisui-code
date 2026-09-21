"""工具过滤多层防线（spec F26-F31）。

任何子 Agent 启动时应用：全局禁止 → 后台白名单 → 定义层黑名单 → 定义层白名单。
ch15: 新增 teammate 模式过滤——非队员看不到协作工具。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── 全局常量 ──────────────────────────────────────────────

# 任何子 Agent 永远不能用的工具名列表（spec F26）
ALL_AGENT_DISALLOWED_TOOLS: list[str] = ["Agent"]

# 自定义 Agent 比内置 Agent 多禁用的工具（spec F27，本期为空）
CUSTOM_AGENT_DISALLOWED_TOOLS: list[str] = []

# 后台 Agent 工具白名单（spec F28）
ASYNC_AGENT_ALLOWED_TOOLS: list[str] = [
    "read_file",
    "write_file",
    "edit_file",
    "glob",
    "grep",
    "bash",
    "load_skill",
    "install_skill",
]

# ch15: 队员专属的 5 个协作工具（队员可见，主 Agent / 普通 SubAgent 不可见）
TEAMMATE_EXTRA_TOOLS: list[str] = [
    "TaskCreate",
    "TeamTaskGet",
    "TeamTaskList",
    "TaskUpdate",
    "TeamSendMessage",
]

# ch15: 队员 spawn 时从 ALL_AGENT_DISALLOWED_TOOLS 临时排除的项
# （队员允许调 Agent，但 in-process 队员通过运行时拦截阻止）
TEAMMATE_AGENT_ALLOWED: bool = True


# ── FilterParams ───────────────────────────────────────────


@dataclass
class FilterParams:
    """工具过滤输入参数（spec F30）。"""

    all: list[str]  # registry 的全部工具名
    source: int = 0  # subagent.Source 的整数值
    background: bool = False
    allowed: list[str] = field(default_factory=list)  # Agent 定义的 tools 白名单
    disallowed: list[str] = field(default_factory=list)  # Agent 定义的 disallowedTools 黑名单
    teammate: bool = False  # ch15: 是否在 Team 队员上下文中


# ── 过滤函数 ──────────────────────────────────────────────


def apply_agent_tool_filter(p: FilterParams) -> list[str]:
    """按 spec F30 顺序应用多层过滤，返回最终 allowed 列表。

    1. 起点 = p.all 副本
    2. 去除 ALL_AGENT_DISALLOWED_TOOLS（teammate 时跳过 Agent 禁止）
    3. 若 p.source >= 2（非 builtin），再去除 CUSTOM_AGENT_DISALLOWED_TOOLS（本期为空）
    4. 若 p.background，与 ASYNC_AGENT_ALLOWED_TOOLS + MCP/Skill 工具取交集
    5. 去除 p.disallowed
    6. 若 p.allowed 非空，与之取交集
    7. ch15: 非 teammate 时排除协作工具（TaskCreate 等）；teammate 时保留
    """
    result = list(p.all)

    # 过滤 1-2：全局禁止
    if p.teammate:
        # 队员允许使用 Agent 工具（除非被其他层过滤）
        # 但仍然禁止嵌套 spawn（由运行时检查兜底）
        banned_copy = [b for b in ALL_AGENT_DISALLOWED_TOOLS if b != "Agent"]
        for banned in banned_copy:
            if banned in result:
                result.remove(banned)
    else:
        for banned in ALL_AGENT_DISALLOWED_TOOLS:
            if banned in result:
                result.remove(banned)
    if p.source >= 2:  # user / project / plugin
        for banned in CUSTOM_AGENT_DISALLOWED_TOOLS:
            if banned in result:
                result.remove(banned)

    # 过滤 3：后台白名单
    if p.background:
        allowed_set = set(ASYNC_AGENT_ALLOWED_TOOLS)
        result = [t for t in result if t in allowed_set or is_mcp_or_skill(t)]

    # 过滤 4：定义层黑名单
    for banned in p.disallowed:
        if banned in result:
            result.remove(banned)

    # 过滤 5：定义层白名单
    if p.allowed:
        allowed_set = set(p.allowed)
        result = [t for t in result if t in allowed_set]

    # ch15: 协作工具可见性
    if not p.teammate:
        # 非队员：看不到协作工具
        for ct in TEAMMATE_EXTRA_TOOLS:
            if ct in result:
                result.remove(ct)

    return result


def is_mcp_or_skill(name: str) -> bool:
    """判断工具名是否来自 MCP 或 Skill（按命名约定）。

    MCP 工具以 ``mcp__`` 开头；Skill 工具以注册时的名字为准。
    本期对 Skill 工具的识别暂按名字前缀兜底。
    """
    return name.startswith("mcp__")
