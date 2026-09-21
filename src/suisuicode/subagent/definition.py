"""Agent 角色定义数据结构（spec F4）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum

from suisuicode.permission import Mode as PermissionMode


class Source(IntEnum):
    """定义文件来源，数值越大优先级越高。"""

    BUILTIN = 0
    USER = 1
    PROJECT = 2
    PLUGIN = 3  # 占位，本期不实现

    def __str__(self) -> str:
        _map = {0: "builtin", 1: "user", 2: "project", 3: "plugin"}
        return _map.get(int(self), "unknown")


@dataclass
class Definition:
    """一个 Agent 角色的完整定义，从 Markdown + YAML frontmatter 解析。

    对应 spec F4 的全部字段。
    """

    name: str  # frontmatter.name — 角色名，用于 subagent_type 参数
    description: str  # frontmatter.description — 一句话描述

    # 工具约束
    tools: list[str] = field(default_factory=list)  # frontmatter.tools 白名单；空 = 不收窄
    disallowed_tools: list[str] = field(default_factory=list)  # frontmatter.disallowedTools 黑名单

    # 模型与行为
    model: str = "inherit"  # "haiku" / "sonnet" / "opus" / "inherit"
    max_turns: int = 0  # 0 = 沿用全局默认 (25)
    permission_mode: PermissionMode = PermissionMode.DEFAULT
    dont_ask: bool = False  # permissionMode=dontAsk 时置 True
    background: bool = False  # 强制后台

    # 正文
    system_prompt: str = ""  # Markdown body（去 frontmatter 后的全文）

    # ch14: 隔离模式
    isolation: str = ""  # "" 或 "worktree"

    # 元信息
    file_path: str = ""  # 定义文件绝对路径（调试用）
    source: Source = Source.BUILTIN

    def is_fork(self) -> bool:
        """是否为 Fork 路径的临时 Definition。"""
        return self.name == "__fork__"
