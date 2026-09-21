"""/worktree 命令 handler + 子命令解析。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from suisuicode.command.ui import UI


async def handle_worktree(ui: UI, args: str) -> None:
    """分发 /worktree 子命令。

    子命令：
    - create <slug>
    - list
    - enter <slug>
    - exit [--remove] [--discard]
    - remove <slug> [--discard]
    """
    accessor = ui.worktree_accessor()
    if accessor is None:
        ui.ui_println("ERROR\x00Worktree 功能未启用（非 git 仓库或管理器降级）")
        return

    parts = args.strip().split()
    if not parts:
        ui.ui_println("用法: /worktree <create|list|enter|exit|remove> [...]")
        return

    sub = parts[0]
    rest = parts[1:]

    if sub == "create":
        if not rest:
            ui.ui_println("用法: /worktree create <slug>")
            return
        slug = rest[0]
        try:
            wt_path, branch = await accessor.create(slug)
            ui.ui_println(f"Worktree 已创建: {wt_path} (分支 {branch})")
        except Exception as e:
            ui.ui_println(f"ERROR\x00创建 Worktree 失败: {e}")

    elif sub == "list":
        items = accessor.list()
        if not items:
            ui.ui_println("(无 Worktree)")
        else:
            for wt in items:
                flags = []
                if wt.active:
                    flags.append("active")
                if wt.manual:
                    flags.append("manual")
                flag_str = f" [{', '.join(flags)}]" if flags else ""
                ui.ui_println(f"  {wt.name}{flag_str}")
                ui.ui_println(f"    路径: {wt.path}  分支: {wt.branch}")

    elif sub == "enter":
        if not rest:
            ui.ui_println("用法: /worktree enter <slug>")
            return
        slug = rest[0]
        try:
            await accessor.enter(slug)
            ui.ui_println(f"已进入 {slug}")
        except Exception as e:
            ui.ui_println(f"ERROR\x00进入 Worktree 失败: {e}")

    elif sub == "exit":
        discard = "--discard" in rest
        action = "remove" if "--remove" in rest else "keep"
        try:
            removed = await accessor.exit(action, discard)
            if removed:
                ui.ui_println("已退出并删除 Worktree")
            else:
                ui.ui_println("已退出 Worktree（保留副本）")
        except Exception as e:
            ui.ui_println(f"ERROR\x00退出 Worktree 失败: {e}")

    elif sub == "remove":
        if not rest:
            ui.ui_println("用法: /worktree remove <slug> [--discard]")
            return
        slug = rest[0]
        discard = "--discard" in rest
        try:
            await accessor.remove(slug, discard)
            ui.ui_println(f"已删除 Worktree: {slug}")
        except Exception as e:
            ui.ui_println(f"ERROR\x00删除 Worktree 失败: {e}")

    else:
        ui.ui_println(f"未知子命令: {sub}。用法: /worktree <create|list|enter|exit|remove> [...]")
