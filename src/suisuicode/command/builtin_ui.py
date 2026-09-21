"""/exit /plan /compact /resume /clear — 5 条影响界面命令。"""

from __future__ import annotations

from suisuicode.permission import Mode


async def handle_exit(ui, args="") -> None:
    """关闭 TUI 进程。"""
    ui.ui_quit()


async def handle_plan(ui, args="") -> None:
    """切换 PLAN 模式：已在 PLAN 时退出，否则进入。"""
    if ui.ui_mode() == Mode.PLAN:
        ui.ui_set_mode(Mode.DEFAULT)
        ui.ui_println("已退出 PLAN 模式，恢复全工具")
    else:
        ui.ui_set_mode(Mode.PLAN)
        ui.ui_println("已切换到 PLAN 模式（仅只读工具）")


async def handle_compact(ui, args="") -> None:
    """手动触发上下文压缩。"""
    if not ui.ui_idle():
        ui.ui_error("请等待当前任务完成")
        return
    ui.ui_force_compact()


async def handle_resume(ui, args="") -> None:
    """打开历史会话恢复列表。"""
    if not ui.ui_idle():
        ui.ui_error("请等待当前任务完成")
        return
    ui.ui_open_resume_menu()


async def handle_clear(ui, args="") -> None:
    """关闭当前会话、开启新会话（含 skill 清理）。"""
    ui.ui_clear_and_new_session()
    ui.clear_active_skills()
    ui.ui_println("已清空当前会话，开启新 session")
