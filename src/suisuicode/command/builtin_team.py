"""/team 系列 slash 命令（F59-F62）。"""

from __future__ import annotations

from suisuicode.command.command import Command, Kind


def register_team_commands(reg, team_mgr) -> None:
    """向 Registry 注册 4 个 /team 子命令。"""

    # /team list
    async def handle_team_list(ui, args: str) -> None:
        teams = team_mgr.list_()
        if not teams:
            await ui.show_message("没有活跃的团队。")
            return

        lines = ["Teams:"]
        for t in teams:
            active_count = sum(
                1 for m in t.members if m.is_active is not False
            )
            total = len(t.members)
            lines.append(
                f"  {t.sanitized_name}  {t.backend}  "
                f"{total} 成员  [{active_count} 活跃]"
            )
        await ui.show_message("\n".join(lines))

    reg.register(Command(
        name="team",
        description="团队管理：list / info <name> / delete <name> / kill <member>",
        kind=Kind.LOCAL,
        handler=_build_team_handler(team_mgr),
    ))


def _build_team_handler(team_mgr):
    """构造 /team 命令 handler（含子命令分发）。"""

    async def handle_team(ui, args: str) -> None:
        parts = args.strip().split()
        if not parts:
            await ui.show_message(
                "/team 子命令: list | info <name> | delete <name> [--force] | kill <member>"
            )
            return

        sub = parts[0].lower()

        if sub == "list":
            teams = team_mgr.list_()
            if not teams:
                await ui.show_message("没有活跃的团队。")
                return
            lines = ["Teams:"]
            for t in teams:
                active_count = sum(
                    1 for m in t.members if m.is_active is not False
                )
                total = len(t.members)
                lines.append(
                    f"  {t.sanitized_name}  {t.backend}  "
                    f"{total} 成员  [{active_count}/{total}] 活跃"
                )
            await ui.show_message("\n".join(lines))

        elif sub == "info":
            if len(parts) < 2:
                await ui.show_message("用法: /team info <name>")
                return
            name = parts[1]
            team = team_mgr.get(name)
            if team is None:
                await ui.show_message(f"Team 不存在: {name!r}")
                return
            lines = [
                f"Team: {team.sanitized_name}",
                f"  原始名: {team.name}",
                f"  后端: {team.backend}",
                f"  配置: {team.config_path}",
                f"  成员 ({len(team.members)}):",
            ]
            for m in team.members:
                status = "活跃" if m.is_active is not False else "空闲"
                lines.append(
                    f"    {m.name}  agent={m.agent_id}  "
                    f"backend={m.backend_type}  {status}"
                )
                if m.worktree_path:
                    lines.append(f"      worktree: {m.worktree_path}")
                if m.pane_id:
                    lines.append(f"      pane: {m.pane_id}")
            await ui.show_message("\n".join(lines))

        elif sub == "delete":
            if len(parts) < 2:
                await ui.show_message("用法: /team delete <name> [--force]")
                return
            name = parts[1]
            force = "--force" in parts
            try:
                await team_mgr.delete(name, force=force)
                await ui.show_message(f"Team {name!r} 已删除。")
            except Exception as e:
                await ui.show_message(f"删除失败: {e}")

        elif sub == "kill":
            if len(parts) < 2:
                await ui.show_message("用法: /team kill <member>")
                return
            member_name = parts[1]
            # 找该成员所属的 Team
            found = False
            for team in team_mgr.list_():
                member = team.member_by_name(member_name)
                if member is not None:
                    from suisuicode.team.backend import new_backend

                    try:
                        backend = new_backend(
                            member.backend_type,
                            task_mgr=team_mgr.task_mgr,
                        )
                        await backend.kill(member.pane_id, member.agent_id)
                        await team_mgr.remove_member(
                            team.sanitized_name, member_name
                        )
                        await ui.show_message(
                            f"成员 {member_name!r} 已从 "
                            f"Team {team.sanitized_name} 中移除。"
                        )
                        found = True
                    except Exception as e:
                        await ui.show_message(f"kill 失败: {e}")
                        found = True
                    break
            if not found:
                await ui.show_message(f"未找到成员: {member_name!r}")

        else:
            await ui.show_message(f"未知子命令: {sub!r}")

    return handle_team
