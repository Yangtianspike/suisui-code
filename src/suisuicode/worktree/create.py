"""Manager.create + 快速恢复 + 创建后四类设置 (A/B/C/D)。

create 入口挂到 Manager 类上（运行时 monkey-patch 或在此文件 def 后绑定）。
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from suisuicode.worktree.slug import flat_slug, validate_slug

if TYPE_CHECKING:
    from suisuicode.worktree.manager import Manager


# ── Worktree dataclass ──────────────────────────────────


@dataclass
class Worktree:
    """单个 Worktree 的元信息（spec F2）。"""

    name: str  # 原始 slug（可能含 /）
    path: str  # 绝对路径
    branch: str  # worktree-<flat_slug>
    based_on: str  # 创建时的 base 引用（HEAD / SHA）
    head_commit: str  # 创建时的 commit SHA
    created: datetime
    manual: bool  # True=用户手动创建


# ── create ──────────────────────────────────────────────


async def create(self: Manager, name: str, base_ref: str, manual: bool) -> Worktree:
    """创建（或快速恢复）一个 Worktree。

    Raises:
        ValueError: slug 非法 或 name 已存在
        RuntimeError: git worktree add 失败
    """
    from suisuicode.worktree.git import _resolve_head_sha_from_fs, _run_git

    # 1. slug 校验
    validate_slug(name)

    async with self.lock:
        # 2. 检查已存在
        if name in self.active:
            raise ValueError(f"Worktree 已存在: {name}")

        # 3. 计算路径
        flat = flat_slug(name)
        wt_path = self.worktree_dir / flat
        branch = f"worktree-{flat}"

        # 4. 快速恢复路径
        if wt_path.exists():
            sha = _resolve_head_sha_from_fs(str(wt_path))
            head_commit = sha if sha else "0000000000000000000000000000000000000000"
            wt = Worktree(
                name=name,
                path=str(wt_path),
                branch=branch,
                based_on=base_ref,
                head_commit=head_commit,
                created=datetime.fromtimestamp(wt_path.stat().st_mtime),
                manual=manual,
            )
            self.active[name] = wt
            return wt

        # 5. git worktree add
        try:
            await _run_git(
                self.repo_root,
                "worktree", "add", "-B", branch,
                str(wt_path), base_ref,
            )
        except Exception:
            # 清理可能的残留目录
            shutil.rmtree(wt_path, ignore_errors=True)
            raise

        # 6. 创建后设置（best-effort）
        await _perform_post_creation_setup(
            self.repo_root, str(wt_path), self.symlink_dirs,
        )

        # 7. 拿 head SHA
        try:
            head_sha = await _run_git(str(wt_path), "rev-parse", "HEAD")
        except Exception:
            head_sha = "0000000000000000000000000000000000000000"

        # 8. 构造 Worktree 并加入 active
        wt = Worktree(
            name=name,
            path=str(wt_path),
            branch=branch,
            based_on=base_ref,
            head_commit=head_sha,
            created=datetime.now(),
            manual=manual,
        )
        self.active[name] = wt
        return wt


# ── 创建后设置 ──────────────────────────────────────────


async def _perform_post_creation_setup(
    repo_root: str,
    wt_path: str,
    symlink_dirs: list[str],
) -> None:
    """依次执行四类后置设置，每个子步骤失败仅 stderr 警告。"""
    _copy_local_configs(repo_root, wt_path)
    await _setup_git_hooks(repo_root, wt_path)
    _symlink_large_dirs(repo_root, wt_path, symlink_dirs)
    _copy_included_ignored(repo_root, wt_path)


def _copy_local_configs(repo_root: str, wt_path: str) -> None:
    """设置 A：复制本地配置文件。"""
    config_files = [".suisuicode/config.yaml", ".suisuicode/settings.local.yaml"]
    for cf in config_files:
        src = Path(repo_root) / cf
        dst = Path(wt_path) / cf
        if not src.exists():
            continue
        if dst.exists():
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(str(src), str(dst))
        except Exception as exc:
            print(f"worktree: setup A (copy {cf}): {exc}", file=sys.stderr)


async def _setup_git_hooks(repo_root: str, wt_path: str) -> None:
    """设置 B：配置 git hooks 路径。"""
    from suisuicode.worktree.git import _run_git

    try:
        # 优先 .husky/
        husky_dir = Path(repo_root) / ".husky"
        if husky_dir.is_dir():
            hooks_path = str(husky_dir.resolve())
            await _run_git(str(wt_path), "config", "core.hooksPath", hooks_path)
            return

        # 回退：读主仓库的 core.hooksPath
        try:
            hooks_path = await _run_git(
                repo_root, "config", "--get", "core.hooksPath",
            )
        except Exception:
            return

        if hooks_path:
            abs_path = str(Path(hooks_path).resolve())
            await _run_git(str(wt_path), "config", "core.hooksPath", abs_path)
    except Exception as exc:
        print(f"worktree: setup B (git hooks): {exc}", file=sys.stderr)


def _symlink_large_dirs(
    repo_root: str,
    wt_path: str,
    symlink_dirs: list[str],
) -> None:
    """设置 C：软链大目录（node_modules / .venv / vendor）。"""
    for dir_name in symlink_dirs:
        src = Path(repo_root) / dir_name
        dst = Path(wt_path) / dir_name
        if not src.exists():
            continue
        if dst.exists():
            continue
        try:
            os.symlink(str(src), str(dst))
        except Exception as exc:
            print(
                f"worktree: setup C (symlink {dir_name}): {exc}",
                file=sys.stderr,
            )


def _copy_included_ignored(repo_root: str, wt_path: str) -> None:
    """设置 D：按 .worktreeinclude 复制被忽略但运行需要的文件。"""
    include_file = Path(repo_root) / ".worktreeinclude"
    if not include_file.exists():
        return

    try:
        patterns = _read_worktree_include(str(include_file))
    except Exception as exc:
        print(f"worktree: setup D (read .worktreeinclude): {exc}", file=sys.stderr)
        return
    if not patterns:
        return

    try:
        ignored = _list_ignored_files(repo_root)
    except Exception as exc:
        print(f"worktree: setup D (list ignored): {exc}", file=sys.stderr)
        return

    for f_rel in ignored:
        f_path = Path(repo_root) / f_rel
        if not f_path.is_file():
            continue
        for pat in patterns:
            if fnmatch.fnmatch(f_rel, pat):
                dst = Path(wt_path) / f_rel
                try:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(f_path), str(dst))
                except Exception as exc:
                    print(
                        f"worktree: setup D (copy {f_rel}): {exc}",
                        file=sys.stderr,
                    )
                break


def _read_worktree_include(path: str) -> list[str]:
    """读取 .worktreeinclude 文件，每行一个 glob 模式（去空行与注释）。"""
    lines = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            lines.append(stripped)
    return lines


def _list_ignored_files(repo_root: str) -> list[str]:
    """列出仓库中所有被忽略的文件（相对路径）。"""
    import subprocess as sp

    result = sp.run(
        [
            "git", "-C", repo_root,
            "ls-files", "--others", "--ignored",
            "--exclude-standard",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line.strip()]
