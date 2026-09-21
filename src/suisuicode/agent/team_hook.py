"""Team Hook — agent 包与 team 包的桥梁。

通过 Protocol 解耦，避免循环依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol


class TeamHook(Protocol):
    """Agent 工具委托给 Team Manager 处理 team_name 分支的接口。"""

    async def spawn_teammate(self, req: TeamSpawnRequest) -> str:
        """启动 Team 队员，返回 final_text（task_id JSON 描述）。"""
        ...

    def is_teammate_context(self, ctx: dict) -> tuple[str, str, bool]:
        """判断当前 ctx 是否在某队员的执行上下文中。

        返回 (team_name, member_name, is_in_team)。
        """
        ...


class MailboxReader(Protocol):
    """邮箱读取抽象——避免 agent 包直接依赖 mailbox 包。"""

    async def read_unread(
        self, agent_id: str
    ) -> tuple[list[int], list[Any]]: ...

    async def mark_read(
        self, agent_id: str, indices: list[int]
    ) -> None: ...


@dataclass
class TeamSpawnRequest:
    """Team spawn 所需参数，从 Agent 工具参数映射过来。"""

    team_name: str
    member_name: str
    prompt: str = ""
    description: str = ""
    subagent_type: str = ""
    model: str = ""
    plan_mode_required: bool = False
    # Fork 队员（留空 subagent_type 且 flag 开启）
    is_fork: bool = False


@dataclass
class TeammateContext:
    """队员的执行上下文，在 spawn 时注入到子 Agent。

    通过 contextvars 或 dict 传递。
    """

    team_name: str
    member_name: str
    agent_id: str
    backend_type: str = ""
    mailbox_dir: str = ""
    team_tasks_path: str = ""

    # 邮箱读写闭包（由 team 包在 spawn 时注入）
    read_unread: Callable[[], Awaitable[tuple[list[int], list[Any]]]] | None = None
    mark_read: Callable[[list[int]], Awaitable[None]] | None = None
    send_message_wake: Callable[[str, str], Awaitable[None]] | None = None


@dataclass
class IncomingMessage:
    """agent 包的轻量消息类型（独立于 mailbox.Message，避免循环依赖）。"""

    from_: str
    to: str
    type: str = "text"
    summary: str = ""
    content: str = ""
    payload: dict[str, Any] | None = None
    timestamp: int = 0


# contextvars 键名
WITH_TEAMMATE_KEY = "teammate"


def with_teammate_context(
    ctx: dict, tc: TeammateContext
) -> dict:
    """把 TeammateContext 注入到 ctx dict。"""
    ctx[WITH_TEAMMATE_KEY] = tc
    return ctx


def teammate_context_from_ctx(
    ctx: dict,
) -> TeammateContext | None:
    """从 ctx dict 中取出 TeammateContext。"""
    return ctx.get(WITH_TEAMMATE_KEY)
