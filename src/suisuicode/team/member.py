"""--team-member 自治循环（F19a-F19c）。

Pane 后端（tmux/iterm2）spawn 的子进程不启动 TUI，
而是运行这个自治协程：读 mailbox → 跑任务 → 通知 Lead idle → 等新消息。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from suisuicode.agent.team_hook import (
    TeammateContext,
)
from suisuicode.team.mailbox import Box, Message
from suisuicode.team.mailbox.message import MessageType


async def run_team_member(
    team_mgr,
    team_name: str,
    member_name: str,
    agent_id: str,
    session_dir: str,
    worktree: str,
    agent_type: str = "",
    model: str = "",
    plan_mode: bool = False,
    sub_agent=None,
    sub_conv=None,
    provider=None,
    registry=None,
    engine=None,
    hook_engine=None,
) -> None:
    """自治协程主函数 — Pane 后端子进程入口。

    不构造 Textual App，只跑纯 asyncio 循环。
    """
    # ── 1. chdir 到 worktree ──
    if worktree:
        try:
            os.chdir(worktree)
        except OSError as e:
            print(f"[team-member] chdir 失败: {e}", file=sys.stderr)

    # ── 2. 取 Team ──
    team = team_mgr.get(team_name)
    if team is None:
        print(f"[team-member] Team 不存在: {team_name!r}", file=sys.stderr)
        return

    # ── 3. 构造 mailbox client ──
    box = Box(team.mailbox_dir) if team.mailbox_dir else None

    # ── 4. 构造 TeammateContext ──
    tc = TeammateContext(
        team_name=team.sanitized_name,
        member_name=member_name,
        agent_id=agent_id,
        backend_type=str(team.backend),
        mailbox_dir=team.mailbox_dir,
        team_tasks_path=team.tasks_path,
    )

    # ── 5. 注入 mailbox 闭包 ──
    if box is not None:
        tc.read_unread = lambda: box.read_unread(agent_id)
        tc.mark_read = lambda indices: box.mark_read(agent_id, indices)

    # ── 6. stdin reader（wake 信号）───
    wake_event = asyncio.Event()

    async def stdin_reader() -> None:
        """读取 stdin，任何输入都视为 wake 信号。"""
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        while True:
            try:
                line = await reader.readline()
                if not line:
                    break
                wake_event.set()
            except Exception:
                break

    stdin_task = asyncio.create_task(stdin_reader())

    # ── 7. 打印启动信息 ──
    print(
        f"[team-member] {member_name} · team={team_name} · "
        f"agent={agent_id} · cwd={os.getcwd()}",
        flush=True,
    )

    # ── 8. 主循环 ──
    try:
        # 若 sub_agent 已预构造（in-process），使用它
        # Pane 后端需要自己构造 sub_agent
        agent = sub_agent
        conv = sub_conv

        while True:
            # 检查 mailbox
            if box is not None and agent is not None:
                indices, unread = await box.read_unread(agent_id)

                if unread:
                    await box.mark_read(agent_id, indices)
                    # 处理消息
                    for msg in unread:
                        if msg.type == MessageType.TEXT:
                            # 作为新任务跑
                            task_text = (
                                f"[新任务来自 {msg.from_}]: {msg.content}"
                            )
                            if conv is not None and agent is not None:
                                conv.add_user(task_text)
                                print(
                                    f"  [task] {msg.summary or task_text[:80]}",
                                    flush=True,
                                )
                                try:
                                    events = asyncio.Queue(maxsize=32)
                                    result = await agent.run_to_completion(
                                        conv, "", events
                                    )
                                    print(f"  [done] {result[:200]}", flush=True)
                                except Exception as e:
                                    print(
                                        f"  [error] {e}", file=sys.stderr
                                    )

                        elif msg.type == MessageType.PLAN_APPROVAL_RESPONSE:
                            payload = msg.payload or {}
                            if payload.get("approve"):
                                print("  [plan] Plan 已获批准", flush=True)
                                if agent is not None:
                                    from suisuicode.permission import (
                                        Mode as PermissionMode,
                                    )
                                    if hasattr(agent, "set_permission_mode"):
                                        agent.set_permission_mode(
                                            PermissionMode.DEFAULT
                                        )
                            else:
                                feedback = payload.get("feedback", "")
                                print(
                                    f"  [plan] Plan 被驳回: {feedback}",
                                    flush=True,
                                )

                        elif msg.type == MessageType.SHUTDOWN_REQUEST:
                            print(
                                "  [shutdown] 收到退出请求",
                                flush=True,
                            )
                            # 发送 shutdown_response 后退出
                            if box is not None:
                                await box.write(
                                    team.lead_agent_id,
                                    Message(
                                        from_=member_name,
                                        to=team.lead_agent_id,
                                        type=MessageType.SHUTDOWN_RESPONSE,
                                        summary=f"{member_name} shutdown ok",
                                        content="优雅退出",
                                    ),
                                )
                            break

                # 无未读消息 → wait
                if not unread:
                    try:
                        await asyncio.wait_for(
                            wake_event.wait(), timeout=2.0
                        )
                        wake_event.clear()
                    except asyncio.TimeoutError:
                        pass  # 兜底轮询
            else:
                # 无 agent / 无 mailbox → 简单循环等 wake
                try:
                    await asyncio.wait_for(
                        wake_event.wait(), timeout=2.0
                    )
                    wake_event.clear()
                except asyncio.TimeoutError:
                    pass

            # 检测 mailbox 目录是否被删除
            if team.mailbox_dir and not Path(team.mailbox_dir).exists():
                print("[team-member] Team 已删除，退出", flush=True)
                break

    except asyncio.CancelledError:
        pass
    except Exception as e:
        print(f"[team-member] 异常: {e}", file=sys.stderr)
    finally:
        stdin_task.cancel()
        try:
            await stdin_task
        except asyncio.CancelledError:
            pass

        # 通知 Lead idle
        if box is not None:
            try:
                await box.write(
                    team.lead_agent_id,
                    Message(
                        from_=member_name,
                        to=team.lead_agent_id,
                        type=MessageType.TEXT,
                        summary=f"{member_name} idle",
                        content=f"agent {agent_id} finished work, available for new tasks",
                    ),
                )
            except Exception:
                pass

        # 标记自己 inactive
        try:
            await team_mgr.set_member_active(
                team.sanitized_name, member_name, False
            )
        except Exception:
            pass

        print(f"[team-member] {member_name} 已退出", flush=True)
