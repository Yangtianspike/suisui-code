"""Manager 生命周期方法：enter / exit / remove / auto_cleanup。"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from suisuicode.worktree.session import WorktreeSession

if TYPE_CHECKING:
    from suisuicode.worktree.manager import Manager


# ── 辅助类型 ───────────────────────────────────────────


class ExitAction(str, Enum):
    KEEP = "keep"
    REMOVE = "remove"


@dataclass
class ExitOptions:
    discard_changes: bool = False


@dataclass
class ExitReport:
    removed: bool
    path: str
    branch: str


@dataclass
class AutoCleanupReport:
    kept: bool
    path: str = ""
    branch: str = ""


class WorktreeHasChangesError(Exception):
    """Worktree 有未提交修改或本地多于 base 的 commit。"""


# ── enter ───────────────────────────────────────────────


async def enter(self: Manager, name: str) -> "WorktreeSession":
    """进入 Worktree——不改变进程 cwd，只记录 worktree_path。

    Returns:
        WorktreeSession 对象（自动持久化到 session_file）。
    """
    from suisuicode.worktree.session import save_session

    async with self.lock:
        wt = self.active.get(name)
        if wt is None:
            raise ValueError(f"Worktree 不存在: {name}")

        original_cwd = str(Path.cwd())

        # 读原分支和 commit
        from suisuicode.worktree.git import _run_git

        original_branch = ""
        original_head = ""
        try:
            original_branch = await _run_git(
                self.repo_root, "rev-parse", "--abbrev-ref", "HEAD",
            )
            original_head = await _run_git(
                self.repo_root, "rev-parse", "HEAD",
            )
        except Exception:
            pass

        session_id = secrets.token_hex(8)

        session = WorktreeSession(
            original_cwd=original_cwd,
            worktree_path=wt.path,
            worktree_name=name,
            original_branch=original_branch,
            original_head_commit=original_head,
            session_id=session_id,
        )

        self.current_session = session
        save_session(self.session_file, session)

        return session


# ── exit ────────────────────────────────────────────────


async def exit(
    self: Manager,
    name: str,
    action: ExitAction,
    opts: ExitOptions | None = None,
) -> ExitReport:
    """退出 Worktree 会话。

    - action=KEEP：仅清除 session，保留 Worktree
    - action=REMOVE（无 discard）：有变更抛 WorktreeHasChangesError
    - action=REMOVE（discard=True）：强制删除

    Raises:
        ValueError: current_session 非空且 worktree_name != name
        WorktreeHasChangesError: 有变更且未显式 discard
    """
    if opts is None:
        opts = ExitOptions()

    from suisuicode.worktree.git import _has_worktree_changes, _run_git
    from suisuicode.worktree.session import save_session

    async with self.lock:
        # 校验 current_session
        if self.current_session is None:
            raise ValueError("当前没有活跃的 Worktree 会话")
        if self.current_session.worktree_name != name:
            raise ValueError(
                f"只能退出当前会话 ({self.current_session.worktree_name})，"
                f"不能退出 {name}"
            )

        wt = self.active.get(name)
        if wt is None:
            raise ValueError(f"Worktree 不存在: {name}")

        # 变更保护
        if action == ExitAction.REMOVE and not opts.discard_changes:
            has_changes = await _has_worktree_changes(wt.path, wt.head_commit)
            if has_changes:
                raise WorktreeHasChangesError(
                    f"Worktree {name} 有未提交修改或新 commit，"
                    f"请加 --discard 强制删除"
                )

        # os.chdir 兜底
        with contextlib.suppress(OSError):
            os.chdir(self.current_session.original_cwd)

        # 清除 session（只改字段，不删文件——tui/App 在退出时靠 current_session=None 跳过）
        self.current_session = None
        save_session(self.session_file, None)

        # remove
        if action == ExitAction.REMOVE:
            await _run_git(
                self.repo_root, "worktree", "remove", "--force", wt.path,
            )
            await asyncio.sleep(0.1)
            try:
                await _run_git(self.repo_root, "branch", "-D", wt.branch)
            except Exception:
                pass
            del self.active[name]

        return ExitReport(
            removed=action == ExitAction.REMOVE,
            path=wt.path,
            branch=wt.branch,
        )


# ── remove ──────────────────────────────────────────────


async def remove(self: Manager, name: str, opts: ExitOptions | None = None) -> None:
    """独立 remove 入口，允许删除非当前 session 的 Worktree。"""
    if opts is None:
        opts = ExitOptions()

    from suisuicode.worktree.git import _has_worktree_changes, _run_git

    async with self.lock:
        wt = self.active.get(name)
        if wt is None:
            raise ValueError(f"Worktree 不存在: {name}")

        # 变更保护
        if not opts.discard_changes:
            has_changes = await _has_worktree_changes(wt.path, wt.head_commit)
            if has_changes:
                raise WorktreeHasChangesError(
                    f"Worktree {name} 有未提交修改或新 commit，"
                    f"请加 --discard 强制删除"
                )

        # 如果是当前 session，清除 session
        if self.current_session is not None and self.current_session.worktree_name == name:
            from suisuicode.worktree.session import save_session

            self.current_session = None
            save_session(self.session_file, None)

        await _run_git(
            self.repo_root, "worktree", "remove", "--force", wt.path,
        )
        await asyncio.sleep(0.1)
        try:
            await _run_git(self.repo_root, "branch", "-D", wt.branch)
        except Exception:
            pass
        del self.active[name]


# ── auto_cleanup ────────────────────────────────────────


async def auto_cleanup(self: Manager, name: str) -> AutoCleanupReport:
    """SubAgent 退出时自动清理。

    - manual=True 直接保留
    - 无变更直接删除
    - 有变更保留并报告路径
    """
    wt = self.active.get(name)
    if wt is None:
        return AutoCleanupReport(kept=False)

    # manual 创建的永不自动清理
    if wt.manual:
        return AutoCleanupReport(kept=True, path=wt.path, branch=wt.branch)

    from suisuicode.worktree.git import _has_worktree_changes

    has_changes = await _has_worktree_changes(wt.path, wt.head_commit)

    if not has_changes:
        await remove(self, name, ExitOptions(discard_changes=True))
        return AutoCleanupReport(kept=False)

    return AutoCleanupReport(kept=True, path=wt.path, branch=wt.branch)
