"""Agent Team 顶层包 — 把 SubAgent 扩展为 Team 队员。

提供完整的 Team 生命周期管理、三种执行后端、邮箱通信、
共享任务列表、协作工具和 Coordinator Mode。
"""

from suisuicode.team.manager import Manager, LeadMessage
from suisuicode.team.types import (
    BackendType,
    InProcessTeammateNoSpawnError,
    MemberExistsError,
    MemberNotFoundError,
    Team,
    TeamError,
    TeamHasActiveMembersError,
    TeamNotFoundError,
    TeammateInfo,
)
from suisuicode.team.registry import AgentNameRegistry
from suisuicode.team.persistence import sanitize

__all__ = [
    "AgentNameRegistry",
    "BackendType",
    "InProcessTeammateNoSpawnError",
    "LeadMessage",
    "Manager",
    "MemberExistsError",
    "MemberNotFoundError",
    "sanitize",
    "Team",
    "TeamError",
    "TeamHasActiveMembersError",
    "TeamNotFoundError",
    "TeammateInfo",
]
