"""Event adapter: 将 suisuicode 的 Agent.run() Event 流转为 mewcode 风格的类型化事件。"""

from __future__ import annotations

import asyncio
import json
import time as _time
from collections.abc import AsyncIterator
from dataclasses import dataclass

from suisuicode.agent.agent import Event, Phase


# ── mewcode 风格事件类型 ──────────────────────────


@dataclass
class AppStreamText:
    text: str


@dataclass
class AppToolUseEvent:
    tool_name: str
    tool_id: str
    arguments: dict


@dataclass
class AppToolResultEvent:
    tool_name: str
    tool_id: str
    output: str
    is_error: bool
    elapsed: float


@dataclass
class AppPermissionRequest:
    tool_name: str
    description: str
    args: str
    respond: asyncio.Future  # Future[Outcome]


@dataclass
class AppTurnComplete:
    turn: int


@dataclass
class AppLoopComplete:
    total_turns: int


@dataclass
class AppErrorEvent:
    message: str


@dataclass
class AppCompactNotification:
    message: str
    boundary: object | None = None


@dataclass
class AppUsageEvent:
    input_tokens: int
    output_tokens: int


@dataclass
class AppRetryEvent:
    reason: str


@dataclass
class AppHookEvent:
    hook_id: str
    success: bool
    output: str


# ── 适配器 ────────────────────────────────────────


async def adapt_events(agent_run: AsyncIterator[Event]) -> AsyncIterator[object]:
    """将 suisuicode 的 Event 流转为 mewcode 风格的类型化事件。

    ToolEvent 的 START/END 配对通过内部 dict 维护：
    - START → AppToolUseEvent（生成 tool_id）
    - END → AppToolResultEvent（匹配 tool_id，计算 elapsed）
    """
    _pending: dict[str, tuple[str, float]] = {}  # tool_id → (name, start_time)
    _tool_counter: int = 0
    _last_iter: int = 1  # 跳过 iter=1 的初始事件，避免首轮创建空行

    async for ev in agent_run:
        # 文本增量
        if ev.text:
            yield AppStreamText(text=ev.text)

        # 工具调用 START
        if ev.tool is not None and ev.tool.phase == Phase.START:
            _tool_counter += 1
            tool_id = f"tool_{_tool_counter}"
            _pending[tool_id] = (ev.tool.name, _time.monotonic())
            args_dict = _parse_args(ev.tool.args)
            yield AppToolUseEvent(
                tool_name=ev.tool.name,
                tool_id=tool_id,
                arguments=args_dict,
            )

        # 工具调用 END
        if ev.tool is not None and ev.tool.phase == Phase.END:
            # 找到最近的匹配 pending tool
            tool_id = None
            start_time = 0.0
            for tid, (tname, tstart) in list(_pending.items()):
                if tname == ev.tool.name:
                    tool_id = tid
                    start_time = tstart
                    del _pending[tid]
                    break
            elapsed = _time.monotonic() - start_time if tool_id else 0.0
            yield AppToolResultEvent(
                tool_name=ev.tool.name,
                tool_id=tool_id or "",
                output=ev.tool.result,
                is_error=ev.tool.is_error,
                elapsed=elapsed,
            )

        # Token 用量
        if ev.usage is not None:
            yield AppUsageEvent(
                input_tokens=ev.usage.input,
                output_tokens=ev.usage.output,
            )

        # 人在回路（权限确认）
        if ev.approval is not None:
            yield AppPermissionRequest(
                tool_name=ev.approval.name,
                description=ev.approval.reason,
                args=ev.approval.args,
                respond=ev.approval.respond,
            )

        # 系统通知
        if ev.notice:
            # 区分取消/超限等特殊通知
            from suisuicode.agent.agent import (
                NOTICE_CANCELLED,
            )
            if ev.notice == NOTICE_CANCELLED:
                yield AppRetryEvent(reason="cancelled")
            else:
                yield AppRetryEvent(reason=ev.notice)

        # 压缩通知
        if ev.compact is not None:
            from suisuicode.tui.commands import format_compact_notice
            yield AppCompactNotification(
                message=format_compact_notice(ev.compact),
            )

        # 错误
        if ev.err is not None:
            yield AppErrorEvent(message=str(ev.err))

        # 轮次变更
        if ev.iter > 0 and ev.iter != _last_iter:
            _last_iter = ev.iter
            yield AppTurnComplete(turn=ev.iter)

        # 完成
        if ev.done:
            yield AppLoopComplete(total_turns=ev.iter)
            return


def _parse_args(input_str: str) -> dict:
    """安全解析工具参数 JSON 字符串。"""
    if not input_str or input_str == "{}":
        return {}
    try:
        return json.loads(input_str)
    except json.JSONDecodeError:
        return {}
