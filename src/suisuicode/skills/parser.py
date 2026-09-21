"""SKILL.md 解析：frontmatter 分离、校验、参数替换。"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from suisuicode.skills.types import Skill, SkillMeta, SkillSource

logger = logging.getLogger(__name__)

# name 必须是全小写字母数字 + 连字符，以字母开头
_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]*$")

# frontmatter 分隔符
_FM_SEP = re.compile(r"^---\s*$", re.MULTILINE)


class SkillParseError(Exception):
    """Skill 解析失败时抛出。"""


def substitute_arguments(prompt_body: str, args: str) -> str:
    """替换 prompt_body 中的 ``$ARGUMENTS`` 占位符为 args。

    若无占位符且 args 非空，在末尾追加 ``## User Request`` 段。
    """
    if "$ARGUMENTS" in prompt_body:
        return prompt_body.replace("$ARGUMENTS", args)
    if args.strip():
        return prompt_body + f"\n\n## User Request\n\n{args}"
    return prompt_body


def parse_frontmatter_and_body(raw: str) -> tuple[dict, str]:
    """从原始 SKILL.md 文本分离 frontmatter 与正文。

    frontmatter 由首尾各一行的 ``---`` 界定。
    返回 ``(frontmatter_dict, body)``。
    """
    # 找到所有 --- 行位置
    matches = list(_FM_SEP.finditer(raw))
    if len(matches) < 2:
        raise SkillParseError("缺少 frontmatter 分隔符（需要开头的 --- 与结尾的 ---）")

    # 第一组 --- 必须是文件开头或仅前有空白
    first = matches[0]
    prefix = raw[: first.start()]
    if prefix.strip():
        raise SkillParseError("frontmatter 的起始 --- 之前不应有非空白内容")

    second = matches[1]
    fm_text = raw[first.end() : second.start()]

    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise SkillParseError(f"frontmatter YAML 解析失败: {e}") from e

    if not isinstance(fm, dict):
        raise SkillParseError("frontmatter 必须是 YAML mapping")

    body = raw[second.end() :].lstrip("\n")
    return fm, body


def _validate_meta(fm: dict, source_dir: Path) -> SkillMeta:
    """校验 frontmatter 字典并返回 SkillMeta。

    检查项：name 必填且格式合法、description 必填、
    mode 可选值、fork_context 可选值。
    """
    name = fm.get("name")
    if not name or not isinstance(name, str):
        raise SkillParseError("frontmatter 缺少必填字段: name")
    name = name.strip().lower()
    if not _NAME_RE.match(name):
        raise SkillParseError(
            f"skill name 格式无效: {name!r}，必须匹配 ^[a-z][a-z0-9\\-]*$"
        )

    description = fm.get("description")
    if not description or not isinstance(description, str):
        raise SkillParseError("frontmatter 缺少必填字段: description")
    description = description.strip()

    allowed_tools = fm.get("allowed_tools", [])
    if not isinstance(allowed_tools, list):
        raise SkillParseError("allowed_tools 必须是列表")

    mode = fm.get("mode", "inline")
    if mode not in ("inline", "fork"):
        logger.warning("skill %r mode=%r 不是 inline/fork，回退为 inline", name, mode)
        mode = "inline"

    fork_context = fm.get("context", "none")
    if fork_context not in ("full", "recent", "none"):
        logger.warning(
            "skill %r context=%r 不是 full/recent/none，回退为 none", name, fork_context
        )
        fork_context = "none"

    model = fm.get("model")
    if model is not None and not isinstance(model, str):
        model = None

    return SkillMeta(
        name=name,
        description=description,
        allowed_tools=allowed_tools,
        mode=mode,
        fork_context=fork_context,
        model=model,
    )


def parse_skill_dir(dir_path: Path, source: SkillSource) -> Skill:
    """解析一个 skill 目录，读取 SKILL.md 并返回 Skill 对象。"""
    skill_md = dir_path / "SKILL.md"
    if not skill_md.is_file():
        raise SkillParseError(f"目录中没有 SKILL.md: {dir_path}")

    raw = skill_md.read_text(encoding="utf-8")
    fm, body = parse_frontmatter_and_body(raw)
    meta = _validate_meta(fm, dir_path)

    return Skill(
        meta=meta,
        prompt_body=body.strip(),
        source_dir=dir_path,
        source=source,
    )
