from __future__ import annotations

from suisuicode.prompt.environment import (
    Environment,
    gather_environment,
    gather_git_branch,
)
from suisuicode.prompt.modules import (
    PromptSection,
    Section,
    SystemSection,
    fixed_sections,
    optional_sections,
)
from suisuicode.prompt.reminder import EXECUTE_DIRECTIVE, plan_reminder, system_reminder
from suisuicode.prompt.skills_block import (
    ActiveSkillEntry,
    SkillCatalogItem,
    render_active_skills_block,
    render_skills_catalog,
)

# ── Banner ─────────────────────────────────────────────

CAT_BANNER = r"""
  /\_/\
 ( o.o )
  > ^ <
"""


def render_banner(version: str, cwd: str) -> str:
    """返回纯文本横幅（由调用方决定 Rich 颜色）。"""
    lines = [
        CAT_BANNER.rstrip(),
        f"  SuisuiCode v{version}",
        f"  {cwd}",
        "",
        "  Ready. 输入 /help 查看可用命令。",
    ]
    return "\n".join(lines)


# ── 模块装配 — Context Section 结构 ──────────────────


def _render_section(s: PromptSection) -> str:
    """将 PromptSection 渲染为 markdown Context Section。

    若 content 以 #  开头，直接返回 content（自带标题）；
    否则以 name 字段作为节标题。
    """
    if s.content.startswith("# "):
        return s.content
    return f"# {s.name}\n\n{s.content}"


def build_system_prompt(
    instructions: str = "", memory: str = "", skills_catalog: str = ""
) -> str:
    """装配完整稳定系统提示（固定 + 可选 section）。

    每个 section 渲染为 # 节标题 + 正文，以 \n\n---\n\n 分隔，
    形成 Claude Code 风格的 Context Section 结构。

    instructions 非空时填入 custom-instructions 模块（priority 80）。
    skills_catalog 非空时填入可用 Skill 模块（priority 90）。
    memory 非空时填入 long-term-memory 模块（priority 100）。
    """
    all_sections = fixed_sections() + optional_sections(
        instructions, memory, skills_catalog
    )
    sorted_secs = sorted(all_sections, key=lambda s: s.priority)
    rendered = [_render_section(s) for s in sorted_secs if s.content]
    return "\n\n---\n\n".join(rendered)


# ── 向后兼容 ─────────────────────────────────────────


def assemble_system(mods: list) -> str:
    """兼容旧版 Module 列表签名。"""
    return build_system_prompt()


# ── 重导出 ────────────────────────────────────────────

__all__ = [
    "ActiveSkillEntry",
    "CAT_BANNER",
    "EXECUTE_DIRECTIVE",
    "Environment",
    "PromptSection",
    "Section",
    "SkillCatalogItem",
    "SystemSection",
    "assemble_system",
    "build_system_prompt",
    "fixed_sections",
    "gather_environment",
    "gather_git_branch",
    "optional_sections",
    "plan_reminder",
    "render_active_skills_block",
    "render_banner",
    "render_skills_catalog",
    "system_reminder",
]
