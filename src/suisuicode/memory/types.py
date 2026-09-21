"""笔记类型定义：NoteType 枚举、Note 与 UpdateAction 数据类。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class NoteType(StrEnum):
    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


@dataclass
class Note:
    """一条笔记的内存表示。"""

    name: str  # kebab-case slug，唯一标识
    description: str  # 一句话摘要，用于索引显示
    type: NoteType
    content: str  # 正文（feedback/project 类型包含 **Why:** 和 **How to apply:**）
    filename: str  # <name>.md
    created: datetime
    updated: datetime


@dataclass
class UpdateAction:
    """LLM 返回的单条操作。"""

    action: str  # "create"/"update"/"delete"
    level: str  # "project"/"user"
    name: str = ""  # kebab-case slug（create 时必需）
    description: str = ""  # 一句话摘要（create/update 时使用）
    type: str = ""  # NoteType（create 时必需）
    content: str = ""  # 正文（create/update 时使用）
    filename: str = ""  # update/delete 时必需（定位已有文件）
