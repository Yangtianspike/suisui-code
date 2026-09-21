"""launch_fork：公共 Fork 启动函数（供 skill_fork 与 AgentTool 共用）。

T31 实现。将 Agent 构造 wiring 抽到一处，skill fork 与 Agent 工具都调此函数。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from suisuicode.agent.agent import Agent
from suisuicode.agent.runtime import new_session_runtime
from suisuicode.conversation import Conversation

if TYPE_CHECKING:
    from suisuicode.permission.engine import Engine
    from suisuicode.tool import Registry


@dataclass
class ForkLaunchOpts:
    """Fork 启动参数（spec F22-F25, plan 模块 D）。"""

    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""
    conv: Conversation | None = None  # 已装填的子对话；None 时从空开始
    system_prompt: str = ""  # 空串 = 走继承
    background: bool = False  # True = 使用 task_mgr.launch 后台
    events_sink: "asyncio.Queue | None" = None
    task_text: str = ""  # 用户任务文本
    name: str = ""  # 子 Agent 名称


async def launch_fork(
    opts: ForkLaunchOpts,
    *,
    provider,
    registry: Registry,
    engine: Engine,
    version: str = "",
    hook_engine=None,
    task_mgr=None,
) -> str:
    """用给定参数启动一个 Fork 子 Agent 并返回 final_text（spec F22-F25, G3 Fork 式）。

    与 AgentTool.execute 前台路径相同的 wiring，只是不读 catalog Definition。
    供 skill_fork 和 Agent 工具 Fork 路径调用。

    Returns:
        子 Agent 的 final_text。
    """
    sub_runtime = new_session_runtime()

    sub_agent = Agent(
        provider=provider,
        registry=registry,
        engine=engine,
        version=version,
        runtime=sub_runtime,
        allowed_tools=opts.allowed_tools if opts.allowed_tools else None,
        system_prompt=opts.system_prompt if opts.system_prompt else None,
        hook_engine=hook_engine,
    )

    conv = opts.conv if opts.conv is not None else Conversation()

    import asyncio

    events: asyncio.Queue | None = (
        asyncio.Queue(maxsize=32) if opts.events_sink is not None else None
    )

    if opts.background and task_mgr is not None:
        task_id = await task_mgr.launch(sub_agent, conv, opts.name, opts.task_text)
        return task_id

    final_text = await sub_agent.run_to_completion(conv, opts.task_text, events)
    return final_text
