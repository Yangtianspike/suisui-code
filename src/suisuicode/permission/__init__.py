"""权限系统：五层防御链（黑名单→沙箱→规则→模式兜底→人在回路）。

前四层由 Engine.check 实现，第五层人在回路由 agent 编排驱动。
"""

from __future__ import annotations

from enum import IntEnum


class Mode(IntEnum):
    """四档权限模式，运行时可在 TUI 中按 Shift+Tab 循环切换。"""

    DEFAULT = 0  # 只读 Allow / 文件写 Ask / 命令执行 Ask
    ACCEPT_EDITS = 1  # 文件写 Allow / 命令执行 Ask
    PLAN = 2  # 仅只读工具可见（沿用 ch04）；矩阵同 default 作防御兜底
    BYPASS = 3  # 全 Allow（黑名单/沙箱仍拦）

    def __str__(self) -> str:
        _map = {
            Mode.DEFAULT: "default",
            Mode.ACCEPT_EDITS: "acceptEdits",
            Mode.PLAN: "plan",
            Mode.BYPASS: "bypassPermissions",
        }
        return _map[self]


def parse_mode(s: str) -> tuple[Mode, bool]:
    """大小写不敏感识别四档名；未知返回 (Mode.DEFAULT, False)。"""
    s_lower = s.strip().lower()
    for m in Mode:
        if str(m).lower() == s_lower:
            return m, True
    return Mode.DEFAULT, False


class Decision(IntEnum):
    """前四层判定结果。"""

    ALLOW = 0
    DENY = 1
    ASK = 2  # 落到人在回路


class Category(IntEnum):
    """工具类别，用于模式兜底矩阵。"""

    READ = 0
    WRITE = 1
    EXEC = 2


class Outcome(IntEnum):
    """人在回路三选一结果。"""

    DENY_ONCE = 0  # 拒绝本次
    ALLOW_ONCE = 1  # 允许本次（不留规则）
    ALLOW_FOREVER = 2  # 永久允许（+写本地层文件，精确匹配）


class ApprovalError(Exception):
    """权限相关异常基类。"""

    pass


# ── 延迟导入避免循环依赖 ──────────────────────────────
def __getattr__(name: str):
    """按需导入 engine 相关符号。"""
    if name in ("Engine", "new_engine", "check", "mode_fallback", "start_mode"):
        from suisuicode.permission import engine as _engine

        return getattr(_engine, name)
    if name == "persist_local_allow":
        from suisuicode.permission import persist as _persist

        return _persist.persist_local_allow
    raise AttributeError(f"module 'suisuicode.permission' has no attribute {name!r}")
