"""共享任务列表 Store — 队员之间协调工作的 Task CRUD。

底层存储为 <team_config_dir>/tasks.json，通过文件锁保证并发安全。
"""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from enum import StrEnum

from suisuicode.team.filelock import acquire
from suisuicode.team.persistence import atomic_write_json, read_json


class Status(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class Task:
    """共享任务数据结构。"""

    id: str
    title: str
    description: str = ""
    status: Status = Status.PENDING
    assignee: str = ""
    blocked_by: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": str(self.status),
            "assignee": self.assignee,
            "blocked_by": self.blocked_by,
            "blocks": self.blocks,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Task:
        status = Status.PENDING
        raw_status = d.get("status", "pending")
        try:
            status = Status(raw_status)
        except ValueError:
            pass
        return cls(
            id=str(d.get("id", "")),
            title=str(d.get("title", "")),
            description=str(d.get("description", "")),
            status=status,
            assignee=str(d.get("assignee", "")),
            blocked_by=d.get("blocked_by", []) or [],
            blocks=d.get("blocks", []) or [],
            created_at=int(d.get("created_at", 0)),
            updated_at=int(d.get("updated_at", 0)),
        )

    @property
    def is_ready(self) -> bool:
        """所有 blocked_by 中的任务都完成了？"""
        return getattr(self, "_is_ready", True)

    @is_ready.setter
    def is_ready(self, value: bool) -> None:
        self._is_ready = value


@dataclass
class Filter:
    """list_ 过滤条件。"""

    status: Status | None = None
    assignee: str | None = None


@dataclass
class Patch:
    """update 可选变更字段。"""

    title: str | None = None
    description: str | None = None
    status: Status | None = None
    assignee: str | None = None
    add_blocks: list[str] = field(default_factory=list)
    add_blocked_by: list[str] = field(default_factory=list)
    remove_blocks: list[str] = field(default_factory=list)
    remove_blocked_by: list[str] = field(default_factory=list)


class Store:
    """Team 共享任务列表持久化存储。"""

    def __init__(self, path: str) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    def _lock_path(self) -> str:
        return self._path + ".lock"

    def _generate_id(self) -> str:
        return f"task_{secrets.token_hex(3)}"

    def _now(self) -> int:
        return int(time.time())

    async def _read_all(self) -> list[dict]:
        """读全部任务（持文件锁）。"""
        async with acquire(self._lock_path()):
            try:
                raw = read_json(self._path)
                return raw.get("tasks", [])
            except FileNotFoundError:
                return []

    async def _write_all(self, tasks: list[dict]) -> None:
        """写全部任务（持文件锁）。"""
        async with acquire(self._lock_path()):
            atomic_write_json(self._path, {"tasks": tasks})

    # ── CRUD ──────────────────────────────────────────────

    async def create(self, t: Task) -> str:
        """创建新任务，返回 task_id。"""
        if not t.id:
            t.id = self._generate_id()
        if not t.created_at:
            t.created_at = self._now()
        t.updated_at = self._now()

        tasks = await self._read_all()
        tasks.append(t.to_dict())
        await self._write_all(tasks)
        return t.id

    async def get(self, id_: str) -> Task | None:
        """按 ID 取任务。"""
        tasks = await self._read_all()
        for raw in tasks:
            if raw.get("id") == id_:
                return Task.from_dict(raw)
        return None

    async def list_(self, f: Filter | None = None) -> list[Task]:
        """按过滤条件列出任务，附带 is_ready 字段。"""
        tasks_raw = await self._read_all()
        task_map: dict[str, Task] = {}
        for raw in tasks_raw:
            t = Task.from_dict(raw)
            task_map[t.id] = t

        result: list[Task] = []
        for t in task_map.values():
            # 过滤
            if f is not None:
                if f.status is not None and t.status != f.status:
                    continue
                if f.assignee is not None and t.assignee != f.assignee:
                    continue

            # 计算 is_ready：所有 blocked_by 任务是否都 completed
            all_blockers_done = True
            for blocker_id in t.blocked_by:
                blocker = task_map.get(blocker_id)
                if blocker is None or blocker.status != Status.COMPLETED:
                    all_blockers_done = False
                    break

            # 借用 description 后的一个附加字段不太好，直接在 Task 上算
            # 这里返回的 Task 对象的 is_ready 在 to_dict 时不会序列化
            t.is_ready = all_blockers_done  # type: ignore[assignment]
            result.append(t)

        return result

    async def update(self, id_: str, p: Patch) -> None:
        """更新任务字段，自动维护双向依赖关系。"""
        tasks = await self._read_all()

        target: dict | None = None
        for raw in tasks:
            if raw.get("id") == id_:
                target = raw
                break

        if target is None:
            raise LookupError(f"任务不存在: {id_}")

        now = self._now()

        # 基础字段更新
        if p.title is not None:
            target["title"] = p.title
        if p.description is not None:
            target["description"] = p.description
        if p.status is not None:
            target["status"] = str(p.status)
        if p.assignee is not None:
            target["assignee"] = p.assignee

        # 依赖关系更新
        blocked_by: list[str] = target.get("blocked_by", []) or []
        blocks: list[str] = target.get("blocks", []) or []

        for b_id in p.add_blocked_by:
            if b_id not in blocked_by:
                blocked_by.append(b_id)
        for b_id in p.add_blocks:
            if b_id not in blocks:
                blocks.append(b_id)
        for b_id in p.remove_blocked_by:
            if b_id in blocked_by:
                blocked_by.remove(b_id)
        for b_id in p.remove_blocks:
            if b_id in blocks:
                blocks.remove(b_id)

        target["blocked_by"] = blocked_by
        target["blocks"] = blocks
        target["updated_at"] = now

        # 双向维护：给 add_blocked_by 的目标任务的 blocks 加上当前任务 id
        for b_id in p.add_blocked_by:
            for raw_other in tasks:
                if raw_other.get("id") == b_id:
                    other_blocks: list[str] = raw_other.get("blocks", []) or []
                    if id_ not in other_blocks:
                        other_blocks.append(id_)
                    raw_other["blocks"] = other_blocks
                    raw_other["updated_at"] = now
                    break

        # 双向维护：给 remove_blocked_by 的目标任务的 blocks 移除当前任务 id
        for b_id in p.remove_blocked_by:
            for raw_other in tasks:
                if raw_other.get("id") == b_id:
                    other_blocks: list[str] = raw_other.get("blocks", []) or []
                    if id_ in other_blocks:
                        other_blocks.remove(id_)
                    raw_other["blocks"] = other_blocks
                    raw_other["updated_at"] = now
                    break

        # 双向维护：给 add_blocks 的目标任务的 blocked_by 加上当前任务 id
        for b_id in p.add_blocks:
            for raw_other in tasks:
                if raw_other.get("id") == b_id:
                    other_blocked: list[str] = (
                        raw_other.get("blocked_by", []) or []
                    )
                    if id_ not in other_blocked:
                        other_blocked.append(id_)
                    raw_other["blocked_by"] = other_blocked
                    raw_other["updated_at"] = now
                    break

        # 双向维护：给 remove_blocks 的目标任务的 blocked_by 移除当前任务 id
        for b_id in p.remove_blocks:
            for raw_other in tasks:
                if raw_other.get("id") == b_id:
                    other_blocked: list[str] = (
                        raw_other.get("blocked_by", []) or []
                    )
                    if id_ in other_blocked:
                        other_blocked.remove(id_)
                    raw_other["blocked_by"] = other_blocked
                    raw_other["updated_at"] = now
                    break

        await self._write_all(tasks)
