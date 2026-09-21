"""过期 Worktree 清理：sweep_stale + random_agent_name。

三层过滤：
1. 名字匹配 ``agent-a[0-9a-f]{7}``
2. 目录 mtime 超过 cutoff 才考虑；跳过当前 session
3. 有未提交修改 / 未推送 commit 的保留（fail-closed）
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from suisuicode.worktree.manager import Manager

# 临时 Worktree 命名模式（spec F33）
EPHEMERAL_PATTERN = re.compile(r"^agent-a[0-9a-f]{7}$")


def random_agent_name() -> str:
    """生成 SubAgent 临时 Worktree 名：``agent-a`` + 7 位随机 hex。"""
    return "agent-a" + secrets.token_hex(4)[:7]


async def sweep_stale(self: Manager, cutoff: datetime) -> list[str]:
    """清理过期的临时 Worktree，返回被删除的 name 列表。

    三层过滤：
    1. 名字只匹配 ``agent-a[0-9a-f]{7}``
    2. 目录 mtime > cutoff 跳过；当前 session 的 worktree 跳过
    3. 有未提交修改或未推送 commit 跳过（fail-closed）
    """
    from suisuicode.worktree.git import _has_worktree_changes, _run_git
    from suisuicode.worktree.lifecycle import ExitOptions, remove

    removed: list[str] = []

    if not self.worktree_dir.exists():
        return removed

    for child in self.worktree_dir.iterdir():
        if not child.is_dir():
            continue

        flat_name = child.name

        # 第一层：名字匹配
        if not EPHEMERAL_PATTERN.match(flat_name):
            continue

        # 第二层：时间过滤 + 跳过当前 session
        mtime = datetime.fromtimestamp(child.stat().st_mtime)
        if mtime > cutoff:
            continue
        if (
            self.current_session is not None
            and self.current_session.worktree_path == str(child)
        ):
            continue

        # 第三层：变更检查
        try:
            # 有未提交修改 → 跳过
            has_changes = await _has_worktree_changes(str(child), "HEAD")
            if has_changes:
                continue

            # 有未推送 commit → 跳过
            unpushed = await _run_git(
                str(child),
                "rev-list", "--max-count=1", "HEAD", "--not", "--remotes",
            )
            if unpushed.strip():
                continue
        except Exception:
            # fail-closed：出错宁可保留
            continue

        # 通过三层 → 删除
        name = flat_name.replace("+", "/")  # 反转 flat_slug
        try:
            await remove(self, name, ExitOptions(discard_changes=True))
        except Exception:
            continue
        removed.append(name)

    return removed
