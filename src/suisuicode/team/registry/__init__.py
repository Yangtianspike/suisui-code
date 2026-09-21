"""AgentNameRegistry — Agent name ↔ agent_id 双向映射。

线程安全，用于 SendMessage 寻址与 id↔name 反查。
"""

from __future__ import annotations

import threading


class AgentNameRegistry:
    """弱引用注册表：后注册的覆盖前注册的（同名 / 同 agent_id）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_name: dict[str, str] = {}  # name → agent_id
        self._by_id: dict[str, str] = {}  # agent_id → name

    # ── 注册 / 注销 ───────────────────────────────────────

    def register(self, name: str, agent_id: str) -> None:
        """注册 name → agent_id 映射。同名覆盖旧映射。"""
        with self._lock:
            # 若 agent_id 已关联其他 name，先清理旧 name
            old_name = self._by_id.get(agent_id)
            if old_name is not None and old_name != name:
                self._by_name.pop(old_name, None)

            # 若 name 已被占用（指向不同 agent_id），清理旧关联
            old_id = self._by_name.get(name)
            if old_id is not None and old_id != agent_id:
                self._by_id.pop(old_id, None)

            self._by_name[name] = agent_id
            self._by_id[agent_id] = name

    def unregister(self, name: str) -> None:
        """按 name 注销。"""
        with self._lock:
            agent_id = self._by_name.pop(name, None)
            if agent_id is not None:
                self._by_id.pop(agent_id, None)

    def unregister_by_agent_id(self, agent_id: str) -> None:
        """按 agent_id 注销。"""
        with self._lock:
            name = self._by_id.pop(agent_id, None)
            if name is not None:
                self._by_name.pop(name, None)

    # ── 查询 ──────────────────────────────────────────────

    def resolve(self, name_or_id: str) -> str | None:
        """输入 name 或 agent_id，返回对应的 agent_id。"""
        with self._lock:
            # 先按 name 查
            if name_or_id in self._by_name:
                return self._by_name[name_or_id]
            # 再按 agent_id 反向查
            if name_or_id in self._by_id:
                return name_or_id
            return None

    def name_of(self, agent_id: str) -> str | None:
        """通过 agent_id 反查 name。"""
        with self._lock:
            return self._by_id.get(agent_id)

    def list_(self) -> dict[str, str]:
        """返回 name → agent_id 完整映射的快照。"""
        with self._lock:
            return dict(self._by_name)
