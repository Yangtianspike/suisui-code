"""TUI 命令胶水层：format_compact_notice + App UI Protocol 方法 + dispatch_slash。

ch10 重写：旧 builtin_commands / dispatch_command / handle_* 函数已移除，
命令注册与分发收编进 suisuicode.command 包。UI 方法直接通过 monkey-patch
附加到 SuisuiCodeApp 类上。
"""

from __future__ import annotations

from suisuicode.agent.event import CompactEvent, CompactPhase


def format_compact_notice(ev: CompactEvent) -> str:
    """按 phase 返回统一的压缩状态文案（Claude Code 风格）。"""
    if ev.phase == CompactPhase.BEFORE_AUTO:
        return "Compacting context..."
    if ev.phase == CompactPhase.BEFORE_EMERGENCY:
        return "Context limit exceeded, compacting..."
    if ev.err is not None:
        return f"Compact failed: {ev.err}"
    return f"Compacted: {ev.before} → {ev.after} estimated tokens"
