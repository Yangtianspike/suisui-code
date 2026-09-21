"""规则引擎：allow/deny 规则解析、Matcher 匹配、RuleSet 判定（F3）。

ch12 升级：Pattern 从单一字符串扩展为结构化 :class:`Matcher` 四种类型。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from suisuicode.permission import Decision
from suisuicode.permission.matcher import (
    Matcher,
    compile_matcher,
)


@dataclass
class Rule:
    """单条规则。

    ch12 升级：``matcher`` 替代原 ``pattern`` 字符串，支持 exact/regex/not/glob。
    ``matcher is None`` 表示该工具全匹配。
    """

    tool: str = ""  # 友好名：Bash/Read/Write/Edit/Glob/Grep
    matcher: Matcher | None = None  # None 表示匹配该工具全部调用
    raw: str = ""  # 原始模式串，仅供错误日志与调试
    allow: bool = True  # True=allow, False=deny


@dataclass
class RuleSet:
    """一个配置层级的规则集合。"""

    allow: list[Rule] = field(default_factory=list)
    deny: list[Rule] = field(default_factory=list)

    def match(self, friendly: str, target: str) -> tuple[Decision, bool]:
        """先 deny 再 allow；命中返回 (Decision, True)，否则 (ALLOW, False)。

        Args:
            friendly: 友好名（Bash/Read/Write/Edit/Glob/Grep）。
            target: 命令串（Bash）或项目相对路径（文件类工具）。
        """
        for r in self.deny:
            if r.tool == friendly and _match_rule(r, target):
                return Decision.DENY, True

        for r in self.allow:
            if r.tool == friendly and _match_rule(r, target):
                return Decision.ALLOW, True

        return Decision.ALLOW, False


def _match_rule(r: Rule, target: str) -> bool:
    """检查 target 是否匹配 Rule 的 matcher。

    ``matcher is None`` → 全匹配（该工具下所有调用皆命中）。
    """
    if r.matcher is None:
        return True
    return r.matcher.match(target)


# ── parse_rule ────────────────────────────────────────────


def parse_rule(s: str) -> tuple[Rule | None, str | None]:
    """解析一条规则字符串，支持四种语法。

    格式：``Tool(描述串)`` 或 ``Tool``（全匹配）。

    描述串语法（单字符前缀）：::

        "=value"  → 精确（整串相等）
        "~regex"  → 正则
        "!inner"  → 反向（对内层取反）
        "value"   → glob 通配（无前缀，向后兼容）

    Examples::

        "Bash(=git status)"      → 精确匹配 "git status"
        "Bash(~^npm (install|test)$)" → 正则匹配
        "Bash(!~^rm)"            → 反向正则，不以 rm 开头
        "Write(**/*.py)"         → 文件 glob（向后兼容）
        "Bash"                   → Bash 工具全匹配

    Returns:
        ``(Rule, None)`` 成功；``(None, error_string)`` 失败。
    """
    s = s.strip()
    if not s:
        return None, "empty rule string"

    # 找括号
    lparen = s.find("(")
    if lparen == -1:
        # 无括号：Tool 全匹配
        tool = s.strip()
        if not tool:
            return None, "empty tool name"
        return Rule(tool=tool, matcher=None, raw=s, allow=True), None

    if not s.endswith(")"):
        return None, "unmatched parenthesis"

    tool = s[:lparen].strip()
    pattern = s[lparen + 1 : -1]  # 去掉首尾括号
    if not tool:
        return None, "empty tool name"

    # 空 pattern → 全匹配（如 "Bash()"）
    if not pattern.strip():
        return Rule(tool=tool, matcher=None, raw=s, allow=True), None

    # 编译 matcher
    is_command = tool == "Bash"
    try:
        m = compile_matcher(pattern.strip(), is_command=is_command)
    except ValueError as e:
        return None, str(e)

    return Rule(tool=tool, matcher=m, raw=s, allow=True), None


# ── 向后兼容的 match_pattern（供外部已废弃的调用方） ────


def match_pattern(pattern: str, target: str, is_command: bool = False) -> bool:
    """glob 模式匹配（向后兼容包装）。

    内部转为 :class:`GlobMatcher` 并委托 ``matcher.match(target)``。
    推荐新代码直接使用 :func:`compile_matcher` + ``matcher.match(target)``。

    Args:
        pattern: glob 模式串。
        target: 待匹配目标串。
        is_command: True 时用命令 glob（``*`` 匹配任意字符含空格）；
                    False 时用文件路径 glob（``*`` 段内不含 ``/``，``**`` 跨段）。

    Returns:
        True 如果 target 匹配 pattern。
    """
    if pattern == "":
        return True  # 空模式匹配一切
    try:
        m = compile_matcher(pattern, is_command=is_command)
        return m.match(target)
    except ValueError:
        return False


# ── 保留的底层 glob 函数（供 matcher.py 引用或旧测试） ──


def _match_command_glob(pattern: str, target: str) -> bool:
    """命令 glob：``*`` 匹配任意字符（含空格），``**`` 等价 ``*``。"""
    escaped = re.escape(pattern)
    regex_str = escaped.replace(r"\*\*", ".*").replace(r"\*", ".*")
    regex_str = "^" + regex_str + "$"
    return bool(re.fullmatch(regex_str, target, re.DOTALL))


def _match_path_glob(pattern: str, target: str) -> bool:
    """文件路径 glob：``*`` 匹配段内任意字符，``**`` 匹配跨段。"""
    placeholder = "\x00DOUBLESTAR\x00"
    escaped = re.escape(pattern)
    escaped = escaped.replace(re.escape("**"), placeholder)
    escaped = escaped.replace(r"\*", r"[^/]*")
    escaped = escaped.replace(placeholder, r".*")
    regex_str = "^" + escaped + "$"
    return bool(re.fullmatch(regex_str, target))
