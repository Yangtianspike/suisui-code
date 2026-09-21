"""/hooks 命令 handler + 输出格式化。"""

from __future__ import annotations


async def handle_hooks(ui, _args: str) -> None:
    """列出已加载的所有 hook 规则。"""
    rules = ui.hook_rules()
    sources = ui.hook_sources()

    if not rules:
        ui.ui_println("No hooks loaded.")
        return

    # 按 event 分组（保留声明顺序）
    groups: dict[str, list] = {}
    for r in rules:
        ev = r.event.value if hasattr(r.event, "value") else str(r.event)
        groups.setdefault(ev, []).append(r)

    for ev in groups:
        ui.ui_println(f"[{ev}]")
        for r in groups[ev]:
            flags = []
            if getattr(r, "only_once", False):
                flags.append("once")
            if getattr(r, "asyncio_mode", False):
                flags.append("async")
            if getattr(r, "reject", False):
                flags.append("reject")
            flag_str = f" [{', '.join(flags)}]" if flags else ""
            atype = r.action.type.value if hasattr(r.action.type, "value") else str(r.action.type)
            ui.ui_println(f"  {r.name}  {ev}  {atype}{flag_str}")

    if sources:
        ui.ui_println(f"Loaded from: {', '.join(sources)}")
