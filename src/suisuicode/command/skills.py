"""Skill 命令注册：把 Catalog 中的 Skill 注册为 / 命令。"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from suisuicode.command.command import Command, Kind

if TYPE_CHECKING:
    from suisuicode.command.registry import Registry
    from suisuicode.skills.catalog import Catalog
    from suisuicode.skills.executor import Executor

logger = logging.getLogger(__name__)

# 跟踪已注册的 skill 命令名，用于 reload 时清理
_REGISTERED_SKILL_NAMES: set[str] = set()


def register_skills_as_commands(
    reg: "Registry",
    catalog: "Catalog",
    executor: "Executor",
) -> None:
    """扫描 Catalog，为每个 Skill 注册一条 / 命令。

    已注册的会先清理再重新注册（支持 reload 场景）。
    """
    # 先清理旧注册
    remove_skill_commands(reg)

    for skill in catalog.list():
        name = skill.name

        # 与内置命令冲突检测：跳过同名
        if reg.lookup(name) is not None:
            logger.warning("skill %r 与已有命令冲突，跳过注册", name)
            continue

        description = f"{skill.description} [skill]"

        # 用 functools.partial 避免闭包循环变量陷阱
        if skill.meta.is_fork():
            handler = _make_fork_handler(name, executor)
        else:
            handler = _make_inline_handler(name, executor)

        cmd = Command(
            name=name,
            description=description,
            kind=Kind.PROMPT,
            handler=handler,
        )
        try:
            reg.register(cmd)
        except RuntimeError as e:
            logger.warning("skill %r 注册失败: %s", name, e)
            continue

        _REGISTERED_SKILL_NAMES.add(name)


def remove_skill_commands(reg: "Registry") -> None:
    """移除之前由 register_skills_as_commands 注册的所有命令。"""
    for name in list(_REGISTERED_SKILL_NAMES):
        if name in reg._by_name:
            del reg._by_name[name]
            reg._visible = [c for c in reg._visible if c.name != name]
    _REGISTERED_SKILL_NAMES.clear()


def _make_inline_handler(name: str, executor: "Executor"):
    """构造 inline skill 的 / 命令 handler。"""

    async def _handler(ui, args="") -> None:
        await executor.execute(name, args, ui=ui)

    _handler.__name__ = f"_inline_{name}"
    return _handler


def _make_fork_handler(name: str, executor: "Executor"):
    """构造 fork skill 的 / 命令 handler。"""

    async def _handler(ui, args="") -> None:
        # fork 在后台运行，结果作为 assistant 消息插入
        asyncio.create_task(_run_fork(name, args, executor, ui))

    _handler.__name__ = f"_fork_{name}"
    return _handler


async def _run_fork(name: str, args: str, executor: "Executor", ui) -> None:
    """后台运行 fork skill，结果写入主对话。"""
    try:
        await executor.execute(name, args, ui=ui)
    except Exception as e:
        logger.warning("fork skill %r 后台执行异常: %s", name, e)
        ui.append_assistant_message(f"[skill {name} failed: {e}]")
