"""Mailbox 单测 — 基本读写 + 并发 + stale 锁。"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

import pytest

from suisuicode.team.mailbox import Box, Message
from suisuicode.team.mailbox.message import MessageType
from suisuicode.team.filelock import acquire, LOCK_STALE_AFTER


class TestBoxBasic:
    """Box 基本读写。"""

    @pytest.fixture
    def tmpdir(self) -> str:
        with tempfile.TemporaryDirectory() as d:
            yield d

    def test_write_and_read(self, tmpdir: str) -> None:
        box = Box(tmpdir)
        msg = Message(
            from_="lead",
            to="alice",
            type=MessageType.TEXT,
            summary="hello",
            content="test message",
            timestamp=1234567890,
        )

        async def _run() -> None:
            await box.write("alice", msg)
            msgs = await box.read("alice")
            assert len(msgs) == 1
            assert msgs[0].from_ == "lead"
            assert msgs[0].to == "alice"
            assert msgs[0].summary == "hello"
            assert msgs[0].content == "test message"

        asyncio.run(_run())

    def test_read_unread_and_mark_read(self, tmpdir: str) -> None:
        box = Box(tmpdir)

        async def _run() -> None:
            await box.write("alice", Message(from_="lead", to="alice",
                                              summary="msg1"))
            await box.write("alice", Message(from_="bob", to="alice",
                                              summary="msg2"))

            indices, unread = await box.read_unread("alice")
            assert len(unread) == 2
            assert indices == [0, 1]

            await box.mark_read("alice", [0])
            indices2, unread2 = await box.read_unread("alice")
            assert len(unread2) == 1
            assert unread2[0].summary == "msg2"

        asyncio.run(_run())

    def test_empty_mailbox(self, tmpdir: str) -> None:
        box = Box(tmpdir)

        async def _run() -> None:
            msgs = await box.read("alice")
            assert msgs == []
            indices, unread = await box.read_unread("alice")
            assert indices == []
            assert unread == []

        asyncio.run(_run())


class TestMailboxConcurrent:
    """并发写入测试（AC14）。"""

    @pytest.mark.asyncio
    async def test_concurrent_writes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            box = Box(d)
            agent_id = "alice"

            async def writer(i: int) -> None:
                await box.write(agent_id, Message(
                    from_="lead", to=agent_id,
                    summary=f"msg{i}",
                ))

            tasks = [writer(i) for i in range(10)]
            await asyncio.gather(*tasks)

            msgs = await box.read(agent_id)
            assert len(msgs) == 10, f"expected 10, got {len(msgs)}"


class TestFileLock:
    """文件锁测试。"""

    @pytest.mark.asyncio
    async def test_acquire_release(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            lock_path = str(Path(d) / "test.lock")
            async with acquire(lock_path):
                assert os.path.exists(lock_path)
            assert not os.path.exists(lock_path)

    @pytest.mark.asyncio
    async def test_acquire_conflict(self) -> None:
        """两次同时抢锁——第二次在第一次释放前无法获取。"""
        with tempfile.TemporaryDirectory() as d:
            lock_path = str(Path(d) / "test.lock")

            async with acquire(lock_path):
                # 另一个 acquire 应该在同一个线程里被阻塞
                # 用 gather + timeout 验证
                async def try_acquire() -> bool:
                    try:
                        async with acquire(lock_path):
                            return True
                    except RuntimeError:
                        return False

                # 用很短的时间来判断——锁应该被占住
                task = asyncio.create_task(try_acquire())
                await asyncio.sleep(0.1)
                assert not task.done(), "第二个 acquire 不应该在锁释放前成功"

            # 释放后第二个任务应该能拿到
            result = await asyncio.wait_for(task, timeout=5.0)
            assert result is True

    @pytest.mark.asyncio
    async def test_stale_lock(self) -> None:
        """stale 超过 10 秒的锁能被抢占（AC15）。"""
        with tempfile.TemporaryDirectory() as d:
            lock_path = str(Path(d) / "test.lock")
            # 制造一个"旧"锁
            Path(lock_path).write_text("")
            # 把 mtime 设成 11 秒前
            stale_time = time.time() - LOCK_STALE_AFTER - 1.0
            os.utime(lock_path, (stale_time, stale_time))

            # acquire 应该检测到 stale 并清掉
            async with acquire(lock_path):
                assert os.path.exists(lock_path)
