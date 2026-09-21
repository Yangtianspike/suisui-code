"""/help /status /memory /permission /session — 5 条纯本地命令。"""

from __future__ import annotations

from suisuicode.command.command import Handler
from suisuicode.command.registry import Registry


def make_help_handler(reg: Registry) -> Handler:
    """工厂：捕获 reg，输出按字典序排列的命令名/描述列表。"""

    async def _handler(ui, args="") -> None:
        cmds = reg.visible()
        if not cmds:
            ui.ui_println("（无已注册命令）")
            return
        w = max(len(c.name) for c in cmds)
        lines = [f"/{c.name.ljust(w)}  {c.description}" for c in cmds]
        ui.ui_println("\n".join(lines))

    return _handler


async def handle_status(ui, args="") -> None:
    """输出 6 行 key:value 状态信息。"""
    lines = [
        "SuisuiCode Status",
        "",
        f"Mode:       {ui.ui_mode().name}",
        f"Tokens:     {ui.ui_usage_in()} in / {ui.ui_usage_out()} out",
        f"Tools:      {ui.ui_tool_count()} enabled",
        f"Memories:   {len(ui.ui_memory_files())} files",
        f"Model:      {ui.ui_model_name()}",
        f"Directory:  {ui.ui_cwd()}",
    ]
    ui.ui_println("\n".join(lines))


async def handle_memory(ui, args="") -> None:
    """列出项目层与用户层记忆文件名。"""
    files = ui.ui_memory_files()
    if not files:
        ui.ui_println("无已加载的记忆文件")
        return
    for f in files:
        ui.ui_println(f)


async def handle_permission(ui, args="") -> None:
    """输出当前权限模式名称。"""
    ui.ui_println(ui.ui_mode().name)


async def handle_session(ui, args="") -> None:
    """输出当前会话标识信息。"""
    ui.ui_println(f"Session: {ui.ui_session_id()}\nPath: {ui.ui_session_path()}")
