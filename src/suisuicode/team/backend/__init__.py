"""Team 执行后端 — Backend Protocol + SpawnRequest + 工厂函数。

三种后端 tmux / iterm2 / in-process 通过统一 Protocol 屏蔽 spawn 差异。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from suisuicode.team.types import BackendType


class Backend(Protocol):
    """队员执行后端抽象。"""

    def type(self) -> BackendType: ...

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """在后端启动新队员，返回 (pane_id, agent_id)。

        Pane 后端：pane_id 为 tmux pane / iterm2 split id。
        in-process 后端：pane_id 为空，agent_id 为 task_id。
        """
        ...

    async def wake(self, pane_id: str, agent_id: str) -> None:
        """消息到达时唤醒目标 pane/in-process task。"""
        ...

    async def kill(self, pane_id: str, agent_id: str) -> None:
        """终止 pane 或 cancel asyncio task。"""
        ...


@dataclass
class SpawnRequest:
    """spawn 所需的全部参数。

    其中 sub_agent / conv / task_mgr 为 in-process 后端专用（Any 类型
    避免 backend 包反向依赖 agent 包）。
    """

    team_name: str
    member_name: str
    agent_id: str
    worktree_path: str
    session_dir: str
    agent_type: str = ""
    model: str = ""
    initial_prompt: str = ""
    plan_mode_required: bool = False

    # in-process 专用
    sub_agent: Any = None  # agent.Agent
    conv: Any = None  # conversation.Conversation
    task_mgr: Any = None  # task.Manager
    allowed_tools: list[str] | None = None
    system_prompt: str = ""
    max_turns: int = 0


def new_backend(t: BackendType, **deps) -> Backend:
    """工厂：按 BackendType 构造对应的 Backend 实例。"""
    if t == BackendType.TMUX:
        from suisuicode.team.backend.tmux import TmuxBackend

        return TmuxBackend()
    elif t == BackendType.ITERM2:
        from suisuicode.team.backend.iterm2 import Iterm2Backend

        return Iterm2Backend()
    elif t == BackendType.IN_PROCESS:
        from suisuicode.team.backend.inprocess import InProcessBackend

        task_mgr = deps.get("task_mgr")
        return InProcessBackend(task_mgr=task_mgr)
    else:
        raise ValueError(f"未知 BackendType: {t!r}")
