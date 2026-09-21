"""tmux 后端 — 在 tmux 会话内 split pane 运行队员。"""

from __future__ import annotations

import asyncio
import os
import shlex

from suisuicode.team.backend import SpawnRequest
from suisuicode.team.types import BackendType


class TmuxBackend:
    """tmux pane 后端。"""

    def type(self) -> BackendType:
        return BackendType.TMUX

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """在 tmux 中 split 新 pane 运行 suisuicode 子进程。

        若当前在 tmux 会话内，走 split-window。
        若当前不在 tmux 会话内但 tmux 二进制可用，走 new-session -d。
        """
        cmd = _build_member_cmd(req)

        if os.environ.get("TMUX"):
            # 当前在 tmux 内：横向 split
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "split-window",
                "-h",
                "-P",
                "-F",
                "#{pane_id}",
                "--",
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            # 外部新 session（detached）
            proc = await asyncio.create_subprocess_exec(
                "tmux",
                "new-session",
                "-d",
                "-s",
                f"suisuicode-team-{req.team_name}-{req.member_name}",
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode() if stderr else ""
            raise RuntimeError(
                f"tmux spawn 失败 (exit {proc.returncode}): {err_msg}"
            )

        pane_id = stdout.decode().strip()
        if not pane_id:
            # new-session -d 可能不输出 pane_id，用 session 名代替
            pane_id = f"session:suisuicode-team-{req.team_name}-{req.member_name}"

        return (pane_id, req.agent_id)

    async def wake(self, pane_id: str, agent_id: str) -> None:
        """通过 send-keys 回车唤醒 pane 内子进程的 stdin reader。"""
        if pane_id.startswith("session:"):
            session = pane_id.split(":", 1)[1]
            await asyncio.create_subprocess_exec(
                "tmux", "send-keys", "-t", session, "C-m",
            )
        else:
            await asyncio.create_subprocess_exec(
                "tmux", "send-keys", "-t", pane_id, "Enter",
            )

    async def kill(self, pane_id: str, agent_id: str) -> None:
        """Kill pane，忽略 pane 不存在错误。"""
        try:
            if pane_id.startswith("session:"):
                session = pane_id.split(":", 1)[1]
                await asyncio.create_subprocess_exec(
                    "tmux", "kill-session", "-t", session,
                )
            else:
                await asyncio.create_subprocess_exec(
                    "tmux", "kill-pane", "-t", pane_id,
                )
        except Exception:
            pass  # pane 可能已被手动关闭


def _build_member_cmd(req: SpawnRequest) -> str:
    """构造 suisuicode 子进程命令行。

    注意：initial_prompt 不通过命令行传递（由 spawn_teammate 预写入
    mailbox），命令行只传标识信息。
    """
    parts = [
        "python",
        "-m",
        "suisuicode",
        "--team-member",
        "--team",
        shlex.quote(req.team_name),
        "--member",
        shlex.quote(req.member_name),
        "--agent-id",
        shlex.quote(req.agent_id),
        "--session-dir",
        shlex.quote(req.session_dir),
        "--worktree",
        shlex.quote(req.worktree_path),
    ]

    if req.agent_type:
        parts.extend(["--agent-type", shlex.quote(req.agent_type)])
    if req.model:
        parts.extend(["--model", shlex.quote(req.model)])
    if req.plan_mode_required:
        parts.append("--plan-mode")

    return " ".join(parts)
