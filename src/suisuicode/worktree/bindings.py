"""将 create / lifecycle / sweep 的方法绑到 Manager 类上。

在 worktree 包 import 时执行一次即可。
"""

from __future__ import annotations

from suisuicode.worktree.create import create
from suisuicode.worktree.lifecycle import (
    auto_cleanup,
    enter,
    exit,
    remove,
)
from suisuicode.worktree.manager import Manager
from suisuicode.worktree.sweep import sweep_stale


def _install() -> None:
    """将各模块的独立函数绑定为 Manager 方法。"""
    Manager.create = create  # type: ignore[method-assign]
    Manager.enter = enter  # type: ignore[method-assign]
    Manager.exit = exit  # type: ignore[method-assign]
    Manager.remove = remove  # type: ignore[method-assign]
    Manager.auto_cleanup = auto_cleanup  # type: ignore[method-assign]
    Manager.sweep_stale = sweep_stale  # type: ignore[method-assign]


_install()
