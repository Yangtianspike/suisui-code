"""Agent 定义文件解析：frontmatter 分离、字段校验、Definition 构造。

与 skills/parser.py 独立实现以避免循环依赖，核心逻辑几乎一致。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

from suisuicode.permission import Mode as PermissionMode, parse_mode as _parse_perm_mode
from suisuicode.subagent.definition import Definition, Source

# name 允许大小写字母开头 + 字母数字连字符下划线，长度 1-32
AGENT_NAME_REGEX = re.compile(r"^[A-Za-z][A-Za-z0-9\-_]{0,31}$")

# frontmatter 分隔符
_FM_SEP = re.compile(r"^---\s*$", re.MULTILINE)

# UTF-8 BOM
UTF8_BOM = b"\xef\xbb\xbf"

VALID_MODELS = {"", "inherit", "haiku", "sonnet", "opus"}


class AgentParseError(Exception):
    """Agent 定义解析失败时抛出。"""


def parse_frontmatter_and_body(raw: str) -> tuple[dict, str]:
    """从原始 .md 文本分离 frontmatter 与正文。

    frontmatter 由首尾各一行的 ``---`` 界定。
    返回 ``(frontmatter_dict, body)``。
    """
    matches = list(_FM_SEP.finditer(raw))
    if len(matches) < 2:
        raise AgentParseError("缺少 frontmatter 分隔符（需要开头的 --- 与结尾的 ---）")

    first = matches[0]
    prefix = raw[: first.start()]
    if prefix.strip():
        raise AgentParseError("frontmatter 的起始 --- 之前不应有非空白内容")

    second = matches[1]
    fm_text = raw[first.end() : second.start()]

    try:
        fm = yaml.safe_load(fm_text)
    except yaml.YAMLError as e:
        raise AgentParseError(f"frontmatter YAML 解析失败: {e}") from e

    if not isinstance(fm, dict):
        raise AgentParseError("frontmatter 必须是 YAML mapping")

    body = raw[second.end() :].lstrip("\n")
    return fm, body


def parse_definition(
    data: bytes,
    file_path: str,
    source: Source,
) -> Definition:
    """从原始字节解析一个 Agent 定义。

    Args:
        data: .md 文件原始字节
        file_path: 文件路径（用于错误消息和调试）
        source: 定义来源

    Returns:
        Definition 实例

    Raises:
        AgentParseError: name 或 description 缺失/不合法时
    """
    # 去 BOM
    if data.startswith(UTF8_BOM):
        data = data[len(UTF8_BOM) :]

    raw = data.decode("utf-8")
    fm, body = parse_frontmatter_and_body(raw)

    # ── 字段提取 ──
    raw_name = fm.get("name")
    name = str(raw_name).strip() if raw_name is not None else ""
    raw_desc = fm.get("description")
    description = str(raw_desc).strip() if raw_desc is not None else ""
    tools = _as_str_list(fm.get("tools"))
    disallowed_tools = _as_str_list(fm.get("disallowedTools"))
    model_str = str(fm.get("model", "")).strip()
    max_turns = int(fm.get("maxTurns") or 0)
    permission_mode_str = str(fm.get("permissionMode") or "").strip()
    isolation_str = str(fm.get("isolation") or "").strip()
    background = bool(fm.get("background") or False)
    dont_ask = False
    permission_mode = PermissionMode.DEFAULT

    # ── 必填校验 ──
    if not name:
        raise AgentParseError(f"{file_path}: frontmatter 缺少必填字段: name")
    if not AGENT_NAME_REGEX.match(name):
        raise AgentParseError(
            f"{file_path}: agent name 格式无效: {name!r}，"
            f"必须匹配 {AGENT_NAME_REGEX.pattern}"
        )
    if not description:
        raise AgentParseError(f"{file_path}: frontmatter 缺少必填字段: description")

    # ── model 校验 ──
    if not model_str:
        model_str = "inherit"
    elif model_str not in VALID_MODELS:
        print(
            f"subagent {name}: unknown model {model_str!r}, defaulting to inherit",
            file=sys.stderr,
        )
        model_str = "inherit"

    # ── permissionMode 解析 ──
    if permission_mode_str:
        if permission_mode_str == "dontAsk":
            dont_ask = True
            permission_mode = PermissionMode.DEFAULT
        else:
            pm, ok = _parse_perm_mode(permission_mode_str)
            if ok:
                permission_mode = pm
            else:
                print(
                    f"subagent {name}: unknown permissionMode {permission_mode_str!r}, "
                    f"defaulting to default",
                    file=sys.stderr,
                )
                permission_mode = PermissionMode.DEFAULT

    # isolation 校验
    if isolation_str and isolation_str not in ("", "worktree"):
        print(
            f"subagent {name}: unknown isolation {isolation_str!r}, "
            f"defaulting to empty string",
            file=sys.stderr,
        )
        isolation_str = ""

    return Definition(
        name=name,
        description=description,
        tools=tools,
        disallowed_tools=disallowed_tools,
        model=model_str,
        max_turns=max_turns,
        permission_mode=permission_mode,
        dont_ask=dont_ask,
        background=background,
        isolation=isolation_str,
        system_prompt=body.strip(),
        file_path=file_path,
        source=source,
    )


def parse_file(path: str, source: Source) -> Definition:
    """读取一个 .md 文件并解析为 Definition。"""
    data = Path(path).read_bytes()
    return parse_definition(data, path, source)


def _as_str_list(val: object) -> list[str]:
    """安全转为字符串列表。"""
    if val is None:
        return []
    if isinstance(val, list):
        return [str(x) for x in val]
    return []
