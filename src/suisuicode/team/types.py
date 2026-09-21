"""Agent Team 核心数据类型。

Team / TeammateInfo / BackendType 及异常类。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class BackendType(StrEnum):
    """队员执行后端类型。"""

    TMUX = "tmux"
    ITERM2 = "iterm2"
    IN_PROCESS = "in-process"


# ── 异常 ──────────────────────────────────────────────────


class TeamError(Exception):
    """Team 相关异常的基类。"""


class TeamNotFoundError(TeamError):
    """指定 Team 不存在。"""


class TeamAlreadyExistsError(TeamError):
    """同名 Team 已存在（罕见，sanitize 后缀兜底后仍冲突）。"""


class TeamHasActiveMembersError(TeamError):
    """Team 仍有活跃成员，拒绝删除。"""


class MemberExistsError(TeamError):
    """成员名在 Team 内已存在。"""


class MemberNotFoundError(TeamError):
    """指定成员在 Team 内未找到。"""


class InProcessTeammateNoSpawnError(TeamError):
    """in-process 后端的队员不允许再 spawn 队员。"""


# ── TeammateInfo ─────────────────────────────────────────


@dataclass
class TeammateInfo:
    """队员信息，对应 Team config.json 中 members 数组的每个元素。

    is_active: None 或 True 表活跃，False 表空闲/已终止。
    """

    name: str  # Lead 分配的队员名，Team 内唯一
    agent_id: str  # 对应 task.BackgroundTask.id 或子进程 agent_id
    agent_type: str = ""  # subagent 定义名；Fork 路径为空
    model: str = ""  # 模型覆盖；空 = inherit
    worktree_path: str = ""  # 工作目录绝对路径
    branch: str = ""  # worktree 分支名
    backend_type: BackendType = BackendType.IN_PROCESS
    pane_id: str = ""  # tmux pane / iterm2 split id；in-process 为空
    is_active: bool | None = None  # None/True 活跃，False 空闲
    plan_mode_required: bool = False
    session_dir: str = ""  # 队员独立 session 目录绝对路径

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "agent_id": self.agent_id,
            "agent_type": self.agent_type,
            "model": self.model,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "backend_type": str(self.backend_type),
            "pane_id": self.pane_id,
            "is_active": self.is_active,
            "plan_mode_required": self.plan_mode_required,
            "session_dir": self.session_dir,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TeammateInfo:
        return cls(
            name=str(d.get("name", "")),
            agent_id=str(d.get("agent_id", "")),
            agent_type=str(d.get("agent_type", "")),
            model=str(d.get("model", "")),
            worktree_path=str(d.get("worktree_path", "")),
            branch=str(d.get("branch", "")),
            backend_type=BackendType(d.get("backend_type", "in-process")),
            pane_id=str(d.get("pane_id", "")),
            is_active=d.get("is_active"),
            plan_mode_required=bool(d.get("plan_mode_required", False)),
            session_dir=str(d.get("session_dir", "")),
        )


# ── Team ──────────────────────────────────────────────────


@dataclass
class Team:
    """一个长期存在的小组对象。

    sanitized_name 是 Team 主键，用于文件路径。
    """

    name: str  # 用户给的原始名
    sanitized_name: str  # 经 sanitize 后用于路径
    lead_agent_id: str = "lead"  # 固定 "lead"（本期 Lead = 主 Agent）
    backend: BackendType = BackendType.IN_PROCESS
    description: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    members: list[TeammateInfo] = field(default_factory=list)

    # 派生路径（不持久化到 config.json）
    config_dir: str = ""
    config_path: str = ""  # <config_dir>/config.json
    tasks_path: str = ""  # <config_dir>/tasks.json
    mailbox_dir: str = ""  # <config_dir>/mailbox/

    _lock: asyncio.Lock = field(
        default_factory=asyncio.Lock, repr=False, compare=False
    )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "sanitized_name": self.sanitized_name,
            "lead_agent_id": self.lead_agent_id,
            "backend": str(self.backend),
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "members": [m.to_dict() for m in self.members],
        }

    @classmethod
    def from_dict(cls, d: dict) -> Team:
        created_at = datetime.now()
        raw_ts = d.get("created_at")
        if raw_ts:
            try:
                created_at = datetime.fromisoformat(str(raw_ts))
            except (ValueError, TypeError):
                pass

        members = [
            TeammateInfo.from_dict(m) for m in d.get("members", [])
        ]

        return cls(
            name=str(d.get("name", "")),
            sanitized_name=str(d.get("sanitized_name", "")),
            lead_agent_id=str(d.get("lead_agent_id", "lead")),
            backend=BackendType(d.get("backend", "in-process")),
            description=str(d.get("description", "")),
            created_at=created_at,
            members=members,
        )

    def member_by_name(self, name: str) -> TeammateInfo | None:
        """按队员名查找。"""
        for m in self.members:
            if m.name == name:
                return m
        return None

    def member_by_agent_id(self, agent_id: str) -> TeammateInfo | None:
        """按 agent_id 查找。"""
        for m in self.members:
            if m.agent_id == agent_id:
                return m
        return None

    def active_members(self) -> list[TeammateInfo]:
        """返回所有活跃成员（is_active != False）。"""
        return [m for m in self.members if m.is_active is not False]

    def has_active_members(self) -> bool:
        """是否有活跃成员（is_active != False，含 None/True）。"""
        return any(m.is_active is not False for m in self.members)
