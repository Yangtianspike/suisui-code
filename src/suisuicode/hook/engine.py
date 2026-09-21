"""Hook 事件分派引擎：dispatch + only_once 集合 + reset。"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field

from suisuicode.hook.event import Event, is_blocking
from suisuicode.hook.executor import ExecutionResult, Executor
from suisuicode.hook.matcher import eval_condition
from suisuicode.hook.rule import Payload, Rule


@dataclass
class DispatchResult:
    """一次事件分派的汇总结果。

    blocked=True 时调用方应阻止当前操作（PreToolUse 跳过工具、UserPromptSubmit 不消费输入）。
    """

    blocked: bool = False
    reason: str = ""
    blocking_hook_name: str = ""
    injected_prompts: list[str] = field(default_factory=list)


class Engine:
    """Hook 事件分派引擎。

    由 Loader 构造，持有已编译的 Hook 规则列表与 only_once 已触发集合。
    """

    def __init__(self, rules: list[Rule], sources: list[str]) -> None:
        self._rules = rules  # 按 YAML 声明顺序
        self._sources = sources  # 来源文件路径列表
        self._once_fired: set[str] = set()  # only_once 已触发的 hook name
        self._lock = asyncio.Lock()
        self._executor = Executor()

    # ── dispatch ──────────────────────────────────────

    async def dispatch(
        self, event: Event, payload: Payload
    ) -> DispatchResult:
        """对事件分派所有匹配的 hook 规则，返回汇总结果。

        流程：
        1. 过滤匹配 event 的 rule
        2. 跳过 _once_fired 中已触发的 only_once rule
        3. 串行求值 if 条件
        4. 命中条件后按 action.type 分发到 Executor
        5. async rule 起 asyncio task、立即往下走
        6. 同步 rule 等结果，拦截类事件若 blocked 则中断后续
        7. prompt 类 rule 把 text 累加到 injected_prompts
        """
        result = DispatchResult()
        for rule in self._rules:
            if rule.event is not event:
                continue

            # only_once check
            async with self._lock:
                if rule.only_once and rule.name in self._once_fired:
                    continue

            if not eval_condition(rule.condition, payload):
                continue

            # async hook：起 task 后立即继续，不参与 blocked/injected_prompts
            if rule.asyncio_mode:
                asyncio.create_task(
                    self._executor.run(rule, payload, blocking=False)
                )
                if rule.only_once:
                    async with self._lock:
                        self._once_fired.add(rule.name)
                continue

            # 同步执行
            outcome: ExecutionResult = await self._executor.run(
                rule, payload, blocking=is_blocking(event)
            )

            # hook 自身失败 → 记日志后继续
            if outcome.err is not None:
                print(
                    f"[hook {rule.name}] {event.value} failed: {outcome.err}",
                    file=sys.stderr,
                )
                continue

            # prompt 注入
            if outcome.prompt:
                result.injected_prompts.append(outcome.prompt)

            # only_once 标记
            if rule.only_once:
                async with self._lock:
                    self._once_fired.add(rule.name)

            # 拦截判定
            if outcome.blocked and is_blocking(event):
                result.blocked = True
                result.reason = outcome.reason
                result.blocking_hook_name = rule.name
                break  # 首个拦截命中即停止

        return result

    # ── session lifecycle ─────────────────────────────

    async def reset_for_new_session(self) -> None:
        """清空 only_once 集合，在新会话开始时调用。"""
        async with self._lock:
            self._once_fired.clear()

    # ── properties ─────────────────────────────────────

    @property
    def sources(self) -> list[str]:
        return list(self._sources)

    @property
    def rules(self) -> list[Rule]:
        return list(self._rules)
