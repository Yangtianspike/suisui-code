"""Team Manager — 创建/查询/删除 Team + 成员操作。

单进程内 asyncio.Lock 保护 Team 状态；跨进程通过 reload_from_disk_locked 兜底。
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from suisuicode.team.types import (
    BackendType,
    MemberExistsError,
    MemberNotFoundError,
    Team,
    TeamHasActiveMembersError,
    TeamNotFoundError,
    TeammateInfo,
)
from suisuicode.team.persistence import (
    atomic_write_json,
    read_json,
    reload_from_disk_locked,
    sanitize,
)

if TYPE_CHECKING:
    from suisuicode.team.registry import AgentNameRegistry


class Manager:
    """在单 suisuicode 进程内管理多个 Team。

    典型场景同一时刻只有一个活跃 Team。
    """

    def __init__(
        self,
        home_dir: str | Path,
        project_root: str | Path,
        wt_mgr=None,  # worktree.Manager | None
        task_mgr=None,  # task.Manager | None
        registry: AgentNameRegistry | None = None,
    ) -> None:
        self.home_dir: str = str(Path(home_dir).resolve())
        self.project_root: str = str(Path(project_root).resolve())
        self.wt_mgr = wt_mgr
        self.task_mgr = task_mgr
        self.registry = registry
        self._lock = asyncio.Lock()
        self.teams: dict[str, Team] = {}  # 按 sanitized_name 索引

        # 确保 teams 根目录存在并扫描还原
        teams_root = self._teams_root()
        teams_root.mkdir(parents=True, exist_ok=True)
        self._scan_existing(teams_root)

    # ── 内部路径 ──────────────────────────────────────────

    def _teams_root(self) -> Path:
        return Path(self.home_dir) / ".suisuicode" / "teams"

    def _team_dir(self, sanitized: str) -> Path:
        return self._teams_root() / sanitized

    # ── 扫描还原 ──────────────────────────────────────────

    def _scan_existing(self, teams_root: Path) -> None:
        """扫描 teams 目录，还原已持久化的 Team。"""
        if not teams_root.exists():
            return
        for child in sorted(teams_root.iterdir()):
            if not child.is_dir():
                continue
            config_path = child / "config.json"
            if not config_path.exists():
                continue
            try:
                raw = read_json(config_path)
                team = Team.from_dict(raw)
                # 填充派生路径
                team.config_dir = str(child)
                team.config_path = str(config_path)
                team.tasks_path = str(child / "tasks.json")
                team.mailbox_dir = str(child / "mailbox")
                self.teams[team.sanitized_name] = team
            except Exception as e:
                print(
                    f"team.Manager: 跳过损坏的 Team 目录 {child.name}: {e}",
                    file=sys.stderr,
                )

    # ── 查询 ──────────────────────────────────────────────

    def get(self, name: str) -> Team | None:
        """按 sanitized name 精确查找；也接受原始名尝试 sanitize 后查找。"""
        key = sanitize(name)
        t = self.teams.get(key)
        if t is not None:
            return t
        # 也尝试直接用原 name 查（支持"已 persist 但 sanitized 完全相同的场景"）
        return self.teams.get(name)

    def list_(self) -> list[Team]:
        """返回所有 Team，按创建时间排序。"""
        return sorted(self.teams.values(), key=lambda t: t.created_at)

    # ── 创建 ──────────────────────────────────────────────

    async def create(
        self,
        name: str,
        description: str = "",
        backend_type: BackendType | None = None,
    ) -> Team:
        """创建一个新 Team，返回 Team 对象。

        Raises:
            ValueError: name sanitize 后为空
        """
        sanitized = sanitize(name)
        if not sanitized:
            raise ValueError(f"Team 名 sanitize 后为空: {name!r}")

        # 同名冲突检测 + 自动后缀
        final_name = sanitized
        suffix = 1
        while final_name in self.teams:
            suffix += 1
            final_name = f"{sanitized}-{suffix}"

        # 确定后端
        if backend_type is None:
            from suisuicode.team.backend.detect import detect

            backend_type = detect()

        team_dir = self._team_dir(final_name)
        team_dir.mkdir(parents=True, exist_ok=True)
        mailbox_dir = team_dir / "mailbox"
        mailbox_dir.mkdir(parents=True, exist_ok=True)

        team = Team(
            name=name,
            sanitized_name=final_name,
            lead_agent_id="lead",
            backend=backend_type,
            description=description,
            created_at=datetime.now(),
            members=[
                TeammateInfo(
                    name="lead",
                    agent_id="lead",
                    is_active=None,
                )
            ],
        )
        team.config_dir = str(team_dir)
        team.config_path = str(team_dir / "config.json")
        team.tasks_path = str(team_dir / "tasks.json")
        team.mailbox_dir = str(mailbox_dir)

        # 原子持久化
        atomic_write_json(team.config_path, team.to_dict())

        async with self._lock:
            self.teams[final_name] = team

        return team

    # ── 删除 ──────────────────────────────────────────────

    async def delete(self, name: str, force: bool = False) -> None:
        """删除 Team 及其所有资源。

        Raises:
            TeamNotFoundError: Team 不存在
            TeamHasActiveMembersError: force=False 且有活跃成员
        """
        async with self._lock:
            team = self.get(name)
            if team is None:
                raise TeamNotFoundError(f"Team 不存在: {name!r}")

            # 非 force 模式检查活跃成员
            if not force and team.has_active_members():
                raise TeamHasActiveMembersError(
                    f"Team {team.sanitized_name!r} 仍有活跃成员，"
                    f"请等待完成或使用 force=True"
                )

            # 逐个清理非 lead 成员资源
            await self._cleanup_team_resources(team)

            # 删整个 config_dir
            config_dir = team.config_dir
            if config_dir and Path(config_dir).exists():
                shutil.rmtree(config_dir, ignore_errors=True)

            # 从 in-memory dict 移除
            del self.teams[team.sanitized_name]

    async def _cleanup_team_resources(self, team: Team) -> None:
        """清理 Team 的非 lead 成员资源（best-effort）。

        对每个非 lead 成员：kill 后端 + 删 session 目录 + 删 worktree。
        失败只 stderr 警告，不中断。
        """
        for member in list(team.members):
            if member.name == "lead":
                continue

            # 杀掉后端（如果有后端实例可用）
            try:
                from suisuicode.team.backend import new_backend

                backend = new_backend(
                    member.backend_type,
                    task_mgr=self.task_mgr,
                )
                await backend.kill(member.pane_id, member.agent_id)
            except Exception as e:
                print(
                    f"team.Manager: kill 成员 {member.name} 失败: {e}",
                    file=sys.stderr,
                )

            # 删 session 目录
            if member.session_dir:
                try:
                    shutil.rmtree(member.session_dir, ignore_errors=True)
                except Exception as e:
                    print(
                        f"team.Manager: 删除 session 目录失败 "
                        f"{member.session_dir}: {e}",
                        file=sys.stderr,
                    )

            # 删 worktree
            if member.worktree_path and self.wt_mgr is not None:
                try:
                    wt_name = Path(member.worktree_path).name
                    await self.wt_mgr.remove(wt_name, discard_changes=True)
                except Exception as e:
                    print(
                        f"team.Manager: 删除 worktree 失败 "
                        f"{member.worktree_path}: {e}",
                        file=sys.stderr,
                    )

    # ── 成员操作 ──────────────────────────────────────────

    async def add_member(
        self, team_name: str, info: TeammateInfo
    ) -> None:
        """向 Team 添加队员并持久化。

        Raises:
            TeamNotFoundError: Team 不存在
            MemberExistsError: name 在 Team 内重复
        """
        team = self.get(team_name)
        if team is None:
            raise TeamNotFoundError(f"Team 不存在: {team_name!r}")

        async with team._lock:
            # 跨进程兜底：先从磁盘重载 members
            await reload_from_disk_locked(team)

            # 检查重名
            if team.member_by_name(info.name) is not None:
                raise MemberExistsError(
                    f"成员 {info.name!r} 已存在于 Team {team.sanitized_name!r}"
                )

            team.members.append(info)
            atomic_write_json(team.config_path, team.to_dict())

    async def set_member_active(
        self, team_name: str, member_name: str, active: bool
    ) -> None:
        """设置队员的 is_active 状态。

        Raises:
            TeamNotFoundError / MemberNotFoundError
        """
        team = self.get(team_name)
        if team is None:
            raise TeamNotFoundError(f"Team 不存在: {team_name!r}")

        async with team._lock:
            # 跨进程兜底：先从磁盘重载 members
            await reload_from_disk_locked(team)

            member = team.member_by_name(member_name)
            if member is None:
                raise MemberNotFoundError(
                    f"成员 {member_name!r} 不在 Team {team.sanitized_name!r}"
                )

            # 精确控制 is_active 语义：None/True/False 三种
            member.is_active = active

            atomic_write_json(team.config_path, team.to_dict())

    async def remove_member(
        self, team_name: str, member_name: str
    ) -> None:
        """从 Team 移除队员并持久化。

        Raises:
            TeamNotFoundError / MemberNotFoundError
        """
        team = self.get(team_name)
        if team is None:
            raise TeamNotFoundError(f"Team 不存在: {team_name!r}")

        async with team._lock:
            # 跨进程兜底
            await reload_from_disk_locked(team)

            member = team.member_by_name(member_name)
            if member is None:
                raise MemberNotFoundError(
                    f"成员 {member_name!r} 不在 Team {team.sanitized_name!r}"
                )

            team.members.remove(member)
            atomic_write_json(team.config_path, team.to_dict())

    # ── Lead mailbox 轮询 ─────────────────────────────────

    async def poll_lead_mailboxes(self) -> list[LeadMessage]:
        """遍历所有 Team，读 lead 邮箱中未读消息，标 read。

        返回聚合后的 LeadMessage 列表。
        """
        from suisuicode.team.mailbox import Box

        results: list[LeadMessage] = []
        for team in self.list_():
            if not team.mailbox_dir:
                continue
            try:
                box = Box(team.mailbox_dir)
                indices, msgs = await box.read_unread(team.lead_agent_id)
                if msgs:
                    await box.mark_read(team.lead_agent_id, indices)
                    for m in msgs:
                        results.append(LeadMessage(
                            team_name=team.sanitized_name,
                            from_=m.from_,
                            type=m.type,
                            summary=m.summary,
                            content=m.content,
                            timestamp=m.timestamp,
                        ))
            except Exception:
                continue
        return results


# ── LeadMessage ──────────────────────────────────────────


@dataclass
class LeadMessage:
    """从 Lead 邮箱轮询到的单条消息。"""
    team_name: str
    from_: str
    type: str
    summary: str
    content: str
    timestamp: int = 0
