"""Skill 相关的提示词块：Catalog 段 + Active Skills 段。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillCatalogItem:
    """Catalog 列表中每条 skill 的轻量表示（避免 prompt 包依赖 skills 包）。"""

    name: str
    description: str


@dataclass(frozen=True)
class ActiveSkillEntry:
    """激活态 skill 条目（避免 prompt 包依赖 skills 包）。"""

    name: str
    body: str


def render_skills_catalog(items: list[SkillCatalogItem]) -> str:
    """渲染「Available Skills」段——对齐 Claude Code 格式。

    items 为空返回空字符串（装配时跳过）。
    """
    if not items:
        return ""
    lines = [
        "# Available Skills",
        "",
        "你可以使用 LoadSkill 工具按需激活以下 Skill。当用户请求与某个 Skill 匹配时，",
        "调用 LoadSkill({name: \"<name>\"})。已通过 / 命令显式调用的 Skill 自动激活。",
        "",
    ]
    for item in items:
        lines.append(f"- {item.name}: {item.description}")
    return "\n".join(lines)


def render_active_skills_block(entries: list[ActiveSkillEntry]) -> str:
    """渲染「## Active Skills」段——每个激活 skill 的完整 SOP。

    entries 为空返回空字符串（装配时跳过）。
    """
    if not entries:
        return ""
    parts = ["## Active Skills", ""]
    for entry in entries:
        parts.append(f"### {entry.name}")
        parts.append("")
        parts.append(entry.body)
        parts.append("")
    return "\n".join(parts).rstrip()
