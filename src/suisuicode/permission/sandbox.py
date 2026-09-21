"""路径沙箱：把文件操作限定在项目根目录内（N2）。

关键顺序：先解析符号链接，再做前缀判断，防止用软链接逃逸。
"""

from __future__ import annotations

import os
from pathlib import Path


def resolve_root(root: str) -> str:
    """解析项目根为绝对路径、消解符号链接。

    Raises:
        FileNotFoundError: 项目根不存在或不可解析。
    """
    return str(Path(root).expanduser().resolve(strict=True))


def eval_symlinks_or_ancestor(abs_path: str) -> str:
    """解析符号链接后返回真实路径。

    对已存在的目标直接用 Path.resolve(strict=True)。
    对不存在的目标（如新建文件、含未创建中间目录），
    逐级回退到最近的已存在祖先目录，resolve 该祖先后拼回剩余段。
    """
    p = Path(abs_path)
    if p.exists():
        return str(p.resolve(strict=True))

    # 回退到最近已存在祖先
    missing_parts: list[str] = []
    current = p
    while not current.exists():
        missing_parts.insert(0, current.name)
        current = current.parent
        if current == current.parent:  # 到达根（/ 或盘符）
            break

    # current 是最近已存在祖先（或根）
    try:
        resolved_ancestor = str(current.resolve(strict=True))
    except (FileNotFoundError, OSError):
        resolved_ancestor = str(current)

    if missing_parts:
        return os.path.join(resolved_ancestor, *missing_parts)
    return resolved_ancestor


def sandbox_ok(root: str, path: str) -> bool:
    """检查路径是否在项目根内（含符号链接解析）。

    Args:
        root: 已解析的项目根绝对路径。
        path: 待检查的目标路径（绝对或相对）。

    Returns:
        True 如果解析后的真实路径在 root 内。
    """
    if not path:
        return True  # 空路径视为 root

    p = Path(path)
    if not p.is_absolute():
        p = Path(root) / p

    abs_path = str(p)
    resolved = eval_symlinks_or_ancestor(abs_path)
    root_norm = root.rstrip(os.sep) + os.sep

    return resolved == root or resolved.startswith(root_norm)
