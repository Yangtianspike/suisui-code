from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from suisuicode import __version__
from suisuicode.agent.runtime import SessionRuntime
from suisuicode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from suisuicode.config import ConfigError, effective_context_window, load
from suisuicode.hook import Engine as HookEngine, Event as HookEvent, load as hook_load
from suisuicode.permission.engine import new_engine
from suisuicode.tool import new_default_registry
from suisuicode.tui.app import new_app

logger = logging.getLogger(__name__)


async def _amain() -> int:
    parser = argparse.ArgumentParser(
        prog="suisuicode", description="SuisuiCode — 终端 AI 对话助手"
    )
    parser.add_argument(
        "--config",
        default="~/.suisuicode/config.yaml",
        help="配置文件路径（默认：~/.suisuicode/config.yaml）",
    )
    # ch15: --team-member 模式（Pane 后端子进程入口）
    parser.add_argument("--team-member", action="store_true", default=False,
                        help=argparse.SUPPRESS)
    parser.add_argument("--team", default="", help=argparse.SUPPRESS)
    parser.add_argument("--member", default="", help=argparse.SUPPRESS)
    parser.add_argument("--agent-id", default="", help=argparse.SUPPRESS)
    parser.add_argument("--session-dir", default="", help=argparse.SUPPRESS)
    parser.add_argument("--worktree", default="", help=argparse.SUPPRESS)
    parser.add_argument("--agent-type", default="", help=argparse.SUPPRESS)
    parser.add_argument("--model", default="", help=argparse.SUPPRESS)
    parser.add_argument("--plan-mode", action="store_true", default=False,
                        help=argparse.SUPPRESS)
    args = parser.parse_args()

    try:
        cfg = load(args.config)
    except ConfigError as e:
        print(f"[错误] {e}", file=sys.stderr)
        return 1

    registry = new_default_registry()

    # ── MCP 客户端 ────────────────────────────────
    from suisuicode import mcp as mcp_client

    root = str(Path.cwd().resolve())
    mcp_cfg = mcp_client.load_config(root)
    mgr = await mcp_client.new_manager(mcp_cfg, version=__version__)
    try:
        mcp_tool_count = len(mgr.tools())
        for t in mgr.tools():
            registry.register(t)

        # ── 构建 MCP 状态（供 TUI 显示）───────
        mcp_status: dict[str, int] = {}
        if mcp_cfg.servers:
            connected = {t._full_name.split("__")[1] for t in mgr.tools()}
            for name in mcp_cfg.servers:
                if name in connected:
                    count = sum(
                        1
                        for t in mgr.tools()
                        if t._full_name.startswith(f"mcp__{name}__")
                    )
                    mcp_status[name] = count
                else:
                    mcp_status[name] = 0

        # ── 构造权限引擎 ──────────────────────────
        engine, err = new_engine(root)
        if err is not None:
            print(f"权限引擎降级: {err}", file=sys.stderr)

        # ── ch12 Hook 引擎 ─────────────────────────
        hook_engine: HookEngine | None = None
        try:
            hook_engine = hook_load(root)
        except Exception as e:
            print(f"hook 引擎加载失败(降级): {e}", file=sys.stderr)

        # ── ch13 SubAgent Catalog ───────────────────
        from suisuicode.subagent import load_catalog as load_subagent_catalog

        subagent_catalog = load_subagent_catalog(root)

        # ── ch14 Worktree Manager ────────────────────
        from suisuicode import worktree

        worktree_mgr = None
        try:
            worktree_mgr = worktree.Manager(root)
        except Exception as exc:
            print(f"Worktree 管理器降级: {exc}", file=sys.stderr)
        else:
            asyncio.get_event_loop().create_task(
                worktree_mgr.sweep_stale(datetime.now() - timedelta(hours=24))
            )

        # ── ch13 Task Manager ────────────────────────
        from suisuicode.task import Manager as TaskManager

        task_mgr = TaskManager()

        # ── ch13 Task 工具注册 ───────────────────────
        from suisuicode.task.tools import (
            TaskListTool,
            TaskGetTool,
            TaskStopTool,
            SendMessageTool,
        )

        registry.register(TaskListTool(task_mgr))
        registry.register(TaskGetTool(task_mgr))
        registry.register(TaskStopTool(task_mgr))
        registry.register(SendMessageTool(task_mgr))

        # ── ch09 项目指令加载 ─────────────────────
        from suisuicode.instructions import load_instructions

        instruction_text = load_instructions(root)

        # ── ch09 记忆管理器初始化 ────────────────
        from suisuicode.memory import Manager

        user_home = os.path.expanduser("~")
        project_mem_dir = os.path.join(root, ".suisuicode", "memory")
        user_mem_dir = os.path.join(user_home, ".suisuicode", "memory")
        mem_mgr = Manager(
            project_dir=project_mem_dir,
            user_dir=user_mem_dir,
            provider=None,  # 延迟到 provider 选定后设置
            model="",
        )
        memory_text = mem_mgr.load_index()

        # ── ch09 SessionRuntime ────────────────────
        session = new_session_context(workspace=root)
        replacement = ContentReplacementState()
        recovery = RecoveryState()
        auto_tracking = CompactCircuitBreaker()

        cw = 200000
        if len(cfg.providers) == 1:
            cw = effective_context_window(cfg.providers[0])

        runtime = SessionRuntime(
            replacement=replacement,
            recovery=recovery,
            auto_tracking=auto_tracking,
            session=session,
            context_window=cw,
        )

        # ── ch09 Session Writer ────────────────────
        from suisuicode.session import Writer

        writer = Writer(session.session_dir)

        # ── ch09 后台会话清理 ─────────────────────
        sessions_dir = os.path.join(root, ".suisuicode", "sessions")
        asyncio.create_task(_cleanup_task(sessions_dir))

        # ── ch13 Agent 工具注册 ────────────────────
        from suisuicode.agent.agent_tool import AgentTool

        bg_enabled = cfg.effective_enable_subagent_background()

        # ── ch15: Team Manager ──────────────────────
        from suisuicode.team.registry import AgentNameRegistry
        from suisuicode.team.manager import Manager as TeamManager

        name_reg = AgentNameRegistry()
        task_mgr.set_name_registry(name_reg)

        user_home_path = os.path.expanduser("~")
        team_mgr = TeamManager(
            home_dir=user_home_path,
            project_root=root,
            wt_mgr=worktree_mgr,
            task_mgr=task_mgr,
            registry=name_reg,
        )

        # ── ch15: 注册 7 个 Team 工具 ──────────────
        from suisuicode.team.tools.team_create import TeamCreateTool
        from suisuicode.team.tools.team_delete import TeamDeleteTool
        from suisuicode.team.tools.task_create import TaskCreateTool
        from suisuicode.team.tools.task_get import TaskGetTool as TeamTaskGetTool
        from suisuicode.team.tools.task_list import TaskListTool as TeamTaskListTool
        from suisuicode.team.tools.task_update import TaskUpdateTool as TeamTaskUpdateTool
        from suisuicode.team.tools.send_message import SendMessageTool as TeamSendMessageTool

        # 协作工具的 store getter（基于活跃 Team 的 tasks_path）
        def _store_getter():
            teams = team_mgr.list_()
            if not teams:
                raise RuntimeError("没有活跃的 Team")
            from suisuicode.team.tasks import Store
            return Store(teams[0].tasks_path)

        registry.register(TeamCreateTool(team_mgr))
        registry.register(TeamDeleteTool(team_mgr))
        registry.register(TaskCreateTool(_store_getter))
        registry.register(TeamTaskGetTool(_store_getter))
        registry.register(TeamTaskListTool(_store_getter))
        registry.register(TeamTaskUpdateTool(_store_getter))
        registry.register(TeamSendMessageTool(team_mgr))

        # ── ch15: Agent 工具注入 team_hook ─────────
        from suisuicode.team.spawn import spawn_teammate

        class _TeamHookAdapter:
            """把 team.spawn_teammate 适配为 agent.TeamHook Protocol。"""
            def __init__(self, tm, ctx_ref):
                self._tm = tm
                self._ctx_ref = ctx_ref

            async def spawn_teammate(self, req) -> str:
                return await spawn_teammate(
                    self._tm, req, self._ctx_ref[0] if self._ctx_ref else None
                )

            def is_teammate_context(self, ctx) -> tuple:
                from suisuicode.agent.team_hook import teammate_context_from_ctx
                tc = teammate_context_from_ctx(ctx)
                if tc is not None:
                    return (tc.team_name, tc.member_name, True)
                return ("", "", False)

        _ctx_ref: list[dict | None] = [None]  # 引用槽，由 Agent 在每轮更新
        team_hook = _TeamHookAdapter(team_mgr, _ctx_ref)

        agent_tool = AgentTool(subagent_catalog, task_mgr, parent=None,
                               bg_enabled=bg_enabled,
                               worktree_mgr=worktree_mgr,
                               team_hook=team_hook)
        registry.register(agent_tool)

        # ── ch15: on_task_done 回调（队员 idle 通知）───
        async def _on_teammate_done(task_id: str) -> None:
            """队员 run_to_completion 结束时通知 Lead。"""
            try:
                member_name = name_reg.name_of(task_id)
                if member_name:
                    for team in team_mgr.list_():
                        member = team.member_by_name(member_name)
                        if member is not None:
                            await team_mgr.set_member_active(
                                team.sanitized_name, member_name, False
                            )
                            if team.mailbox_dir:
                                from suisuicode.team.mailbox import Box, Message
                                box = Box(team.mailbox_dir)
                                await box.write(
                                    team.lead_agent_id,
                                    Message(
                                        from_=member_name,
                                        to=team.lead_agent_id,
                                        type="text",
                                        summary=f"{member_name} idle",
                                        content=f"agent {task_id} 已完成任务",
                                    ),
                                )
                            break
            except Exception:
                pass

        task_mgr.on_task_done(_on_teammate_done)

        # ── ch15: Coordinator Mode ──────────────────
        from suisuicode.coordinator import is_enabled as coordinator_enabled

        coordinator_mode = coordinator_enabled(cfg)

        # ── ch15: --team-member 分支 ────────────────
        if args.team_member:
            from suisuicode.team.member import run_team_member

            await run_team_member(
                team_mgr=team_mgr,
                team_name=args.team,
                member_name=args.member,
                agent_id=args.agent_id,
                session_dir=args.session_dir,
                worktree=args.worktree,
                agent_type=args.agent_type,
                model=args.model,
                plan_mode=args.plan_mode,
            )
            return 0

        try:
            app = new_app(
                cfg.providers,
                "dev",
                registry,
                engine,
                mcp_servers=mcp_status,
                runtime=runtime,
                providers_config=cfg.providers if len(cfg.providers) > 1 else None,
                writer=writer,
                mem_mgr=mem_mgr,
                instruction_text=instruction_text,
                memory_text=memory_text,
                hook_engine=hook_engine,
                task_mgr=task_mgr,
                subagent_catalog=subagent_catalog,
                worktree_mgr=worktree_mgr,
                team_mgr=team_mgr,
                coordinator_mode=coordinator_mode,
            )
            # ch13: 回填 Agent 工具的 parent
            if app.main_agent is not None:
                agent_tool.set_parent(app.main_agent)
                # ch15: Coordinator Mode — 收窄 Lead 工具
                if coordinator_mode:
                    from suisuicode.coordinator import (
                        allowed_tools as coordinator_tools,
                        system_prompt_suffix as coordinator_prompt,
                    )
                    app.main_agent.set_allowed_tools(coordinator_tools())
                    # 追加 coordinator 提示词到 system_prompt
                    if hasattr(app.main_agent, "system_prompt"):
                        app.main_agent.system_prompt += coordinator_prompt()
            await app.run_async()
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"[错误] 意外错误：{e}", file=sys.stderr)
            return 1
        finally:
            # ch12: SessionEnd 兜底（Ctrl+C / 正常退出都会走）
            if hook_engine is not None:
                try:
                    await hook_engine.dispatch(
                        HookEvent.SESSION_END,
                        {"event": "SessionEnd",
                         "session_id": runtime.session.session_id,
                         "cwd": root,
                         "mode": str(engine.start_mode)},
                    )
                except Exception:
                    pass
            writer.close()
    finally:
        await mgr.close()

    return 0


async def _cleanup_task(sessions_dir: str) -> None:
    """后台异步执行会话过期清理。"""
    try:
        from suisuicode.session import clean_expired

        await asyncio.to_thread(
            clean_expired, sessions_dir, timedelta(days=30)
        )
    except Exception:
        logger.debug("后台会话清理失败", exc_info=True)


def main() -> None:
    try:
        exit_code = asyncio.run(_amain())
    except KeyboardInterrupt:
        exit_code = 0
    raise SystemExit(exit_code)
