"""Team 持久化辅助：sanitize、原子写、跨进程 reload。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from suisuicode.team.types import Team


def sanitize(name: str) -> str:
    """把用户给的 Team 名转为文件路径安全的 slug。

    只保留 [a-zA-Z0-9._-]，其他字符替换为 ``-``，首尾去 ``-``。
    空字符串返回 ``""``。
    """
    if not name:
        return ""
    result = re.sub(r"[^a-zA-Z0-9._-]", "-", name)
    # 合并连续 ``-``
    result = re.sub(r"-{2,}", "-", result)
    # 去首尾 ``-``
    result = result.strip("-")
    return result


def atomic_write_json(path: str | Path, value: Any) -> None:
    """原子写入 JSON 文件：先写 .tmp 再 os.replace。"""
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, p)


def read_json(path: str | Path) -> Any:
    """读取 JSON 文件，不存在时抛 FileNotFoundError。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


async def reload_from_disk_locked(team: Team) -> None:
    """在持锁状态下从磁盘重载 Team 的 members 字段。

    用于跨进程并发兜底：Pane 后端的 Lead 与子进程是不同进程，
    add_member / set_member_active 修改前必须先读最新的 disk 状态，
    避免「子进程没看到自己的 TeammateInfo，set_member_active 静默 no-op」。

    调用方必须先持有 team._lock。
    """
    try:
        raw = read_json(team.config_path)
    except (FileNotFoundError, json.JSONDecodeError):
        # 磁盘损坏或尚未写入，保留内存现状
        return

    if not isinstance(raw, dict):
        return

    raw_members = raw.get("members", [])
    if not isinstance(raw_members, list):
        return

    from suisuicode.team.types import TeammateInfo

    members = [TeammateInfo.from_dict(m) for m in raw_members]
    team.members = members
