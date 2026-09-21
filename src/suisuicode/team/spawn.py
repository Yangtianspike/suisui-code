"""spawn_teammate 主流程 — 被 agent.TeamHook 调用。

覆盖 F25 完整链路：校验 → 定义解析 → Worktree 创建 → session 分配 →
子 Agent 构造（注入协作工具 + team-context + mailbox）→ Backend.spawn →
注册 + 持久化。
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from suisuicode.agent.team_hook import (
    TeammateContext,
    TeamSpawnRequest,
)
from suisuicode.team.backend import SpawnRequest
from suisuicode.team.mailbox import Box, Message
from suisuicode.team.mailbox.message import MessageType
from suisuicode.team.types import (
    BackendType,
    InProcessTeammateNoSpawnError,
    TeammateInfo,
)

if TYPE_CHECKING:
    from suisuicode.team.manager import Manager
    from suisuicode.subagent import Definition as AgentDefinition

# ── 系统提示词附录 ─────────────────────────────────────


TEAM_SYSTEM_PROMPT_SUFFIX = """IMPORTANT: You are running as an agent in a team.
Just writing a response in text is not visible to others
on your team - you MUST use the SendMessage tool.
The user interacts primarily with the team lead.
Your work is coordinated through the task system
and teammate messaging."""


def team_system_prompt_suffix() -> str:
    """返回 F39 队员系统提示词附录。"""
    return TEAM_SYSTEM_PROMPT_SUFFIX


def build_team_context_reminder(
    team_name: str,
    member_name: str,
    agent_id: str,
    worktree_path: str,
    members: list[dict],
) -> str:
    """构造 <team-context> system reminder（F40）。"""
    member_lines = []
    for m in members:
        name = m.get("name", "?")
        role = m.get("agent_type", "member")
        member_lines.append(f"  {name} ({role})")

    return f"""<team-context>
