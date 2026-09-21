"""Skill 系统核心数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


class SkillSource(Enum):
    CLAUDE = "claude"  # ~/.claude/skills/ — 最低优先级
    USER = "user"  # ~/.suisuicode/skills/
    PROJECT = "project"  # <project>/.suisuicode/skills/ — 最高优先级


@dataclass
class SkillMeta:
    """SKILL.md frontmatter 解析后的元数据。"""

    name: str
    description: str
    allowed_tools: list[str] = field(default_factory=list)
    mode: Literal["inline", "fork"] = "inline"
    fork_context: Literal["none", "recent", "full"] = "none"
    model: str | None = None

    def is_fork(self) -> bool:
        return self.mode == "fork"


@dataclass
class Skill:
    """一个完整的 Skill 定义：元数据 + 正文 + 来源信息。"""

    meta: SkillMeta
    prompt_body: str  # SKILL.md 去 frontmatter 后的正文
    source_dir: Path  # skill 目录绝对路径，重读 SKILL.md 时用
    source: SkillSource

    @property
    def name(self) -> str:
        return self.meta.name

    @property
    def description(self) -> str:
        return self.meta.description


@dataclass
class ActiveEntry:
    """激活态 Skill 条目：名称 + 激活那一刻的正文。"""

    name: str
    body: str
