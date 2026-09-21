"""类型桥接：skills 包类型 ↔ prompt 包类型，避免循环依赖。"""

from __future__ import annotations


def to_prompt_items(skills: list) -> list:
    """将 Skill 列表转为 prompt 包的 SkillCatalogItem 列表。"""
    # 延迟 import 防止循环依赖
    from suisuicode.prompt.skills_block import SkillCatalogItem

    return [SkillCatalogItem(name=s.name, description=s.description) for s in skills]


def to_prompt_entries(entries: list) -> list:
    """将 ActiveEntry 列表转为 prompt 包的 ActiveSkillEntry 列表。"""
    from suisuicode.prompt.skills_block import ActiveSkillEntry

    return [ActiveSkillEntry(name=e.name, body=e.body) for e in entries]
