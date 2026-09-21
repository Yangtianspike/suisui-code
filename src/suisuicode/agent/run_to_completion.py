"""Agent.run_to_completion：子 Agent "跑到底" 循环（spec F9）。

与主 ``run`` 共享 ``_stream_once`` / ``_execute_batched``，但：
- 不通过队列返回事件（内部消费），最终返回 final_text
- max_turns 由 self.max_turns 决定（若 0 则用 MAX_ITERATIONS）
- 不触发 memory update / compact reminder 等主对话专属逻辑
- 接受可选 events 队列，转发内部事件供 TaskManager / TUI 消费
"""

from __future__ import annotations

import asyncio
import logging

from suisuicode.agent.agent import (
    Agent,
    Event,
    MAX_ITERATIONS,
    MAX_UNKNOWN_RUN,
    NOTICE_CANCELLED,
    NOTICE_MAX_ITER,
    NOTICE_UNKNOWN_TOOLS,
    Usage,
    _all_unknown,
    _ensure_assistant_tail,
    _ensure_final,
)
from suisuicode.compact.token import estimate_tokens, usage_anchor
from suisuicode.conversation import Conversation
from suisuicode.hook import Event as HookEvent
from suisuicode.llm import PromptTooLongError
from suisuicode.permission import Mode
from suisuicode.prompt import (
    build_system_prompt,
    gather_environment,
    plan_reminder,
    render_active_skills_block,
)

logger = logging.getLogger(__name__)

PLAN_REMINDER_INTERVAL: int = 4


class MaxTurnsReached(Exception):
    """子 Agent 跑满 max_turns 时抛出。"""

    def __init__(self, final_text: str) -> None:
        super().__init__(final_text)
        self.final_text = final_text


# ── 挂到 Agent 上的方法 ──────────────────────────────────


