"""配置加载、工具名映射、类别判定、参数提取（F4/F5）。"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from suisuicode.llm import ToolCall
from suisuicode.permission import Category
from suisuicode.permission.rule import RuleSet, parse_rule


class SettingsError(Exception):
    """配置文件解析错误（调用方据此降级，N5）。"""

    pass


@dataclass
class PermissionsBlock:
    """单层配置的权限块。"""

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass
class Settings:
    """单个 YAML 文件的权限配置结构。"""

    default_mode: str = ""
    permissions: PermissionsBlock = field(default_factory=PermissionsBlock)


def load_settings(path: str) -> Settings:
    """从 YAML 文件加载权限配置。

    文件不存在 → 空 Settings。
    YAML 解析失败 → 抛 SettingsError（调用方降级跳过该文件）。
    """
    p = Path(path)
    if not p.is_file():
        return Settings()

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise SettingsError(f"YAML 解析失败: {path}: {e}") from e

    if not isinstance(raw, dict):
        return Settings()

    permissions_raw = raw.get("permissions", {})
    if not isinstance(permissions_raw, dict):
        permissions_raw = {}

    return Settings(
        default_mode=str(raw.get("default_mode", "")).strip(),
        permissions=PermissionsBlock(
            allow=_as_str_list(permissions_raw.get("allow", [])),
            deny=_as_str_list(permissions_raw.get("deny", [])),
        ),
    )


def _as_str_list(v: object) -> list[str]:
    """安全地把 YAML 值转为字符串列表；非列表返回空。"""
    if not isinstance(v, list):
        return []
    return [str(item).strip() for item in v if str(item).strip()]


def to_rule_set(s: Settings) -> RuleSet:
    """将 Settings 转为 RuleSet（allow 条 allow=True，deny 条 allow=False）。

    非法规则条目（parse_rule 失败）打印 stderr 后跳过，其它规则正常加载。
    """
    rule_set = RuleSet()

    for raw in s.permissions.deny:
        r, err = parse_rule(raw)
        if err is not None:
            print(f"rule {raw!r} parse failed: {err}", file=sys.stderr)
            continue
        if r is not None:
            r.allow = False
            rule_set.deny.append(r)

    for raw in s.permissions.allow:
        r, err = parse_rule(raw)
        if err is not None:
            print(f"rule {raw!r} parse failed: {err}", file=sys.stderr)
            continue
        if r is not None:
            r.allow = True
            rule_set.allow.append(r)

    return rule_set


# ── 友好名映射 ───────────────────────────────────────────

_FRIENDLY_MAP: dict[str, str] = {
    "bash": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "glob": "Glob",
    "grep": "Grep",
}


def friendly_name(internal: str) -> str:
    """内部工具名 → 友好名；未知原样返回。"""
    return _FRIENDLY_MAP.get(internal, internal)


def categorize(internal: str, read_only: bool) -> Category:
    """判定工具类别（用于模式兜底矩阵）。

    read_only 为 True → Category.READ（优先）。
    否则：write_file/edit_file → WRITE；其余（含 bash、未知）→ EXEC（N7 最严）。
    """
    if read_only:
        return Category.READ
    if internal in ("write_file", "edit_file"):
        return Category.WRITE
    # bash 及其他未知工具 → EXEC
    return Category.EXEC


def extract_target(call: ToolCall) -> tuple[str, bool, bool]:
    """从一次工具调用中提取匹配目标串。

    Returns:
        (target, is_file, ok)
        - target: 命令串（bash）或路径（文件类工具）
        - is_file: True 表示文件类工具（走沙箱），False 表示命令（走黑名单）
        - ok: False 表示解析失败 / 缺必填字段
    """
    raw = call.input
    if isinstance(raw, str):
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return ("", False, False)
    elif isinstance(raw, dict):
        d = raw
    else:
        return ("", False, False)

    if not isinstance(d, dict):
        return ("", False, False)

    name = call.name

    # 文件读写工具 → 取 path
    if name in ("read_file", "write_file", "edit_file"):
        path_val = d.get("path")
        if path_val is None or not isinstance(path_val, str):
            return ("", True, False)  # 文件类工具缺 path → ok=False
        return (path_val, True, True)

    # glob/grep → 取 path（搜索根目录），空 → "."
    if name in ("glob", "grep"):
        path_val = d.get("path", ".")
        if path_val is None or (isinstance(path_val, str) and path_val.strip() == ""):
            path_val = "."
        return (str(path_val), True, True)

    # bash → 取 command
    if name == "bash":
        cmd = d.get("command")
        if cmd is None or not isinstance(cmd, str):
            return ("", False, False)  # 缺 command → ok=False
        return (cmd, False, True)

    # 未知工具
    return ("", False, False)
