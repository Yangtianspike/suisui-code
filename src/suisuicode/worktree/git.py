"""Git helper：子进程调用、变更检测、快速恢复 SHA。

所有 git 操作通过 ``asyncio.create_subprocess_exec`` 执行，
注入 ``GIT_TERMINAL_PROMPT=0`` + ``GIT_ASKPASS=""`` 防止阻塞式提示。
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path


def _git_env() -> dict[str, str]:
    """构造 git 安全环境变量（阻止交互式提示）。"""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = ""
    return env


async def _run_git(work_dir: str, *args: str) -> str:
    """在 work_dir 下异步执行 git 命令，返回 stdout（去尾换行）。

    Raises:
        RuntimeError: git 返回非零退出码（stderr 作为错误消息）。
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=work_dir,
        env=_git_env(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode("utf-8", errors="replace").rstrip("\n")
    stderr = stderr_b.decode("utf-8", errors="replace").rstrip("\n")

    if proc.returncode != 0:
        raise RuntimeError(stderr or f"git exited with code {proc.returncode}")

    return stdout


async def _has_worktree_changes(wt_path: str, base_commit: str) -> bool:
    """检测 Worktree 是否有未提交修改或本地多于 base 的 commit。

    fail-closed：任何异常返回 True（宁可保留）。
    """
    try:
        # ① 未提交修改（工作区 / 暂存区）
        status = await _run_git(wt_path, "status", "--porcelain")
        if status.strip():
            return True

        # ② 本地多于 base 的 commit 数
        count_str = await _run_git(
            wt_path, "rev-list", "--count", f"{base_commit}..HEAD"
        )
        if int(count_str) > 0:
            return True

        return False
    except Exception:
        # fail-closed：git 本身出错宁可保留
        return True


def _resolve_head_sha_from_fs(wt_path: str) -> str | None:
    """纯文件系统读取 Worktree HEAD 的 commit SHA。

    不调任何 git 子进程，毫秒级返回用于快速恢复路径。

    步骤：
    1. 读 ``<wt_path>/.git``（Worktree 的 .git 是指向主仓库的文本文件）
    2. 读 ``<gitdir>/HEAD``，若是符号引用则通过 ``commondir`` 解析到
       主仓库的 refs，读对应的 ``refs/heads/<name>``
    3. 返回 commit SHA 或 None
    """
    try:
        git_file = Path(wt_path) / ".git"
        if not git_file.exists():
            return None

        raw = git_file.read_text(encoding="utf-8").strip()
        # 格式：gitdir: <path>
        if not raw.startswith("gitdir:"):
            # 可能是主仓库（.git 是目录）
            if git_file.is_dir():
                head_file = git_file / "HEAD"
                if not head_file.exists():
                    return None
                head = head_file.read_text(encoding="utf-8").strip()
                if head.startswith("ref:"):
                    ref_path_str = head[len("ref:"):].strip()
                    ref_path = git_file / ref_path_str
                    if not ref_path.exists():
                        return None
                    sha = ref_path.read_text(encoding="utf-8").strip()
                    return sha if sha else None
                else:
                    return head if head else None
            return None

        gitdir = raw[len("gitdir:"):].strip()
        gitdir_path = Path(gitdir)

        # 读 HEAD
        head_file = gitdir_path / "HEAD"
        if not head_file.exists():
            return None
        head = head_file.read_text(encoding="utf-8").strip()

        if head.startswith("ref:"):
            # 符号引用 → 读取 commondir 找到主仓库 .git 目录，
            # 再在主仓库的 refs 下解析引用路径
            ref_path_str = head[len("ref:"):].strip()

            # 先尝试 commondir
            commondir_file = gitdir_path / "commondir"
            if commondir_file.exists():
                common_rel = commondir_file.read_text(encoding="utf-8").strip()
                main_gitdir = (gitdir_path / common_rel).resolve()
            else:
                # 兜底：gitdir_path 的父目录的父目录（即 .git）
                main_gitdir = gitdir_path.parent.parent

            ref_path = main_gitdir / ref_path_str
            if not ref_path.exists():
                # 再尝试 packed-refs
                packed = main_gitdir / "packed-refs"
                if packed.exists():
                    for line in packed.read_text(encoding="utf-8").splitlines():
                        if line.startswith("#"):
                            continue
                        parts = line.strip().split()
                        if len(parts) >= 2 and parts[1] == ref_path_str:
                            return parts[0]
                return None
            sha = ref_path.read_text(encoding="utf-8").strip()
            return sha if sha else None
        else:
            # detached HEAD — 本身就是 SHA
            return head if head else None
    except Exception:
        return None
