"""Agent：持有 provider、注册中心与权限引擎，执行 ReAct 多轮循环（ch08 集成 compact）。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

from suisuicode.agent.event import CompactEvent, CompactPhase
from suisuicode.agent.runtime import SessionRuntime
from suisuicode.hook import DispatchResult, Event as HookEvent, Engine as HookEngine
from suisuicode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    ManageInput,
    RecoveryState,
    TriggerKind,
    manage_context,
    new_session_context,
)
from suisuicode.compact.const import (
    AUTO_SAFETY_MARGIN,
    MANUAL_SAFETY_MARGIN,
    SUMMARY_RESERVE,
)
from suisuicode.compact.token import estimate_tokens, usage_anchor
from suisuicode.conversation import Conversation
from suisuicode.llm import (
    PromptTooLongError,
    Provider,
    Request,
    ToolCall,
    ToolResult,
)
from suisuicode.permission import Decision, Mode, Outcome
from suisuicode.permission.engine import Engine, check as _perm_check
from suisuicode.permission.persist import persist_local_allow
from suisuicode.prompt import (
    build_system_prompt,
    gather_environment,
    plan_reminder,
    render_active_skills_block,
)
from suisuicode.tool import DEFAULT_TIMEOUT, Registry

if TYPE_CHECKING:
    from suisuicode.agent.permission_upgrade import ApprovalUpgrader

logger = logging.getLogger(__name__)


# ── 事件流类型 ───────────────────────────────────────


class Phase(IntEnum):
    START = 0
    END = 1


@dataclass
class ToolEvent:
    name: str
    args: str = ""
    phase: Phase = Phase.START
    result: str = ""
    is_error: bool = False


@dataclass
class Usage:
    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0


@dataclass
class ApprovalRequest:
    name: str
    args: str
    reason: str
    respond: asyncio.Future[Outcome]


@dataclass
class Event:
    text: str = ""
    tool: ToolEvent | None = None
    usage: Usage | None = None
    iter: int = 0
    notice: str = ""
    done: bool = False
    err: Exception | None = None
    approval: ApprovalRequest | None = None
    compact: CompactEvent | None = None  # ch08 新增：压缩生命周期事件


# ── 常量 ──────────────────────────────────────────────

MAX_ITERATIONS: int = 25
MAX_UNKNOWN_RUN: int = 3
PLAN_REMINDER_INTERVAL: int = 4

NOTICE_MAX_ITER = "（已达最大迭代轮数 25，自动停止；可继续发消息推进。）"
NOTICE_UNKNOWN_TOOLS = "（连续多轮只请求到未注册的工具，自动停止。）"
NOTICE_STREAM_ERR = "（请求出错，本轮已中断。）"
NOTICE_CANCELLED = "（已取消。）"


# ── 辅助函数 ──────────────────────────────────────────


def _preview_args(input_str: str, max_len: int = 80) -> str:
    if not input_str or input_str == "{}":
        return ""
    try:
        data = json.loads(input_str)
    except json.JSONDecodeError:
        return input_str[:max_len]
    parts = []
    if "path" in data:
        parts.append(f"path={data['path']}")
    elif "pattern" in data:
        parts.append(f"pattern={data['pattern']}")
    elif "command" in data:
        cmd = data["command"]
        if len(cmd) > 40:
            cmd = cmd[:40] + "…"
        parts.append(f"cmd={cmd}")
    preview = ", ".join(parts)
    if len(preview) > max_len:
        preview = preview[:max_len] + "…"
    return preview


def _result_preview(result_str: str, max_lines: int = 8) -> str:
    lines = result_str.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + "\n…"
    return result_str


def _ensure_final(text: str) -> str:
    if text.strip():
        return text
    return "（已完成）"


def _all_unknown(calls: list[ToolCall], registry: Registry) -> bool:
    for tc in calls:
        if registry.get(tc.name) is not None:
            return False
    return True


def _ensure_assistant_tail(conv: Conversation, fallback: str) -> None:
    if conv.last_role() != "assistant":
        conv.add_assistant(fallback)


def _finish_cancelled(conv: Conversation) -> None:
    _ensure_assistant_tail(conv, NOTICE_CANCELLED)


def _parse_tool_input(tc_input: str | dict) -> dict:
    """将 ToolCall.input（JSON 字符串或 dict）统一转为 dict。

    Anthropic/OpenAI 的 ToolCall.input 存的是 JSON 字符串；
    ch12 PreToolUse hook 需要 dict 形式做 tool_input.path 取值。
    """
    if isinstance(tc_input, dict):
        return tc_input
    if isinstance(tc_input, str) and tc_input.strip():
        try:
            return json.loads(tc_input)
        except json.JSONDecodeError:
            return {}
    return {}


# ── Agent ─────────────────────────────────────────────


class Agent:
    """持有 provider、注册中心与权限引擎，执行 ReAct 多轮循环。"""

    def __init__(
        self,
        provider: Provider,
        registry: Registry,
        engine: Engine,
        version: str = "",
        *,
        runtime: SessionRuntime | None = None,
        memory_manager=None,  # memory.Manager | None
        instruction_text: str = "",
        memory_text: str = "",
        catalog=None,  # skills.Catalog | None
        hook_engine: HookEngine | None = None,  # ch12
        # ch13: SubAgent 扩展参数
        system_prompt: str | None = None,  # 覆盖默认系统提示
        max_turns: int = 0,  # 0 = 用全局 MAX_ITERATIONS
        permission_mode: "Mode | None" = None,  # None = 用 TUI 运行时模式
        dont_ask: bool = False,  # 子 Agent dontAsk 兜底
        approval_upgrader: "ApprovalUpgrader | None" = None,  # 审批升级回调
        allowed_tools: list[str] | None = None,  # 工具白名单（过滤后）
        provider_override: Provider | None = None,  # 覆盖默认 provider（model 切换）
    ) -> None:
        self._provider = provider_override if provider_override is not None else provider
        self._registry = registry
        self._engine = engine
        self._version = version
        self._run_lock = asyncio.Lock()
        self._mem_mgr = memory_manager
        self._instruction_text = instruction_text
        self._memory_text = memory_text
        self._catalog = catalog
        self._skills_catalog_text: str = ""
        self._hook_engine = hook_engine

        # ch13: SubAgent 扩展参数
        self.system_prompt = system_prompt
        self.max_turns = max_turns
        self.permission_mode = permission_mode
        self.dont_ask = dont_ask
        self.approval_upgrader = approval_upgrader
        self._allowed_tools = allowed_tools

        if runtime is None:
            runtime = SessionRuntime(
                replacement=ContentReplacementState(),
                recovery=RecoveryState(),
                auto_tracking=CompactCircuitBreaker(),
                session=new_session_context("."),
                context_window=200000,
            )
        self.runtime = runtime

    # ── Skill 管理 ────────────────────────────────────

    def activate_skill(self, name: str, body: str) -> None:
        """激活一个 Skill：将其 SOP 钉到 runtime 的 active_skills。"""
        self.runtime.active_skills.activate(name, body)

    def clear_active_skills(self) -> None:
        """清空全部已激活 Skill。"""
        self.runtime.active_skills.clear()

    def set_skill_catalog(self, catalog_text: str) -> None:
        """设置 Skill Catalog 提示文本（注入 system prompt）。"""
        self._skills_catalog_text = catalog_text

    @property
    def active_skill_names(self) -> list[str]:
        """当前已激活 Skill 名称列表。"""
        return self.runtime.active_skills.names()

    # ── ch12 Hook ─────────────────────────────────────

    async def _dispatch_hook(self, event: HookEvent, payload: dict) -> DispatchResult:
        """发送 hook 事件，收集 injected_prompts 到 runtime。

        若 hook_engine 为 None 则返回空 DispatchResult。
        """
        if self._hook_engine is None:
            return DispatchResult()
        result = await self._hook_engine.dispatch(event, payload)
        self.runtime.append_reminders(result.injected_prompts)
        return result

    def _build_reminder(self, mode: Mode, it: int) -> str:
        """构造 reminder 串：plan reminder + pending hook reminders。"""
        reminder = ""
        if mode == Mode.PLAN:
            full = it == 1 or (it - 1) % PLAN_REMINDER_INTERVAL == 0
            reminder = plan_reminder(full)
        hook_reminders = self.runtime.take_reminders()
        if hook_reminders:
            reminder = (reminder + "\n\n" + "\n\n".join(hook_reminders)).strip()
        return reminder

    # ── run（ReAct 主循环）────────────────────────────

    async def run(
        self,
        conv: Conversation,
        mode: Mode = Mode.DEFAULT,
        cancel: asyncio.Event | None = None,
    ) -> AsyncIterator[Event]:
        if cancel is None:
            cancel = asyncio.Event()

        env = gather_environment(self._version, self._provider.model)
        env_text = env.render()

        # 拼接 active skills block 到 env_text
        active_block = render_active_skills_block(
            self.runtime.active_skills.to_prompt_entries()
        )
        if active_block:
            env_text = env_text + "\n\n" + active_block

        sys = build_system_prompt(
            self._instruction_text, self._memory_text, self._skills_catalog_text
        )

        unknown_run = 0

        async with self._run_lock:
            for it in range(1, MAX_ITERATIONS + 1):
                emergency_retried = False
                yield Event(iter=it)

                if cancel.is_set():
                    _finish_cancelled(conv)
                    return

                # 本轮迭代开头：按 mode 选 defs（同一份列表传给 manage_context 与 _stream_once）
                if mode == Mode.PLAN:
                    defs = self._registry.read_only_definitions()
                else:
                    defs = self._registry.definitions()

                # reminder（含 hook 注入）
                reminder = self._build_reminder(mode, it)

                # ── manage_context（AUTO 路径）─────────
                await self._dispatch_hook(
                    HookEvent.PRE_COMPACT,
                    {"event": "PreCompact", "trigger": "auto"},
                )
                anchor = self.runtime.usage_anchor
                anchor_len = self.runtime.anchor_msg_len
                cw = self.runtime.context_window
                est = estimate_tokens(anchor, conv.messages(), anchor_len)

                will_summarize = (
                    cw > SUMMARY_RESERVE + AUTO_SAFETY_MARGIN
                    and est >= cw - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN
                )

                if will_summarize:
                    yield Event(compact=CompactEvent(phase=CompactPhase.BEFORE_AUTO))

                in_ = ManageInput(
                    conv=conv,
                    provider=self._provider,
                    model=self._provider.model,
                    context_window=cw,
                    tool_defs=defs,
                    replacement=self.runtime.replacement,
                    recovery=self.runtime.recovery,
                    auto_tracking=self.runtime.auto_tracking,
                    session=self.runtime.session,
                    usage_anchor=anchor,
                    anchor_msg_len=anchor_len,
                    estimated_token=est,
                    trigger=TriggerKind.AUTO,
                )

                mc_err = None
                out = None
                try:
                    out = await manage_context(in_)
                except Exception as e:
                    mc_err = e

                # 泼洒反馈：layer1 落盘了工具结果时通知用户
                if out is not None and out.spilled_count > 0:
                    yield Event(
                        notice=f"Spilled {out.spilled_count} tool result(s) to disk"
                    )

                if will_summarize:
                    yield Event(
                        compact=CompactEvent(
                            phase=CompactPhase.AFTER_AUTO,
                            before=est,
                            after=out.after_tokens if out else 0,
                            err=mc_err,
                        )
                    )

                if mc_err is not None:
                    await self._dispatch_hook(
                        HookEvent.POST_COMPACT,
                        {"event": "PostCompact", "trigger": "auto",
                         "before_tokens": est, "after_tokens": 0},
                    )
                    yield Event(err=mc_err)
                    return

                # PostCompact hook
                await self._dispatch_hook(
                    HookEvent.POST_COMPACT,
                    {"event": "PostCompact", "trigger": "auto",
                     "before_tokens": est,
                     "after_tokens": out.after_tokens if out else 0},
                )

                # ── _stream_once ──────────────────────
                # PreUserMessage hook
                last_user = ""
                msgs = conv.messages()
                for m in reversed(msgs):
                    if m.role == "user":
                        last_user = m.content
                        break
                await self._dispatch_hook(
                    HookEvent.PRE_USER_MESSAGE,
                    {"event": "PreUserMessage", "prompt": last_user},
                )

                text, calls, usage, err, text_events = await self._stream_once(
                    conv, defs, sys, env_text, reminder, cancel
                )
                for te in text_events:
                    yield te

                # 主对话路径：更新 usage_anchor
                if usage is not None:
                    self.runtime.usage_anchor = usage_anchor(usage)
                    self.runtime.anchor_msg_len = conv.length()

                # ── 紧急压缩路径 ──────────────────────
                if err is not None and isinstance(err, PromptTooLongError):
                    if emergency_retried:
                        # 已重试过一次，不再做第二次紧急压缩
                        pass
                    else:
                        # ch12: PreCompact emergency
                        await self._dispatch_hook(
                            HookEvent.PRE_COMPACT,
                            {"event": "PreCompact", "trigger": "emergency"},
                        )
                        # emit emergency before event
                        yield Event(
                            compact=CompactEvent(phase=CompactPhase.BEFORE_EMERGENCY)
                        )

                        em_in = ManageInput(
                            conv=conv,
                            provider=self._provider,
                            model=self._provider.model,
                            context_window=cw,
                            tool_defs=defs,
                            replacement=self.runtime.replacement,
                            recovery=self.runtime.recovery,
                            auto_tracking=self.runtime.auto_tracking,
                            session=self.runtime.session,
                            usage_anchor=self.runtime.usage_anchor,
                            anchor_msg_len=self.runtime.anchor_msg_len,
                            estimated_token=est,
                            trigger=TriggerKind.EMERGENCY,
                        )

                        em_err = None
                        em_out = None
                        try:
                            em_out = await manage_context(em_in)
                        except Exception as e:
                            em_err = e

                        if em_out is not None and em_out.spilled_count > 0:
                            yield Event(
                                notice=f"Spilled {em_out.spilled_count} tool result(s) to disk"
                            )

                        # ch12: PostCompact emergency
                        await self._dispatch_hook(
                            HookEvent.POST_COMPACT,
                            {"event": "PostCompact", "trigger": "emergency",
                             "before_tokens": est,
                             "after_tokens": em_out.after_tokens if em_out else 0},
                        )

                        yield Event(
                            compact=CompactEvent(
                                phase=CompactPhase.AFTER_EMERGENCY,
                                before=est,
                                after=em_out.after_tokens if em_out else 0,
                                err=em_err,
                            )
                        )

                        if em_err is not None:
                            yield Event(err=em_err)
                            return

                        # 重置锚点
                        self.runtime.usage_anchor = 0
                        self.runtime.anchor_msg_len = 0

                        # 重新估算
                        est2 = estimate_tokens(0, conv.messages(), 0)
                        if est2 >= cw - MANUAL_SAFETY_MARGIN:
                            yield Event(err=err)
                            return

                        emergency_retried = True
                        text, calls, usage, err, text_events = await self._stream_once(
                            conv, defs, sys, env_text, reminder, cancel
                        )
                        for te in text_events:
                            yield te
                        if usage is not None:
                            self.runtime.usage_anchor = usage_anchor(usage)
                            self.runtime.anchor_msg_len = conv.length()

                # ── 最终错误检查 ──────────────────────
                if err is not None:
                    await self._dispatch_hook(
                        HookEvent.NOTIFICATION,
                        {"event": "Notification", "kind": "stream_error",
                         "detail": str(err)},
                    )
                    if cancel.is_set():
                        _finish_cancelled(conv)
                        return
                    _ensure_assistant_tail(conv, NOTICE_STREAM_ERR)
                    yield Event(notice=NOTICE_STREAM_ERR)
                    yield Event(err=err)
                    yield Event(done=True)
                    return

                if cancel.is_set():
                    _finish_cancelled(conv)
                    return

                # yield usage 事件
                if usage is not None:
                    yield Event(
                        usage=Usage(
                            input=usage.input_tokens,
                            output=usage.output_tokens,
                            cache_write=usage.cache_write,
                            cache_read=usage.cache_read,
                        )
                    )

                # 无工具调用 → 自然完成
                if not calls:
                    final = _ensure_final(text)
                    conv.add_assistant(final)

                    # Stop hook
                    await self._dispatch_hook(
                        HookEvent.STOP, {"event": "Stop", "iter": it}
                    )

                    # ch09 记忆更新触发：每轮完成后异步执行
                    if self._mem_mgr is not None:
                        self.runtime.turn_count += 1
                        recent_msgs = self._extract_recent_turn(conv)
                        try:
                            asyncio.create_task(
                                self._mem_mgr.update_async(recent_msgs)
                            )
                        except Exception:
                            logger.debug("记忆更新任务创建失败", exc_info=True)

                    yield Event(done=True)
                    return

                conv.add_assistant_with_tool_calls(text, calls)

                if _all_unknown(calls, self._registry):
                    unknown_run += 1
                else:
                    unknown_run = 0

                # ── 工具执行（通过 Queue 收集事件）───
                queue: asyncio.Queue[Event | None] = asyncio.Queue()

                async def _emit(ev: Event) -> None:
                    await queue.put(ev)

                async def _run_batched():
                    r, c = await self._execute_batched(calls, mode, cancel, _emit)
                    await queue.put(None)
                    return r, c

                batch_task = asyncio.ensure_future(_run_batched())

                while True:
                    ev = await queue.get()
                    if ev is None:
                        break
                    yield ev

                results, completed = await batch_task
                conv.add_tool_results(results)

                if not completed:
                    _ensure_assistant_tail(conv, NOTICE_CANCELLED)
                    yield Event(notice=NOTICE_CANCELLED)
                    yield Event(done=True)
                    return

                if unknown_run >= MAX_UNKNOWN_RUN:
                    yield Event(notice=NOTICE_UNKNOWN_TOOLS)
                    _ensure_assistant_tail(conv, NOTICE_UNKNOWN_TOOLS)
                    yield Event(done=True)
                    return

            yield Event(notice=NOTICE_MAX_ITER)
            _ensure_assistant_tail(conv, NOTICE_MAX_ITER)
            yield Event(done=True)

    # ── _stream_once（ch08：返回 (text, calls, usage, err, text_events)）─

    async def _stream_once(
        self,
        conv: Conversation,
        defs: list,
        sys: str,
        env_text: str,
        reminder: str,
        cancel: asyncio.Event,
    ) -> tuple[str, list[ToolCall], object | None, Exception | None, list[Event]]:
        text_parts: list[str] = []
        text_events: list[Event] = []
        calls: list[ToolCall] = []
        usage: object = None
        captured_err: Exception | None = None

        from suisuicode.llm import System as LlmSystem

        req = Request(
            messages=conv.messages(),
            tools=defs,
            system=LlmSystem(stable=sys, environment=env_text),
            reminder=reminder,
        )

        try:
            async for ev in self._provider.stream(req):
                if cancel.is_set():
                    return "", [], None, None, []

                if ev.err is not None:
                    captured_err = ev.err
                    # text 不写回 Conversation（保证状态原子）
                    return "".join(text_parts), calls, usage, captured_err, text_events

                if ev.usage is not None:
                    usage = ev.usage

                if ev.tool_calls:
                    calls.extend(ev.tool_calls)

                if ev.text:
                    text_parts.append(ev.text)
                    text_events.append(Event(text=ev.text))

                if ev.done:
                    break

            if cancel.is_set():
                return "", [], None, None, []

            return "".join(text_parts), calls, usage, None, text_events

        except asyncio.CancelledError:
            raise
        except Exception as e:
            return "".join(text_parts), calls, usage, e, text_events

            return "".join(text_parts), calls, usage, None

        except asyncio.CancelledError:
            raise
        except Exception as e:
            return "".join(text_parts), calls, usage, e

    # ── run_force_compact（TUI 手动 /compact）─────────

    async def run_force_compact(
        self, conv: Conversation, tool_defs: list
    ) -> tuple[int, int]:
        """手动压缩入口：等主循环空闲后无条件触发摘要。"""
        async with self._run_lock:
            est = estimate_tokens(
                self.runtime.usage_anchor,
                conv.messages(),
                self.runtime.anchor_msg_len,
            )
            in_ = ManageInput(
                conv=conv,
                provider=self._provider,
                model=self._provider.model,
                context_window=self.runtime.context_window,
                tool_defs=tool_defs,
                replacement=self.runtime.replacement,
                recovery=self.runtime.recovery,
                auto_tracking=self.runtime.auto_tracking,
                session=self.runtime.session,
                usage_anchor=self.runtime.usage_anchor,
                anchor_msg_len=self.runtime.anchor_msg_len,
                estimated_token=est,
                trigger=TriggerKind.MANUAL,
            )
            out = await manage_context(in_)
            return out.before_tokens, out.after_tokens

    # ── 工具执行（含权限判定 + 人在回路 + ch08 ReadFile 追踪 + ch12 Hook）─

    async def _execute_batched(
        self,
        calls: list[ToolCall],
        mode: Mode,
        cancel: asyncio.Event,
        emit,
    ) -> tuple[list[ToolResult], bool]:
        # ch13: 子 Agent 显式设了 permission_mode 则覆盖入参
        if self.permission_mode is not None:
            mode = self.permission_mode
        n = len(calls)
        results: list[ToolResult | None] = [None] * n
        i = 0

        while i < n:
            if cancel.is_set():
                for k in range(i, n):
                    results[k] = ToolResult(
                        tool_call_id=calls[k].id,
                        content=NOTICE_CANCELLED,
                        is_error=True,
                    )
                return [r for r in results if r is not None], False

            tc = calls[i]
            ro = self._registry.is_read_only(tc.name)

            if ro:
                j = i + 1
                while j < n and self._registry.is_read_only(calls[j].name):
                    j += 1

                # ch12: PreToolUse hook — 只读批中的每条 tool call
                hook_blocked: dict[int, tuple[str, str]] = {}  # k -> (hook_name, reason)
                for k in range(i, j):
                    ck = calls[k]
                    hook_input = _parse_tool_input(ck.input)
                    hr = await self._dispatch_hook(
                        HookEvent.PRE_TOOL_USE,
                        {"event": "PreToolUse", "tool_name": ck.name,
                         "tool_input": hook_input},
                    )
                    if hr.blocked:
                        hook_blocked[k] = (hr.blocking_hook_name, hr.reason)
                        results[k] = ToolResult(
                            tool_call_id=ck.id,
                            content=f"[hook {hr.blocking_hook_name}] {hr.reason}",
                            is_error=True,
                        )

                # ch06 权限判定（仅对未被 hook 拦截的调用）
                deny_slots: dict[int, str] = {}
                for k in range(i, j):
                    if k in hook_blocked:
                        continue
                    d, reason = _perm_check(self._engine, mode, calls[k], True)
                    if d == Decision.DENY:
                        deny_slots[k] = reason
                        results[k] = ToolResult(
                            tool_call_id=calls[k].id,
                            content=reason,
                            is_error=True,
                        )

                for k in range(i, j):
                    await emit(
                        Event(
                            tool=ToolEvent(
                                name=calls[k].name,
                                args=_preview_args(calls[k].input),
                                phase=Phase.START,
                            )
                        )
                    )

                # ch12: PostToolUse — 被 hook 拦截的
                for k in hook_blocked:
                    await self._dispatch_hook(
                        HookEvent.POST_TOOL_USE,
                        {"event": "PostToolUse",
                         "tool_name": calls[k].name,
                         "tool_input": calls[k].input if isinstance(calls[k].input, dict) else {},
                         "tool_result": results[k].content[:500] if results[k] else "",
                         "is_error": True},
                    )

                async def run_one(k: int) -> None:
                    if k in deny_slots or k in hook_blocked:
                        return
                    r = await asyncio.wait_for(
                        self._registry.execute(calls[k].name, calls[k].input),
                        timeout=DEFAULT_TIMEOUT,
                    )
                    results[k] = ToolResult(
                        tool_call_id=calls[k].id,
                        content=r.content,
                        is_error=r.is_error,
                    )
                    await self._track_readfile(calls[k], results[k])

                await asyncio.gather(*[run_one(k) for k in range(i, j)])

                for k in range(i, j):
                    r = results[k]
                    assert r is not None
                    # ch12: PostToolUse — 权限 DENY 或工具实际执行结果
                    is_err = r.is_error or k in deny_slots or k in hook_blocked
                    await self._dispatch_hook(
                        HookEvent.POST_TOOL_USE,
                        {"event": "PostToolUse",
                         "tool_name": calls[k].name,
                         "tool_input": calls[k].input if isinstance(calls[k].input, dict) else {},
                         "tool_result": r.content[:500],
                         "is_error": is_err},
                    )
                    await emit(
                        Event(
                            tool=ToolEvent(
                                name=calls[k].name,
                                args=_preview_args(calls[k].input),
                                phase=Phase.END,
                                result=_result_preview(r.content),
                                is_error=r.is_error,
                            )
                        )
                    )

                i = j
            else:
                # ch12: PreToolUse hook（非只读，在权限判定之前）
                hook_input = _parse_tool_input(tc.input)
                hook_result = await self._dispatch_hook(
                    HookEvent.PRE_TOOL_USE,
                    {"event": "PreToolUse", "tool_name": tc.name,
                     "tool_input": hook_input},
                )
                if hook_result.blocked:
                    blocked_content = (
                        f"[hook {hook_result.blocking_hook_name}] "
                        f"{hook_result.reason}"
                    )
                    results[i] = ToolResult(
                        tool_call_id=tc.id,
                        content=blocked_content,
                        is_error=True,
                    )
                    await emit(
                        Event(tool=ToolEvent(name=tc.name,
                            args=_preview_args(tc.input), phase=Phase.START))
                    )
                    await emit(
                        Event(tool=ToolEvent(name=tc.name,
                            args=_preview_args(tc.input), phase=Phase.END,
                            result=_result_preview(blocked_content), is_error=True))
                    )
                    await self._dispatch_hook(
                        HookEvent.POST_TOOL_USE,
                        {"event": "PostToolUse", "tool_name": tc.name,
                         "tool_input": hook_input,
                         "tool_result": blocked_content[:500], "is_error": True},
                    )
                    i += 1
                    continue

                d, reason = _perm_check(self._engine, mode, tc, False)

                if d == Decision.DENY:
                    results[i] = ToolResult(
                        tool_call_id=tc.id, content=reason, is_error=True,
                    )
                    await emit(
                        Event(tool=ToolEvent(name=tc.name,
                            args=_preview_args(tc.input), phase=Phase.START))
                    )
                    await emit(
                        Event(tool=ToolEvent(name=tc.name,
                            args=_preview_args(tc.input), phase=Phase.END,
                            result=_result_preview(reason), is_error=True))
                    )
                    # ch12: PostToolUse for DENY
                    await self._dispatch_hook(
                        HookEvent.POST_TOOL_USE,
                        {"event": "PostToolUse", "tool_name": tc.name,
                         "tool_input": hook_input,
                         "tool_result": reason[:500], "is_error": True},
                    )

                elif d == Decision.ASK:
                    # ch13: 子 Agent dontAsk 模式——直接 Allow
                    if self.dont_ask:
                        await emit(
                            Event(tool=ToolEvent(name=tc.name,
                                args=_preview_args(tc.input), phase=Phase.START))
                        )
                        r_tool = await asyncio.wait_for(
                            self._registry.execute(tc.name, tc.input),
                            timeout=DEFAULT_TIMEOUT,
                        )
                        results[i] = ToolResult(
                            tool_call_id=tc.id, content=r_tool.content,
                            is_error=r_tool.is_error,
                        )
                        await self._track_readfile(tc, results[i])
                        await self._dispatch_hook(
                            HookEvent.POST_TOOL_USE,
                            {"event": "PostToolUse", "tool_name": tc.name,
                             "tool_input": hook_input,
                             "tool_result": r_tool.content[:500],
                             "is_error": r_tool.is_error},
                        )
                        await emit(
                            Event(tool=ToolEvent(name=tc.name,
                                args=_preview_args(tc.input), phase=Phase.END,
                                result=_result_preview(r_tool.content),
                                is_error=r_tool.is_error))
                        )
                        i += 1
                        continue

                    # ch13: 子 Agent 升级到父 TUI 审批
                    if self.approval_upgrader is not None:
                        req = ApprovalRequest(
                            name=tc.name,
                            args=_preview_args(tc.input),
                            reason=reason,
                            respond=asyncio.get_running_loop().create_future(),
                        )
                        outcome, ok = await self.approval_upgrader(req)
                        if ok:
                            if outcome == Outcome.ALLOW_ONCE:
                                await emit(
                                    Event(tool=ToolEvent(name=tc.name,
                                        args=_preview_args(tc.input), phase=Phase.START))
                                )
                                r_tool = await asyncio.wait_for(
                                    self._registry.execute(tc.name, tc.input),
                                    timeout=DEFAULT_TIMEOUT,
                                )
                                results[i] = ToolResult(
                                    tool_call_id=tc.id, content=r_tool.content,
                                    is_error=r_tool.is_error,
                                )
                                await self._track_readfile(tc, results[i])
                                await self._dispatch_hook(
                                    HookEvent.POST_TOOL_USE,
                                    {"event": "PostToolUse", "tool_name": tc.name,
                                     "tool_input": hook_input,
                                     "tool_result": r_tool.content[:500],
                                     "is_error": r_tool.is_error},
                                )
                                await emit(
                                    Event(tool=ToolEvent(name=tc.name,
                                        args=_preview_args(tc.input), phase=Phase.END,
                                        result=_result_preview(r_tool.content),
                                        is_error=r_tool.is_error))
                                )
                                i += 1
                                continue
                            elif outcome == Outcome.ALLOW_FOREVER:
                                try:
                                    persist_local_allow(self._engine, tc)
                                except Exception:
                                    logger.warning("永久放行写入失败", exc_info=True)
                                await emit(
                                    Event(tool=ToolEvent(name=tc.name,
                                        args=_preview_args(tc.input), phase=Phase.START))
                                )
                                r_tool = await asyncio.wait_for(
                                    self._registry.execute(tc.name, tc.input),
                                    timeout=DEFAULT_TIMEOUT,
                                )
                                results[i] = ToolResult(
                                    tool_call_id=tc.id, content=r_tool.content,
                                    is_error=r_tool.is_error,
                                )
                                await self._track_readfile(tc, results[i])
                                await self._dispatch_hook(
                                    HookEvent.POST_TOOL_USE,
                                    {"event": "PostToolUse", "tool_name": tc.name,
                                     "tool_input": hook_input,
                                     "tool_result": r_tool.content[:500],
                                     "is_error": r_tool.is_error},
                                )
                                await emit(
                                    Event(tool=ToolEvent(name=tc.name,
                                        args=_preview_args(tc.input), phase=Phase.END,
                                        result=_result_preview(r_tool.content),
                                        is_error=r_tool.is_error))
                                )
                                i += 1
                                continue
                            else:  # DENY_ONCE
                                deny_reason = f"用户拒绝：{reason}"
                                results[i] = ToolResult(
                                    tool_call_id=tc.id, content=deny_reason,
                                    is_error=True,
                                )
                                await emit(
                                    Event(tool=ToolEvent(name=tc.name,
                                        args=_preview_args(tc.input), phase=Phase.START))
                                )
                                await emit(
                                    Event(tool=ToolEvent(name=tc.name,
                                        args=_preview_args(tc.input), phase=Phase.END,
                                        result=_result_preview(deny_reason), is_error=True))
                                )
                                i += 1
                                continue

                    outcome, user_cancelled = await self._request_approval(
                        tc, reason, emit
                    )
                    if user_cancelled:
                        for k in range(i, n):
                            results[k] = ToolResult(
                                tool_call_id=calls[k].id,
                                content=NOTICE_CANCELLED, is_error=True,
                            )
                        return [r for r in results if r is not None], False

                    if outcome == Outcome.DENY_ONCE:
                        deny_reason = f"用户拒绝：{reason}"
                        results[i] = ToolResult(
                            tool_call_id=tc.id, content=deny_reason,
                            is_error=True,
                        )
                        await emit(
                            Event(tool=ToolEvent(name=tc.name,
                                args=_preview_args(tc.input), phase=Phase.START))
                        )
                        await emit(
                            Event(tool=ToolEvent(name=tc.name,
                                args=_preview_args(tc.input), phase=Phase.END,
                                result=_result_preview(deny_reason), is_error=True))
                        )
                        # ch12: PostToolUse for user-DENY
                        await self._dispatch_hook(
                            HookEvent.POST_TOOL_USE,
                            {"event": "PostToolUse", "tool_name": tc.name,
                             "tool_input": hook_input,
                             "tool_result": deny_reason[:500], "is_error": True},
                        )
                    else:
                        if outcome == Outcome.ALLOW_FOREVER:
                            try:
                                persist_local_allow(self._engine, tc)
                            except Exception:
                                logger.warning("永久放行写入失败", exc_info=True)
                        await emit(
                            Event(tool=ToolEvent(name=tc.name,
                                args=_preview_args(tc.input), phase=Phase.START))
                        )
                        r_tool = await asyncio.wait_for(
                            self._registry.execute(tc.name, tc.input),
                            timeout=DEFAULT_TIMEOUT,
                        )
                        results[i] = ToolResult(
                            tool_call_id=tc.id, content=r_tool.content,
                            is_error=r_tool.is_error,
                        )
                        await self._track_readfile(tc, results[i])
                        # ch12: PostToolUse for user-ALLOW
                        await self._dispatch_hook(
                            HookEvent.POST_TOOL_USE,
                            {"event": "PostToolUse", "tool_name": tc.name,
                             "tool_input": hook_input,
                             "tool_result": r_tool.content[:500],
                             "is_error": r_tool.is_error},
                        )
                        await emit(
                            Event(tool=ToolEvent(name=tc.name,
                                args=_preview_args(tc.input), phase=Phase.END,
                                result=_result_preview(r_tool.content),
                                is_error=r_tool.is_error))
                        )

                else:  # ALLOW
                    await emit(
                        Event(tool=ToolEvent(name=tc.name,
                            args=_preview_args(tc.input), phase=Phase.START))
                    )
                    r_tool = await asyncio.wait_for(
                        self._registry.execute(tc.name, tc.input),
                        timeout=DEFAULT_TIMEOUT,
                    )
                    results[i] = ToolResult(
                        tool_call_id=tc.id, content=r_tool.content,
                        is_error=r_tool.is_error,
                    )
                    await self._track_readfile(tc, results[i])
                    # ch12: PostToolUse for ALLOW
                    await self._dispatch_hook(
                        HookEvent.POST_TOOL_USE,
                        {"event": "PostToolUse", "tool_name": tc.name,
                         "tool_input": hook_input,
                         "tool_result": r_tool.content[:500],
                         "is_error": r_tool.is_error},
                    )
                    await emit(
                        Event(tool=ToolEvent(name=tc.name,
                            args=_preview_args(tc.input), phase=Phase.END,
                            result=_result_preview(r_tool.content),
                            is_error=r_tool.is_error))
                    )

                i += 1

        return [r for r in results if r is not None], True

    # ── ch09 记忆更新 ──────────────────────────────────

    @staticmethod
    def _extract_recent_turn(conv: Conversation) -> list:
        """提取最近一轮消息（从最后一条 user 到末尾）。"""
        msgs = conv.messages()
        # 从尾部找到最后一条 user 消息的索引
        last_user_idx = -1
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].role == "user":
                last_user_idx = i
                break
        if last_user_idx == -1:
            return list(msgs)
        return list(msgs[last_user_idx:])

    # ── ch08 ReadFile 追踪 ────────────────────────────

    async def _track_readfile(self, call: ToolCall, result: ToolResult | None) -> None:
        """ReadFile 成功后用纯净字节写入 RecoveryState。"""
        if result is None or result.is_error:
            return
        if call.name != "read_file":
            return

        args = call.input
        if not isinstance(args, dict):
            return
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return

        try:
            abs_path = str(Path(path).resolve())
        except OSError:
            return

        try:
            data = await asyncio.to_thread(Path(abs_path).read_bytes)
        except OSError:
            return

        self.runtime.recovery.record_file(
            abs_path, data.decode("utf-8", errors="replace")
        )

    # ── 人在回路 ──────────────────────────────────────

    async def _request_approval(
        self,
        call: ToolCall,
        reason: str,
        emit,
    ) -> tuple[Outcome, bool]:
        loop = asyncio.get_running_loop()
        respond: asyncio.Future[Outcome] = loop.create_future()

        # Notification hook
        await self._dispatch_hook(
            HookEvent.NOTIFICATION,
            {"event": "Notification", "kind": "approval",
             "detail": call.name},
        )

        await emit(
            Event(
                approval=ApprovalRequest(
                    name=call.name,
                    args=_preview_args(call.input),
                    reason=reason,
                    respond=respond,
                )
            )
        )

        try:
            outcome = await respond
            return outcome, False
        except asyncio.CancelledError:
            return Outcome.DENY_ONCE, True