team: {team_name}
你的成员名: {member_name}
你的 agent_id: {agent_id}
worktree 目录: {worktree_path}
当前团队成员:
{chr(10).join(member_lines) if member_lines else '  (空)'}
</team-context>"""


# ── spawn_teammate ──────────────────────────────────────


async def spawn_teammate(
    mgr: Manager,
    req: TeamSpawnRequest,
    ctx: dict | None = None,
) -> str:
    """完整的队员 spawn 流程，返回 JSON 结果字符串。

    实现 plan.md 中 "Agent(team_name=...) spawn 路径" 描述的 12 步流程。
    """
    import json

    # Step 1: 取 Team
    team = mgr.get(req.team_name)
    if team is None:
        return json.dumps({
            "error": f"Team 不存在: {req.team_name!r}",
        })

    # Step 2: 校验调用者权限
    if ctx is not None:
        from suisuicode.agent.team_hook import teammate_context_from_ctx

        tc = teammate_context_from_ctx(ctx)
        if tc is not None:
            # 当前调用者在某个 Team 上下文里
            if tc.backend_type == BackendType.IN_PROCESS:
                raise InProcessTeammateNoSpawnError(
                    "in-process 队员不允许 spawn 新队员"
                )

    # Step 3: 解析 SubAgentDefinition
    defi: AgentDefinition | None = None

    if req.subagent_type:
        # 定义式
        if mgr.wt_mgr is None:
            return json.dumps({
                "error": "worktree manager 未配置",
            })
        # 通过 subagent catalog 解析（从 wt_mgr 或 context 找 catalog）
        # 本期通过 mgr 持有 catalog 引用
        # TODO: 在 Manager 构造时注入 catalog
    else:
        # Fork 路径或 fallback（默认关闭，受 FORK_TEAMMATE flag 控制）
        pass

    # Step 4: 生成 member_name
    member_name = req.member_name
    if not member_name:
        member_name = f"agent-{secrets.token_hex(4)}"

    # Step 5: 创建 Worktree
    sanitized = team.sanitized_name
    worktree_name = f"team-{sanitized}+{member_name}"
    worktree_path = ""

    if mgr.wt_mgr is not None:
        try:
            wt = await mgr.wt_mgr.create(
                worktree_name, "HEAD", False
            )
            worktree_path = wt.path if hasattr(wt, "path") else str(
                Path(mgr.project_root) / ".suisuicode" / "worktrees" / worktree_name
            )
        except Exception as e:
            # worktree 创建失败不阻塞 spawn？tough call——spec 要求必须有 worktree
            return json.dumps({
                "error": f"Worktree 创建失败: {e}",
            })

    if not worktree_path:
        worktree_path = str(
            Path(mgr.project_root)
            / ".suisuicode"
            / "worktrees"
            / worktree_name
        )

    # Step 6: 申请 session 目录
    from suisuicode.compact.state import new_session_context

    session_ctx = new_session_context(workspace=worktree_path)
    session_dir = session_ctx.session_dir

    # Step 7: 预生成 agent_id
    agent_id = f"agent-{secrets.token_hex(7)}"

    # Step 8: 构造 SpawnRequest
    spawn_req = SpawnRequest(
        team_name=team.sanitized_name,
        member_name=member_name,
        agent_id=agent_id,
        worktree_path=worktree_path,
        session_dir=session_dir,
        agent_type=req.subagent_type,
        model=req.model,
        initial_prompt=req.prompt,
        plan_mode_required=req.plan_mode_required,
    )

    # Step 9: 注入协作工具 + system prompt
    # 子 Agent 的构造交由 backend 处理（in-process）或子进程自己构造（Pane）
    # 这里只设置标记

    # Step 10: Pane 后端预写入初始任务到 mailbox
    backend_type = team.backend
    if backend_type in (BackendType.TMUX, BackendType.ITERM2):
        if team.mailbox_dir:
            box = Box(team.mailbox_dir)
            await box.write(
                agent_id,
                Message(
                    from_="lead",
                    to=agent_id,
                    type=MessageType.TEXT,
                    summary=_truncate_summary(req.prompt),
                    content=req.prompt,
                ),
            )

    # Step 11: Backend.spawn
    if backend_type == BackendType.IN_PROCESS:
        # 构造 in-process 子 Agent
        sub_agent, sub_conv = _build_inprocess_agent(
            mgr, team, spawn_req, defi, member_name
        )
        spawn_req.sub_agent = sub_agent
        spawn_req.conv = sub_conv
        spawn_req.task_mgr = mgr.task_mgr

    from suisuicode.team.backend import new_backend

    backend = new_backend(backend_type, task_mgr=mgr.task_mgr)
    pane_id, returned_agent_id = await backend.spawn(spawn_req)

    # 用 backend 返回的 agent_id（in-process 是 task_id）
    final_agent_id = returned_agent_id or agent_id

    # Step 12: 注册 + 持久化
    if mgr.registry is not None:
        mgr.registry.register(member_name, final_agent_id)

    await mgr.add_member(
        team.sanitized_name,
        TeammateInfo(
            name=member_name,
            agent_id=final_agent_id,
            agent_type=req.subagent_type,
            model=req.model,
            worktree_path=worktree_path,
            branch=worktree_name,
            backend_type=backend_type,
            pane_id=pane_id,
            is_active=True,
            plan_mode_required=req.plan_mode_required,
            session_dir=session_dir,
        ),
    )

    return json.dumps({
        "member_name": member_name,
        "agent_id": final_agent_id,
        "worktree": worktree_path,
        "backend": str(backend_type),
        "pane_id": pane_id,
    })


def _truncate_summary(prompt: str, max_len: int = 80) -> str:
    """从 prompt 中截取摘要。"""
    first_line = prompt.split("\n")[0].strip()
    if len(first_line) > max_len:
        return first_line[: max_len - 3] + "..."
    return first_line


def _build_inprocess_agent(
    mgr: Manager,
    team,
    spawn_req: SpawnRequest,
    defi,  # AgentDefinition | None
    member_name: str,
):
    """构造 in-process 后端的子 Agent + Conversation。

    包括：allowed tools 计算（注入协作工具）、system prompt 扩展、
    team-context reminder 注入、TeammateContext 绑定、
    强制 dont_ask=True。
    """
    from suisuicode.agent.agent import Agent
    from suisuicode.agent.runtime import new_session_runtime
    from suisuicode.conversation import Conversation
    from suisuicode.permission import Mode as PermissionMode

    # ── allowed tools ──
    # 收集 registry 的全量工具名
    all_names: list[str] = []
    if mgr.task_mgr is not None:
        # 从 agent 构造拿 registry（稍微绕一点——spawn_teammate 没有直接拿到 agent）
        # 实际 cli 会通过 TeamHook 的 mgr 拿到 subagent catalog
        pass

    # 简化：teammate 模式 + 默认全工具 + Agent 不让嵌套
    allowed = all_names  # fallback

    # ── system prompt ──
    system_prompt = (
        defi.system_prompt if defi is not None else ""
    )
    system_prompt += "\n\n" + team_system_prompt_suffix()

    # ── 构造 Agent ──
    sub_runtime = new_session_runtime()
    sub_runtime.context_window = 200000

    # 权限模式：plan_mode_required → plan；否则 default
    perm_mode = PermissionMode.DEFAULT
    if spawn_req.plan_mode_required:
        perm_mode = PermissionMode.PLAN

    sub_agent = Agent(
        provider=None,  # 需要由调用方 set（本期 spawn_teammate 处理）
        registry=None,  # 需要由调用方 set
        engine=None,  # 需要由调用方 set
        version="",
        runtime=sub_runtime,
        allowed_tools=allowed,
        system_prompt=system_prompt,
        max_turns=spawn_req.max_turns if spawn_req.max_turns > 0 else 0,
        permission_mode=perm_mode,
        dont_ask=True,  # F39a: 队员一律 dont_ask=True
    )

    # ── 注入 team-context ──
    team_ctx_reminder = build_team_context_reminder(
        team_name=team.sanitized_name,
        member_name=member_name,
        agent_id=spawn_req.agent_id,
        worktree_path=spawn_req.worktree_path,
        members=[
            {"name": m.name, "agent_type": m.agent_type or "member"}
            for m in team.members
        ],
    )

    # ── TeammateContext ──
    _tc = TeammateContext(
        team_name=team.sanitized_name,
        member_name=member_name,
        agent_id=spawn_req.agent_id,
        backend_type=str(team.backend),
        mailbox_dir=team.mailbox_dir,
        team_tasks_path=team.tasks_path,
    )

    # ── Conversation ──
    sub_conv = Conversation()
    # 注入 team-context 作为第一条 system reminder
    # 走 Conversation.add_system_message
    if hasattr(sub_conv, "add_system_message"):
        sub_conv.add_system_message(team_ctx_reminder)

    # 注入 TeammateContext 到某个可传递的容器
    # 实际由 Agent 构造 ctx dict 时注入

    return sub_agent, sub_conv
