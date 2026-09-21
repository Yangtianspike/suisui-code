"""会话列表扫描：按修改时间倒序排列有效会话。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from suisuicode.compact.state import parse_session_time

# 最小文件大小阈值（字节），过滤空会话
_MIN_JSONL_SIZE = 50


@dataclass
class SessionInfo:
    """会话列表中一项的摘要信息。"""

    id: str  # session ID（目录名）
    title: str  # 第一条 user 消息内容（截断）
    modified_at: datetime  # 最后修改时间
    model: str  # 模型标签
    size: int  # JSONL 文件大小（字节）
    dir: str  # 会话目录绝对路径


def list_sessions(sessions_dir: str) -> list[SessionInfo]:
    """扫描 sessions_dir，返回按修改时间倒序排列的会话列表。

    过滤规则：
    - 只返回包含 conversation.jsonl 且 ID 能解析为新格式的目录
    - 过滤 JSONL 小于 _MIN_JSONL_SIZE 的空会话
    """
    root = Path(sessions_dir)
    if not root.exists():
        return []

    result: list[SessionInfo] = []

    for entry in root.iterdir():
        if not entry.is_dir():
            continue

        # 尝试解析 session ID → 旧格式跳过
        try:
            parse_session_time(entry.name)
        except ValueError:
            continue

        jsonl = entry / "conversation.jsonl"
        if not jsonl.exists():
            continue

        stat = jsonl.stat()
        # 过滤空 JSONL
        if stat.st_size < _MIN_JSONL_SIZE:
            continue

        # 读取第一条 user 消息提取 title 和 model
        title = ""
        model = ""
        try:
            with open(jsonl, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if obj.get("type") == "compact":
                        continue
                    # 取第一条消息的 model
                    if model == "" and obj.get("model"):
                        model = obj["model"]
                    if obj.get("role") == "user":
                        title = obj.get("content", "")
                        if model == "" and obj.get("model"):
                            model = obj["model"]
                        break
        except OSError:
            continue

        # 截断 title
        if len(title) > 50:
            title = title[:47] + "..."

        result.append(
            SessionInfo(
                id=entry.name,
                title=title,
                modified_at=datetime.fromtimestamp(stat.st_mtime),
                model=model,
                size=stat.st_size,
                dir=str(entry),
            )
        )

    # 按 modified_at 倒序
    result.sort(key=lambda x: x.modified_at, reverse=True)
    return result
