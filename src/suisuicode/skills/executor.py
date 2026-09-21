"""Skill 执行器：inline / fork 分发、工具白名单过滤。"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from suisuicode.skills.render import render_body
from suisuicode.skills.types import Skill

if TYPE_CHECKING:
    from suisuicode.llm import Provider
    from suisuicode.permission.engine import Engine
    from suisuicode.tool import Registry as ToolRegistry

logger = logging.getLogger(__name__)

# 系统工具名称——这些工具在 fork 子 Agent 中自动透传
SYSTEM_TOOL_NAMES: frozenset = frozenset({"LoadSkill"})


class SkillDependencyError(Exception):
    """allowed_tools 中引用了不存在的工具时抛出。"""


def filter_tool_registry(
    registry: "ToolRegistry",
    allowed: list[str],
) -> "ToolRegistry":
    """按白名单 + 系统工具豁免创建新 ToolRegistry 视图。

    - *allowed* 为空：直接返回原 registry（不限制工具）
    - *allowed* 非空：缺工具抛出 SkillDependencyError；
      从原 registry 中选出 allowed 列表中的工具，
      并自动透传 ``is_system_tool == True`` 的工具。
    """
    from suisuicode.tool import Registry as ToolRegistry

    if not allowed:
        return registry

    # 校验所有 allowed 工具都存在
    for name in allowed:
        if registry.get(name) is None:
            raise SkillDependencyError(f"skill 引用了不存在的工具: {name!r}")

    new_reg = ToolRegistry()
    for name in registry._order:
        tool = registry._tools[name]
        if name in allowed or getattr(tool, "is_system_tool", False):
            new_reg.register(tool)

    return new_reg


class Executor:
    """Skill 执行器——inline / fork 两条路径。

    被 Slash 命令 handler 调用；持有 catalog、runtime、registry、
    provider、permission engine 等依赖。
    """

    def __init__(
        self,
        catalog,
        runtime,
        registry: "ToolRegistry",
        provider: "Provider",
        eng: "Engine",
        version: str = "",
    ) -> None:
        self._catalog = catalog
        self._runtime = runtime
        self._registry = registry
        self._provider = provider
        self._eng = eng
        self._version = version

    # ── 入口 ──────────────────────────────────────────

    async def execute(
        self,
        name: str,
        args: str = "",
        *,
        conv=None,
        ui=None,
    ) -> None:
        """按 skill.mode 分发到 inline 或 fork 路径。"""
        skill = self._catalog.get(name)
        if skill is None:
            if ui is not None:
                ui.ui_error(f"未知 skill: {name}")
            return

        if skill.meta.is_fork():
            await self._execute_fork(skill, args, conv=conv, ui=ui)
        else:
            await self._execute_inline(skill, args, ui=ui)

    # ── inline ────────────────────────────────────────

    async def _execute_inline(self, skill: Skill, args: str, *, ui=None) -> None:
        """inline 路径：渲染 body → 激活 skill → 注入 user 消息触发 loop。"""
        rendered = render_body(skill, args)
        # 激活 skill（钉到 env context）
        self._runtime.active_skills.activate(skill.name, rendered)

        if ui is not None:
            label = f"/{skill.name}"
            # args 为空时用 skill 描述作为默认触发 prompt
            trigger = args.strip() if args.strip() else f"执行 {skill.name}"
            ui.ui_inject_and_send(label, trigger)

    # ── fork ──────────────────────────────────────────

    async def _execute_fork(
        self,
        skill: Skill,
        args: str,
        *,
        conv=None,
        ui=None,
    ) -> None:
        """fork 路径：独立子 Agent 跑完，收集结果写回主对话。"""
        # 延迟 import 避免循环依赖
        from suisuicode.agent.agent import Agent as AgentClass
        from suisuicode.agent.runtime import new_session_runtime
        from suisuicode.conversation import Conversation

        rendered = render_body(skill, args)

        # 构造子对话
        fork_conv = Conversation()
        fork_context = skill.meta.fork_context

        if fork_context == "full" and conv is not None:
            # 全量上下文：用主对话摘要
            main_msgs = conv.messages()
            summary_lines = ["## Previous conversation summary\n"]
            for m in main_msgs[-20:]:  # 最近 20 条作为摘要
                role = m.role
                content = str(m.content)[:500]
                summary_lines.append(f"[{role}] {content}")
            fork_conv.add_user("\n".join(summary_lines))
        elif fork_context == "recent" and conv is not None:
            # 最近 5 条
            main_msgs = conv.messages()
            for m in main_msgs[-5:]:
                if m.role in ("user", "assistant"):
                    fork_conv._messages.append(m)
        # none: 空对话

        fork_conv.add_user(rendered)

        # 工具过滤
        try:
            fork_registry = filter_tool_registry(
                self._registry, skill.meta.allowed_tools
            )
        except SkillDependencyError as e:
            err_msg = f"[skill {skill.name} failed: {e}]"
            if ui is not None:
                ui.append_assistant_message(err_msg)
            return

        # 选 provider
        fork_provider = self._provider
        if skill.meta.model:
            from suisuicode.llm import new_provider

            try:
                fork_provider = new_provider(skill.meta.model)
            except Exception:
                logger.warning(
                    "skill %r: 无法创建 model=%s 的 provider，使用默认",
                    skill.name,
                    skill.meta.model,
                )

        # 构造子 Agent
        fork_runtime = new_session_runtime()
        fork_agent = AgentClass(
            provider=fork_provider,
            registry=fork_registry,
            engine=self._eng,
            version=self._version,
            runtime=fork_runtime,
        )

        # 跑子 Agent，收集事件
        result_parts: list[str] = []
        try:
            async for event in fork_agent.run(fork_conv):
                if event.text:
                    result_parts.append(event.text)
                if event.done:
                    break
        except asyncio.CancelledError:
            result_parts.append("\n[fork cancelled]")
        except Exception as e:
            logger.warning("fork skill %r 异常: %s", skill.name, e)
            result_parts.append(f"\n[fork error: {e}]")

        final_text = "".join(result_parts).strip()
        if not final_text:
            final_text = f"[skill {skill.name} completed]"

        # 写回主对话
        if ui is not None:
            ui.append_assistant_message(final_text)
