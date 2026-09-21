"""WorktreeSession dataclass + JSON 原子持久化。"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class WorktreeSession:
    """当前活跃的 Worktree 会话记录。"""

    original_cwd: str
    worktree_path: str
    worktree_name: str
    original_branch: str
    original_head_commit: str
    session_id: str
    hook_based: bool = False

    def to_json(self) -> str:
        """序列化为 JSON 字符串（字段名小写下划线）。"""
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "WorktreeSession":
        """从 JSON 字符串反序列化。"""
        return cls(**json.loads(raw))


def load_session(path: Path) -> WorktreeSession | None:
    """从 JSON 文件读取 session。

    返回：
        WorktreeSession — 反序列化成功
        None — 文件不存在 或 内容为 ``null``/空
    """
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not raw or raw == "null":
        return None
    return WorktreeSession.from_json(raw)


def save_session(path: Path, session: WorktreeSession | None) -> None:
    """原子写入 session 到 JSON 文件。

    session=None 时写入 ``null``。
    先写 ``.tmp`` 临时文件再 ``os.replace`` 原子替换。
    """
    if session is None:
        content = "null"
    else:
        content = session.to_json()

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(str(tmp), str(path))


def clear_session(path: Path) -> None:
    """清空 session 文件（等同写入 null）。"""
    save_session(path, None)
