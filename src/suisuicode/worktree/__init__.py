"""Worktree 隔离子包 — Git Worktree 完整生命周期管理。

提供 Manager（创建/进入/退出/删除/自动清理/过期清理）、
Slug 校验、WorktreeSession 持久化等。
"""

from __future__ import annotations

from suisuicode.worktree.slug import flat_slug, validate_slug

# 触发方法绑定（将 create / lifecycle / sweep 的独立函数绑到 Manager 类）
import suisuicode.worktree.bindings  # noqa: F401  — 副作用导入

__all__ = [
    "Manager",
    "ExitAction",
    "ExitOptions",
    "ExitReport",
    "AutoCleanupReport",
    "Worktree",
    "WorktreeSession",
    "WorktreeHasChangesError",
    "validate_slug",
    "flat_slug",
    "random_agent_name",
]


# 延迟导入以避免循环依赖
def __getattr__(name: str):
    if name == "Manager":
        from suisuicode.worktree.manager import Manager

        return Manager
    if name == "ExitAction":
        from suisuicode.worktree.lifecycle import ExitAction

        return ExitAction
    if name == "ExitOptions":
        from suisuicode.worktree.lifecycle import ExitOptions

        return ExitOptions
    if name == "ExitReport":
        from suisuicode.worktree.lifecycle import ExitReport

        return ExitReport
    if name == "AutoCleanupReport":
        from suisuicode.worktree.lifecycle import AutoCleanupReport

        return AutoCleanupReport
    if name == "Worktree":
        from suisuicode.worktree.create import Worktree

        return Worktree
    if name == "WorktreeSession":
        from suisuicode.worktree.session import WorktreeSession

        return WorktreeSession
    if name == "WorktreeHasChangesError":
        from suisuicode.worktree.lifecycle import WorktreeHasChangesError

        return WorktreeHasChangesError
    if name == "random_agent_name":
        from suisuicode.worktree.sweep import random_agent_name

        return random_agent_name
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
