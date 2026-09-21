"""/skill — Skill 管理命令（list / info / reload）。"""

from __future__ import annotations


async def handle_skill(ui, args="") -> None:
    """/skill [list | info <name> | reload]

    无子命令默认为 list。
    """
    parts = args.split(maxsplit=1) if args.strip() else []
    sub = parts[0].lower() if parts else "list"
    sub_args = parts[1] if len(parts) > 1 else ""

    if sub == "reload":
        if hasattr(ui, "reload_skills"):
            await ui.reload_skills()
            ui.ui_println("Skills reloaded. 使用 /help 查看更新后的命令列表。")
        else:
            ui.ui_error("Skill reload 不可用")
        return

    if sub == "info":
        if not sub_args.strip():
            ui.ui_error("用法: /skill info <name>")
            return
        _show_info(ui, sub_args.strip())
        return

    # default: list
    _show_list(ui)


def _show_list(ui) -> None:
    skills = ui.list_catalog_skills()
    if not skills:
        ui.ui_println("Available skills (0):")
        ui.ui_println("")
        ui.ui_println("  将 SKILL.md 放入 .suisuicode/skills/<name>/ 即可加载。")
        return

    order = {"project": 0, "user": 1, "claude": 2}
    skills.sort(key=lambda s: (order.get(s.get("source", "?"), 99), s["name"]))

    lines = ["Available skills ({}):".format(len(skills))]
    for s in skills:
        name = s["name"]
        desc = s["description"]
        mode = s.get("mode", "inline")
        if mode == "fork":
            lines.append("/{} (fork) — {}".format(name, desc))
        else:
            lines.append("/{} — {}".format(name, desc))

    lines.append("")
    lines.append("Type /<skill-name> to invoke a skill.")
    ui.ui_println("\n".join(lines))


def _show_info(ui, name: str) -> None:
    skills = ui.list_catalog_skills()
    for s in skills:
        if s.get("name") == name:
            lines = [
                "Skill: /{}".format(name),
                "Description: {}".format(s["description"]),
                "Source: {}".format(s.get("source", "?")),
                "Mode: {}".format(s.get("mode", "inline")),
            ]
            if s.get("model"):
                lines.append("Model: {}".format(s["model"]))
            if s.get("allowed_tools"):
                lines.append(
                    "Allowed tools: {}".format(
                        ", ".join(s["allowed_tools"])
                    )
                )
            ui.ui_println("\n".join(lines))
            return
    ui.ui_error("未知 skill: {}".format(name))
