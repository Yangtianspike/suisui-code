"""ActiveSkills：跨轮维持的激活 Skill 列表。"""

from __future__ import annotations

import threading

from suisuicode.skills.types import ActiveEntry


class ActiveSkills:
    """线程安全的激活 Skill 追踪器。

    保持激活顺序；重复激活同名 skill 会覆盖原位置内容。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[ActiveEntry] = []
        self._index: dict[str, int] = {}  # name → entries 索引

    def activate(self, name: str, body: str) -> None:
        """激活一个 Skill（同名已激活则替换正文）。"""
        with self._lock:
            if name in self._index:
                idx = self._index[name]
                self._entries[idx] = ActiveEntry(name=name, body=body)
            else:
                self._index[name] = len(self._entries)
                self._entries.append(ActiveEntry(name=name, body=body))

    def clear(self) -> None:
        """清空全部激活项。"""
        with self._lock:
            self._entries.clear()
            self._index.clear()

    def snapshot(self) -> list[ActiveEntry]:
        """返回当前激活列表的副本（env 装配用）。"""
        with self._lock:
            return list(self._entries)

    def names(self) -> list[str]:
        """返回当前激活 Skill 名称列表。"""
        with self._lock:
            return [e.name for e in self._entries]

    def to_prompt_entries(self) -> list:
        """转为 prompt 包 ActiveSkillEntry 列表（避免循环依赖）。"""
        from suisuicode.skills.adapter import to_prompt_entries

        return to_prompt_entries(self.snapshot())
