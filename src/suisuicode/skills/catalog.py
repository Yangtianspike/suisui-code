"""Catalog：两层路径扫描、覆盖管理与热重载。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from suisuicode.skills.parser import SkillParseError, parse_skill_dir
from suisuicode.skills.types import Skill, SkillSource

if TYPE_CHECKING:
    from suisuicode.tool import Registry as ToolRegistry

logger = logging.getLogger(__name__)

PROJECT_SKILLS_DIR = ".suisuicode/skills"
USER_SKILLS_DIR = "~/.suisuicode/skills"
CLAUDE_SKILLS_DIR = "~/.claude/skills"


class Catalog:
    """Skill 编目：三层扫描（Claude < 用户 SuisuiCode < 项目 SuisuiCode）、热重载。

    通过 ``Catalog.load(work_dir)`` 构造；之后可 ``reload`` 刷新。
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._by_name: dict[str, Skill] = {}
        self._order: list[str] = []  # 按 name 排序的稳定迭代序

    # ── 工厂 ──────────────────────────────────────────

    @classmethod
    def load(cls, work_dir: Path) -> "Catalog":
        """扫描两层路径并返回编目。"""
        cat = cls()
        cat.reload(work_dir)
        return cat

    # ── 扫描与重载 ─────────────────────────────────────

    def reload(self, work_dir: Path) -> None:
        """重新扫描三层目录（Claude → 用户 SuisuiCode → 项目 SuisuiCode），替换内存索引。

        扫描顺序即覆盖优先级：后扫到的同名 skill 覆盖前者。
        所以 Claude（最低）先扫 → 用户 SuisuiCode → 项目 SuisuiCode（最高）。
        """
        new_by_name: dict[str, Skill] = {}
        new_order: list[str] = []

        claude_dir = Path(CLAUDE_SKILLS_DIR).expanduser().resolve()
        user_dir = Path(USER_SKILLS_DIR).expanduser().resolve()
        project_dir = (work_dir / PROJECT_SKILLS_DIR).resolve()

        # 优先级从低到高扫描：Claude < 用户 SuisuiCode < 项目 SuisuiCode
        for d, src in [
            (claude_dir, SkillSource.CLAUDE),
            (user_dir, SkillSource.USER),
            (project_dir, SkillSource.PROJECT),
        ]:
            if not d.is_dir():
                continue
            for entry in sorted(d.iterdir()):
                if not entry.is_dir():
                    continue
                try:
                    skill = parse_skill_dir(entry, src)
                except SkillParseError as e:
                    logger.warning("Skipping skill %s: %s", entry.name, e)
                    continue
                except Exception:
                    logger.warning(
                        "Skipping skill '%s': 解析失败", entry.name, exc_info=True
                    )
                    continue

                name = skill.name
                if name in new_by_name:
                    old_src = new_by_name[name].source.value
                    logger.debug(
                        "skill %r 被 %s 级覆盖（原 %s）",
                        name,
                        src.value,
                        old_src,
                    )
                    # 移除旧顺序条目
                    new_order.remove(name)
                new_by_name[name] = skill
                new_order.append(name)

        new_order.sort()
        self._by_name = new_by_name
        self._order = new_order

    # ── 查询 ──────────────────────────────────────────

    def get(self, name: str) -> Skill | None:
        """按名获取 Skill，并热重载（重读源文件，失败回退缓存）。"""
        skill = self._by_name.get(name)
        if skill is None:
            return None
        try:
            return parse_skill_dir(skill.source_dir, skill.source)
        except Exception:
            logger.warning("skill %r 热重载失败，回退缓存版本", name, exc_info=True)
            return skill

    def list(self) -> list[Skill]:
        """按 name 排序返回所有 Skill 列表。"""
        return [self._by_name[n] for n in self._order]

    def names(self) -> list[str]:
        """返回所有 Skill 名称（按排序）。"""
        return list(self._order)

    # ── 工具校验 ──────────────────────────────────────

    def validate_tools(self, reg: "ToolRegistry") -> list[str]:
        """检查所有 Skill 的 allowed_tools 是否在 ToolRegistry 中存在。

        返回不通过的工具名列表（供调用方 warning + 移除该 Skill）。
        """
        issues: list[str] = []
        for name in list(self._order):
            skill = self._by_name[name]
            for tool_name in skill.meta.allowed_tools:
                if reg.get(tool_name) is None:
                    msg = (
                        f"skill {name!r} 引用了不存在的工具 {tool_name!r}，"
                        f"该 skill 已从编目中移除"
                    )
                    logger.warning(msg)
                    issues.append(name)
                    del self._by_name[name]
                    self._order.remove(name)
                    break
        return issues

    # ── 适配 ──────────────────────────────────────────

    def to_prompt_items(self) -> list:
        """转为 prompt 包 SkillCatalogItem 列表（避免循环依赖）。"""
        from suisuicode.skills.adapter import to_prompt_items

        return to_prompt_items(self.list())

    def __len__(self) -> int:
        return len(self._by_name)
