"""iTerm2 后端 — 通过 it2 CLI 管理 pane。"""

from __future__ import annotations

import asyncio

from suisuicode.team.backend import SpawnRequest
from suisuicode.team.types import BackendType


class Iterm2Backend:
    """iTerm2 pane 后端。"""

    def type(self) -> BackendType:
        return BackendType.ITERM2

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """通过 it2 split 创建新 pane 并运行 suisuicode 子进程。"""
        cmd = _build_member_cmd_iterm(req)

        proc = await asyncio.create_subprocess_exec(
            "it2",
            "split",
            "--new-pane",
            "--command",
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode() if stderr else ""
            raise RuntimeError(
                f"it2 spawn 失败 (exit {proc.returncode}): {err_msg}"
            )

        pane_id = stdout.decode().strip()
        return (pane_id, req.agent_id)

    async def wake(self, pane_id: str, agent_id: str) -> None:
        """通过 it2 send-text 唤醒 pane。"""
        await asyncio.create_subprocess_exec(
            "it2", "send-text", "--pane", pane_id, "",
        )

    async def kill(self, pane_id: str, agent_id: str) -> None:
        """关闭 pane。"""
        try:
            await asyncio.create_subprocess_exec(
                "it2", "close-pane", "--pane", pane_id,
            )
        except Exception:
            pass


def _build_member_cmd_iterm(req: SpawnRequest) -> str:
    """构造 suisuicode 子进程命令行（iTerm2 版，与 tmux 同构）。"""
    # 与 tmux 的 _build_member_cmd 格式一致
    from suisuicode.team.backend.tmux import _build_member_cmd

    return _build_member_cmd(req)
