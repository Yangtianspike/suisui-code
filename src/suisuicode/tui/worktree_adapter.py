"""WorktreeAccessor 适配器：将 worktree.Manager 适配为 command 包的协议。

TUI 通过此适配器桥接 Manager 到 slash 命令 handler。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from suisuicode.command.ui import WorktreeSummary

if TYPE_CHECKING:
    from suisuicode.worktree.manager import Manager


class WorktreeAdapter:
    """实现 WorktreeAccessor 协议，适配 worktree.Manager。"""

    def __init__(
        self,
        manager: Manager,
        set_active_cwd=None,
    ) -> None:
        self._manager = manager
        self._set_active_cwd = set_active_cwd or (lambda d: None)

    async def create(self, name: str) -> tuple[str, str]:
        """创建 Worktree 并返回 (path, branch)。"""
        wt = await self._manager.create(name, "HEAD", manual=True)
        return (wt.path, wt.branch)

    def list(self) -> list[WorktreeSummary]:
        """列出所有 Worktree 摘要。"""
        result: list[WorktreeSummary] = []
        for wt in self._manager.list():
            active = (
                self._manager.current_session is not None
                and self._manager.current_session.worktree_name == wt.name
            )
            result.append(WorktreeSummary(
                name=wt.name,
                path=wt.path,
                branch=wt.branch,
                active=active,
                manual=wt.manual,
            ))
        return result

    async def enter(self, name: str) -> None:
        """进入 Worktree——调 Manager.enter 并设置 TUI active_cwd。"""
        session = await self._manager.enter(name)
        self._set_active_cwd(session.worktree_path)

    async def exit(self, action: str, discard: bool) -> bool:
        """退出 Worktree 会话。返回是否已删除。"""
        from suisuicode.worktree.lifecycle import ExitAction, ExitOptions

        act = ExitAction.REMOVE if action == "remove" else ExitAction.KEEP
        current_name = (
            self._manager.current_session.worktree_name
            if self._manager.current_session
            else ""
        )
        report = await self._manager.exit(
            current_name,
            act,
            ExitOptions(discard_changes=discard),
        )
        self._set_active_cwd("")
        return report.removed

    async def remove(self, name: str, discard: bool) -> None:
        """删除指定 Worktree。"""
        from suisuicode.worktree.lifecycle import ExitOptions

        await self._manager.remove(name, ExitOptions(discard_changes=discard))
