"""危险命令黑名单（内置、不可配置、不可绕过，N1）。

用一组编译好的正则表达式在执行前拦截已知高危命令。
这是**启发式防御**而非完备保证，明确不追求穷尽所有危险命令。
"""

from __future__ import annotations

import re

# ── 内置危险命令正则集 ──────────────────────────────────
# 注意：均为启发式规则，非完备保证；不可配置、不可关闭（含 bypassPermissions）。
_BLACKLIST: list[re.Pattern] = [
    # 递归强删根 / 家目录 / 通配根
    re.compile(r"rm\s+(-[-a-zA-Z]*[rf][-a-zA-Z]*\s*)+(/|~|\$HOME|/\*)", re.IGNORECASE),
    # 递归强删根变体（--recursive --force /）
    re.compile(r"rm\s+--recursive.*--force.*\s+(/|~|\$HOME)", re.IGNORECASE),
    # dd 写块设备
    re.compile(r"dd\s+.*of=/dev/[a-z]+", re.IGNORECASE),
    # fork bomb
    re.compile(r":\(\)\s*\{[^}]*\|[^}]*&\s*\}", re.IGNORECASE),
    # mkfs 格式化
    re.compile(r"mkfs\.", re.IGNORECASE),
    # 重定向覆盖磁盘设备
    re.compile(r">\s*/dev/(sd[a-z]|hd[a-z]|nvme|disk)", re.IGNORECASE),
    # chmod -R 777 /（递归改权限到根）
    re.compile(r"chmod\s+-R\s+0?777\s+/", re.IGNORECASE),
]


def hits_blacklist(command: str) -> bool:
    """任意黑名单正则命中即返回 True。"""
    return any(p.search(command) for p in _BLACKLIST)
