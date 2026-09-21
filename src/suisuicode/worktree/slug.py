"""Slug 校验：validate_slug + flat_slug。

规则（spec F1）：
- 非空，总长度 ≤ 64
- 按 ``/`` 切段，每段匹配 ``^[a-zA-Z0-9._-]+$`` 且不能是 ``.`` 或 ``..``
- 不允许连续 ``//``、首末 ``/``
- 失败抛 ``ValueError`` 携带具体原因
"""

from __future__ import annotations

import re

# 每段必须匹配：字母数字 + 点号、下划线、短横
_SEGMENT_RE = re.compile(r"^[a-zA-Z0-9._-]+$")

MAX_SLUG_LENGTH: int = 64


def validate_slug(name: str) -> None:
    """校验 Worktree slug 名，失败抛 ValueError(具体原因)。"""
    if not name:
        raise ValueError("slug 不能为空")

    if len(name) > MAX_SLUG_LENGTH:
        raise ValueError(
            f"slug 长度不能超过 {MAX_SLUG_LENGTH} 个字符，当前 {len(name)} 字符"
        )

    # 检查首末 /
    if name.startswith("/"):
        raise ValueError("slug 不能以 '/' 开头")
    if name.endswith("/"):
        raise ValueError("slug 不能以 '/' 结尾")

    # 检查连续 //
    if "//" in name:
        raise ValueError("slug 不能包含连续的 '//'")

    # 按 / 切段校验
    segments = name.split("/")
    for seg in segments:
        if not seg:
            raise ValueError("slug 段不能为空")
        if seg == "." or seg == "..":
            raise ValueError(f"slug 段不能为 '.' 或 '..'，当前段: {seg!r}")
        if not _SEGMENT_RE.match(seg):
            raise ValueError(
                f"slug 段包含非法字符: {seg!r}，"
                f"只允许字母、数字、点号、下划线、短横"
            )


def flat_slug(name: str) -> str:
    """将嵌套 slug 的 ``/`` 替换为 ``+``，避免 Git D/F 冲突。"""
    return name.replace("/", "+")
