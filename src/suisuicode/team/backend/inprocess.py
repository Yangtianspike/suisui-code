"""in-process 后端 — 同进程 asyncio task 运行队员。"""

from __future__ import annotations


from suisuicode.team.backend import SpawnRequest
from suisuicode.team.types import BackendType


class InProcessBackend:
    """同进程后端：通过 task.Manager.launch 在事件循环中起 asyncio task。"""

    def __init__(self, task_mgr=None) -> None:
        self._task_mgr = task_mgr

    def type(self) -> BackendType:
        return BackendType.IN_PROCESS

    async def spawn(self, req: SpawnRequest) -> tuple[str, str]:
        """在事件循环中启动队员 Agent。"""
        if self._task_mgr is None:
            raise RuntimeError(
                "InProcessBackend 需要 task_mgr 依赖"
            )

        sub_agent = req.sub_agent
        conv = req.conv

        if sub_agent is None:
            raise RuntimeError(
                "InProcessBackend.spawn: req.sub_agent 不能为 None"
            )

        task_id = await self._task_mgr.launch(
            sub_agent,
            conv,
            req.member_name,
            req.initial_prompt,
        )

        return ("", task_id)

    async def wake(self, pane_id: str, agent_id: str) -> None:
        """in-process 不需要唤醒（同进程，下一轮 Loop 自然读邮箱）。"""
        return

    async def kill(self, pane_id: str, agent_id: str) -> None:
        """Cancel 对应 asyncio task。"""
        if self._task_mgr is not None:
            try:
                await self._task_mgr.stop(agent_id)
            except Exception:
                pass
