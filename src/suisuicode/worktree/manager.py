"""Manager 类型：Worktree 生命周期管理中心。

持有 active 映射、锁、session 状态、目录路径。
create / enter / exit / remove / auto_cleanup / sweep_stale
的具体实现按 task 分解到 create.py / lifecycle.py / sweep.py。
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from suisuicode.worktree.create import Worktree

from suisuicode.worktree.session import (
    WorktreeSession,
    clear_session,
    load_session,
)

# 默认软链目录列表
DEFAULT_SYMLINK_DIRS: list[str] = ["node_modules", ".venv", "vendor"]


# Worktree 类型用前向引用，定义在 create.py
class Manager:
    """Worktree 管理器。

    ``repo_root`` 必须是 git 仓库根目录（``git rev-parse --show-toplevel`` 验证）。
    内部所有状态变更受 ``asyncio.Lock`` 保护。
    """

    def __init__(self, repo_root: str) -> None:
        # 解析为绝对路径
        self.repo_root: str = str(Path(repo_root).resolve())

        # 验证是 git 仓库根目录
        result = subprocess.run(
            ["git", "-C", self.repo_root, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError(
                f"不是有效的 git 仓库: {repo_root}（git rev-parse 失败）"
            )
        git_root = result.stdout.strip()
        git_root_resolved = str(Path(git_root).resolve())
        if git_root_resolved != self.repo_root:
            raise ValueError(
                f"{repo_root!r} 不是 git 仓库根目录，根目录为 {git_root!r}"
            )

        # 目录与文件路径
        self.worktree_dir: Path = Path(self.repo_root) / ".suisuicode" / "worktrees"
        self.session_file: Path = Path(self.repo_root) / ".suisuicode" / "worktree_session.json"

        # 并发保护
        self.lock: asyncio.Lock = asyncio.Lock()

        # 活跃 Worktree 映射 name → Worktree
        self.active: dict[str, "Worktree"] = {}
        self.current_session: WorktreeSession | None = None

        # 软链目录配置
        self.symlink_dirs: list[str] = list(DEFAULT_SYMLINK_DIRS)

        # 创建 worktree_dir
        self.worktree_dir.mkdir(parents=True, exist_ok=True)

        # 恢复 session
        try:
            session = load_session(self.session_file)
        except Exception as exc:
            print(
                f"worktree: session 文件损坏，已清空: {exc}",
                file=sys.stderr,
            )
            clear_session(self.session_file)
            session = None

        if session is not None and not Path(session.worktree_path).exists():
            print(
                f"worktree: session worktree 目录已不存在 ({session.worktree_path})，已清空",
                file=sys.stderr,
            )
            clear_session(self.session_file)
            session = None

        self.current_session = session

        # 扫描已存在的 worktree 目录 → active（快速恢复）
        self._scan_active()

    # ── 查询方法 ──────────────────────────────────────

    def list(self) -> list["Worktree"]:
        """返回按 name 排序的 Worktree 列表。"""
        return sorted(self.active.values(), key=lambda wt: wt.name)

    def get(self, name: str) -> "Worktree | None":
        """按 name 查找 Worktree。"""
        return self.active.get(name)

    def session(self) -> WorktreeSession | None:
        """返回当前 session（兼容属性名）。"""
        return self.current_session

    # ── 内部方法 ──────────────────────────────────────

    def _scan_active(self) -> None:
        """扫描 worktree_dir 子目录，用快速恢复路径填充 active 映射。"""
        from suisuicode.worktree.git import _resolve_head_sha_from_fs

        if not self.worktree_dir.exists():
            return

        for child in self.worktree_dir.iterdir():
            if not child.is_dir():
                continue
            # 还原 slug（flat_slug 的反向：+ → /）
            flat_name = child.name
            name = flat_name.replace("+", "/")

            # 跳过已存在的
            if name in self.active:
                continue

            sha = _resolve_head_sha_from_fs(str(child))
            if sha is None:
                continue

            # 反转 flat_slug 得 name
            branch = f"worktree-{flat_name}"

            # 用 Worktree 构造（延迟导入避免循环）
            from suisuicode.worktree.create import Worktree

            wt = Worktree(
                name=name,
                path=str(child),
                branch=branch,
                based_on="HEAD",
                head_commit=sha,
                created=datetime.fromtimestamp(child.stat().st_mtime),
                manual=False,  # 扫描恢复的默认为自动创建的
            )
            self.active[name] = wt
