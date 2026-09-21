"""邮箱 — 队员间异步消息传递。

Box 提供 write / read / read_unread / mark_read，
所有公开方法内部走 filelock.acquire 保证跨进程安全。
"""

from __future__ import annotations

import time
from pathlib import Path

from suisuicode.team.filelock import acquire
from suisuicode.team.mailbox.message import Message, MessageType
from suisuicode.team.persistence import atomic_write_json, read_json

__all__ = ["Box", "Message", "MessageType"]


class Box:
    """邮箱读写，底层为 <mailbox_dir>/<agent_id>.json + 文件锁。"""

    def __init__(self, dir_: str) -> None:
        self._dir = dir_
        Path(self._dir).mkdir(parents=True, exist_ok=True)

    def _mailbox_path(self, agent_id: str) -> str:
        return str(Path(self._dir) / f"{agent_id}.json")

    def _lock_path(self, agent_id: str) -> str:
        return str(Path(self._dir) / f"{agent_id}.lock")

    async def write(self, agent_id: str, msg: Message) -> None:
        """向指定 agent_id 的邮箱追加一条消息。"""
        lock_path = self._lock_path(agent_id)
        mbox_path = self._mailbox_path(agent_id)

        async with acquire(lock_path):
            # 读现有消息
            messages: list[dict] = []
            try:
                raw = read_json(mbox_path)
                messages = raw.get("messages", [])
            except FileNotFoundError:
                pass

            # 设置时间戳
            if msg.timestamp == 0:
                msg.timestamp = int(time.time())

            messages.append(msg.to_dict())
            atomic_write_json(mbox_path, {"messages": messages})

    async def read(self, agent_id: str) -> list[Message]:
        """读取指定 agent_id 邮箱的全部消息。"""
        mbox_path = self._mailbox_path(agent_id)
        lock_path = self._lock_path(agent_id)

        async with acquire(lock_path):
            try:
                raw = read_json(mbox_path)
                return [
                    Message.from_dict(m) for m in raw.get("messages", [])
                ]
            except FileNotFoundError:
                return []

    async def read_unread(
        self, agent_id: str
    ) -> tuple[list[int], list[Message]]:
        """返回未读消息的索引列表与消息本身。"""
        all_msgs = await self.read(agent_id)
        indices: list[int] = []
        unread: list[Message] = []
        for i, m in enumerate(all_msgs):
            if not m.read:
                indices.append(i)
                unread.append(m)
        return indices, unread

    async def mark_read(
        self, agent_id: str, indices: list[int]
    ) -> None:
        """把指定索引的消息标记为已读。"""
        if not indices:
            return
        mbox_path = self._mailbox_path(agent_id)
        lock_path = self._lock_path(agent_id)
        idx_set = set(indices)

        async with acquire(lock_path):
            try:
                raw = read_json(mbox_path)
                messages = raw.get("messages", [])
                for i in idx_set:
                    if 0 <= i < len(messages):
                        messages[i]["read"] = True
                atomic_write_json(mbox_path, {"messages": messages})
            except FileNotFoundError:
                pass

    async def write_broadcast(
        self,
        agent_ids: list[str],
        msg: Message,
        exclude: str = "",
    ) -> None:
        """向一组 agent_id 各写一条消息副本（排除 exclude）。"""
        for aid in agent_ids:
            if aid == exclude:
                continue
            await self.write(aid, msg)
