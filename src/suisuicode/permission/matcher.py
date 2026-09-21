"""Matcher Protocol 与四种匹配实现（exact / glob / regex / not）。

将原 `rule.py` 中单一 glob 匹配扩展为四种结构化匹配类型，
供权限规则（ch06）与 Hook 条件表达式（ch12）共用。

Usage::

    from suisuicode.permission.matcher import compile_matcher

    m = compile_matcher("=git", is_command=True)
    assert m.match("git")       # exact hit
    assert not m.match("git -v")

    m = compile_matcher("~^npm\\s+(install|test)$", is_command=False)
    assert m.match("npm install")

    m = compile_matcher("!~^rm", is_command=True)
    assert m.match("ls -lh")    # not rm
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


# ── Matcher Protocol ──────────────────────────────────────


class Matcher(Protocol):
    """规则匹配的统一接口。

    四种实现：
    - :class:`ExactMatcher` — 整串相等 ``=value``
    - :class:`GlobMatcher`  — 通配（无前缀，沿用现有语义）
    - :class:`RegexMatcher` — 正则 ``~regex``
    - :class:`NotMatcher`   — 反向 ``!inner``
    """

    def match(self, s: str) -> bool: ...
    def __str__(self) -> str: ...


# ── Four Implementations ─────────────────────────────────


@dataclass(frozen=True)
class ExactMatcher:
    """精确匹配：``=value``，整串相等比较。

    >>> m = ExactMatcher("git status")
    >>> m.match("git status")
    True
    >>> m.match("git status -s")
    False
    """

    value: str

    def match(self, s: str) -> bool:
        return s == self.value

    def __str__(self) -> str:
        return f"={self.value}"


@dataclass(frozen=True)
class GlobMatcher:
    """通配匹配：无前缀，沿用现有 wildcard / path glob 语义。

    *is_command=True* 时走命令 glob（``*`` 匹配任意字符含空格，``**`` 等价 ``*``）。
    *is_command=False* 时走文件路径 glob（``*`` 段内不含 ``/``，``**`` 跨段）。

    >>> m = GlobMatcher("git *", is_command=True)
    >>> m.match("git status")
    True
    >>> m.match("npm test")
    False
    """

    pattern: str
    is_command: bool

    def match(self, s: str) -> bool:
        if self.is_command:
            return _match_command_glob(self.pattern, s)
        return _match_path_glob(self.pattern, s)

    def __str__(self) -> str:
        return self.pattern


@dataclass(frozen=True)
class RegexMatcher:
    """正则匹配：``~regex``，用 ``re.Pattern.search`` 检测。

    编译在构造期由 ``compile_matcher`` 完成并缓存，运行期复用。

    >>> m = RegexMatcher(r"^npm (install|test)$", re.compile(r"^npm (install|test)$"))
    >>> m.match("npm install")
    True
    >>> m.match("npm run dev")
    False
    """

    src: str
    compiled: re.Pattern[str]

    def match(self, s: str) -> bool:
        return self.compiled.search(s) is not None

    def __str__(self) -> str:
        return f"~{self.src}"


@dataclass(frozen=True)
class NotMatcher:
    """反向匹配：``!inner``，对内层 matcher 取反。

    ``!`` 是一元运算，支持嵌套：
    ``!=value`` → ``NotMatcher(ExactMatcher("value"))``
    ``!~regex`` → ``NotMatcher(RegexMatcher(...))``
    ``!glob``   → ``NotMatcher(GlobMatcher(...))``

    >>> m = NotMatcher(ExactMatcher("rm -rf"))
    >>> m.match("rm -rf")
    False
    >>> m.match("ls")
    True
    """

    inner: Matcher

    def match(self, s: str) -> bool:
        return not self.inner.match(s)

    def __str__(self) -> str:
        return f"!{self.inner}"


# ── Factory ───────────────────────────────────────────────


def compile_matcher(pattern: str, *, is_command: bool) -> Matcher:
    """解析单条匹配描述串，返回对应的 Matcher 实例。

    描述串规则（单字符前缀）：:

        "=value"  → ExactMatcher("value")
        "~regex"  → RegexMatcher("regex", re.compile("regex"))
        "!inner"  → NotMatcher(compile_matcher(inner, ...))
        "value"   → GlobMatcher("value", is_command)

    Args:
        pattern: 匹配描述串。
        is_command: **仅对无前缀（glob）生效**。True 走命令整串通配、
                    False 走文件路径 glob。hook 上下文统一传 False。

    Returns:
        匹配器实例。

    Raises:
        ValueError: pattern 为空串或正则编译失败。
    """
    if not pattern:
        raise ValueError("empty matcher pattern")

    head, rest = pattern[0], pattern[1:]

    if head == "=":
        return ExactMatcher(rest)
    if head == "~":
        try:
            return RegexMatcher(rest, re.compile(rest))
        except re.error as e:
            raise ValueError(f"invalid regex: {e}") from e
    if head == "!":
        return NotMatcher(compile_matcher(rest, is_command=is_command))
    # 无前缀 → glob（向后兼容）
    return GlobMatcher(pattern, is_command)


# ── Internal: glob matchers ───────────────────────────────


def _match_command_glob(pattern: str, target: str) -> bool:
    """命令 glob：``*`` 匹配任意字符（含空格），``**`` 等价 ``*``。

    ``git *`` 命中 ``git status``、``git push origin main``。
    """
    escaped = re.escape(pattern)
    regex_str = escaped.replace(r"\*\*", ".*").replace(r"\*", ".*")
    regex_str = "^" + regex_str + "$"
    return bool(re.fullmatch(regex_str, target, re.DOTALL))


def _match_path_glob(pattern: str, target: str) -> bool:
    """文件路径 glob：``*`` 匹配段内任意字符（不含 ``/``），``**`` 跨段。

    ``**/*.py`` 命中 ``src/main.py``、``tests/test_foo.py``。
    """
    placeholder = "\x00DOUBLESTAR\x00"
    escaped = re.escape(pattern)
    escaped = escaped.replace(re.escape("**"), placeholder)
    escaped = escaped.replace(r"\*", r"[^/]*")
    escaped = escaped.replace(placeholder, r".*")
    regex_str = "^" + escaped + "$"
    return bool(re.fullmatch(regex_str, target))
