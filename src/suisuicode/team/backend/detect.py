"""后端自动检测 — 按优先级一次性决定 BackendType。"""

from __future__ import annotations

import os
import shutil

from suisuicode.team.types import BackendType


def detect() -> BackendType:
    """按以下优先级检测可用的执行后端：

    1. ``$TMUX`` 已设置 → tmux（当前在 tmux 会话内）
    2. ``$TERM_PROGRAM == "iTerm.app"`` 且 ``it2`` 可执行 → iterm2
    3. ``tmux`` 在 PATH 中 → tmux（外部 spawn 新 session）
    4. 否则 → in-process
    """
    if os.environ.get("TMUX"):
        return BackendType.TMUX

    if os.environ.get("TERM_PROGRAM") == "iTerm.app" and shutil.which("it2"):
        return BackendType.ITERM2

    if shutil.which("tmux"):
        return BackendType.TMUX

    return BackendType.IN_PROCESS