async def run_to_completion(
    self: Agent,
    conv: Conversation,
    task: str,
    events: asyncio.Queue | None = None,
) -> str:
    """执行子 Agent 的 "跑到底" 循环。

    Args:
        conv: 子 Agent 对话（可能已预装填消息）
        task: 用户任务文本（空串时不追加新 user 消息）
        events: 可选的外部事件队列，用于 TaskManager / TUI 消费

    Returns:
        子 Agent 最后一条 assistant 文本。

    Raises:
        MaxTurnsReached: 跑满 max_turns（final_text 仍可通过 exc.final_text 获取）
        asyncio.CancelledError: 被外部取消（透传）
    """
    # 追加任务
    if task:
        conv.add_user(task)

    # 计算参数
    turns = self.max_turns if self.max_turns > 0 else MAX_ITERATIONS
    mode = self.permission_mode or Mode.DEFAULT

    # 构造环境与系统提示
    env = gather_environment(getattr(self, "_version", ""),
                             self._provider.model if hasattr(self, '_provider') else "")
    env_text = env.render()

    active_block = render_active_skills_block(
        self.runtime.active_skills.to_prompt_entries()
    )
    if active_block:
        env_text = env_text + "\n\n" + active_block

    # 子 Agent 使用自定义 system_prompt 或默认
    if self.system_prompt:
        sys = self.system_prompt
    else:
        sys = build_system_prompt(
            getattr(self, "_instruction_text", ""),
            getattr(self, "_memory_text", ""),
            getattr(self, "_skills_catalog_text", ""),
        )

    unknown_run = 0

    for it in range(1, turns + 1):
        # 按 mode 选 defs
        if mode == Mode.PLAN:
            defs = self._registry.read_only_definitions()
        else:
            defs = self._registry.definitions()

        # ch13: 应用 allowed_tools 白名单
        if self._allowed_tools is not None:
            allowed_set = set(self._allowed_tools)
            defs = [d for d in defs if d.name in allowed_set]

        # reminder
        reminder = ""
        if mode == Mode.PLAN:
            full = it == 1 or (it - 1) % PLAN_REMINDER_INTERVAL == 0
            reminder = plan_reminder(full)

        # hook reminders
        hook_reminders = self.runtime.take_reminders()
        if hook_reminders:
            reminder = (reminder + "\n\n" + "\n\n".join(hook_reminders)).strip()

        cancel_evt = asyncio.Event()

        # _stream_once
        try:
            text, calls, usage, err, text_events = await self._stream_once(
                conv, defs, sys, env_text, reminder, cancel_evt,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("run_to_completion stream error: %s", e)
            return _ensure_final("")

        # 转发 text 事件
        if events is not None:
            for te in text_events:
                _put_event(events, te)

        # 更新 usage anchor
        if usage is not None:
            self.runtime.usage_anchor = usage_anchor(usage)
            self.runtime.anchor_msg_len = conv.length()

        # 错误处理
        if err is not None:
            if events is not None:
                _put_event(events, Event(err=err))
            if isinstance(err, PromptTooLongError):
                # 尝试轻量 emergency offload
                try:
                    from suisuicode.compact import ManageInput, TriggerKind, manage_context
                    in_ = ManageInput(
                        conv=conv,
                        provider=self._provider,
                        model=self._provider.model,
                        context_window=self.runtime.context_window,
                        tool_defs=defs,
                        replacement=self.runtime.replacement,
                        recovery=self.runtime.recovery,
                        auto_tracking=self.runtime.auto_tracking,
                        session=self.runtime.session,
                        usage_anchor=self.runtime.usage_anchor,
                        anchor_msg_len=self.runtime.anchor_msg_len,
                        estimated_token=estimate_tokens(
                            self.runtime.usage_anchor,
                            conv.messages(),
                            self.runtime.anchor_msg_len,
                        ),
                        trigger=TriggerKind.EMERGENCY,
                    )
                    await manage_context(in_)
                    # 重试一次
                    text, calls, usage, err, text_events = await self._stream_once(
                        conv, defs, sys, env_text, reminder, cancel_evt,
                    )
                    for te in text_events:
                        if events is not None:
                            _put_event(events, te)
                    if err is not None:
                        msg = _ensure_final(text)
                        return msg
                except Exception:
                    return _ensure_final(text)
            else:
                msg = _ensure_final(text)
                return msg

        # usage 事件
        if usage is not None and events is not None:
            _put_event(events, Event(usage=Usage(
                input=usage.input_tokens,
                output=usage.output_tokens,
                cache_write=usage.cache_write,
                cache_read=usage.cache_read,
            )))

        # 无工具调用 → 自然完成
        if not calls:
            final = _ensure_final(text)
            conv.add_assistant(final)
            await self._dispatch_hook(HookEvent.STOP, {"event": "Stop", "iter": it})
            return final

        conv.add_assistant_with_tool_calls(text, calls)

        if _all_unknown(calls, self._registry):
            unknown_run += 1
        else:
            unknown_run = 0

        # 工具执行
        queue: asyncio.Queue[Event | None] = asyncio.Queue()

        async def _emit(ev: Event) -> None:
            await queue.put(ev)
            if events is not None:
                _put_event(events, ev)

        results, completed = await self._execute_batched(
            calls, mode, cancel_evt, _emit,
        )
        conv.add_tool_results(results)

        if not completed:
            _ensure_assistant_tail(conv, NOTICE_CANCELLED)
            return NOTICE_CANCELLED

        if unknown_run >= MAX_UNKNOWN_RUN:
            _ensure_assistant_tail(conv, NOTICE_UNKNOWN_TOOLS)
            return NOTICE_UNKNOWN_TOOLS

    # 触达 max_turns
    last = ""
    msgs = conv.messages()
    for m in reversed(msgs):
        if m.role == "assistant":
            last = m.content
            break
    final = _ensure_final(last)
    _ensure_assistant_tail(conv, NOTICE_MAX_ITER)
    raise MaxTurnsReached(final)


def _put_event(q: asyncio.Queue, ev: Event) -> None:
    """安全放入事件队列（忽略满队列）。"""
    try:
        q.put_nowait(ev)
    except asyncio.QueueFull:
        pass


# ── Monkey-patch Agent ────────────────────────────────────

def _install() -> None:
    """将 run_to_completion 挂到 Agent 类上。"""
    Agent.run_to_completion = run_to_completion


_install()
