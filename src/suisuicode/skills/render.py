"""Skill body 渲染：参数替换、工具提示注入。"""

from __future__ import annotations

from suisuicode.skills.parser import substitute_arguments
from suisuicode.skills.types import Skill


def render_body(skill: Skill, args: str = "") -> str:
    """渲染 Skill body 为最终注入文本。

    1. 替换 ``$ARGUMENTS``（委托给 substitute_arguments）
    2. 若 allowed_tools 非空，在顶部插入工具提示
    """
    rendered = substitute_arguments(skill.prompt_body, args)

    if skill.meta.allowed_tools:
        tools_str = ", ".join(skill.meta.allowed_tools)
        header = (
            f"This skill is designed to use only these tools: {tools_str}. "
            f"Prefer them over other tools when possible.\n\n---\n\n"
        )
        rendered = header + rendered

    return rendered
