"""后台任务管理：Manager + BackgroundTask + launch / adopt_running / stop / send_message。

协程安全（单事件循环），通过 asyncio.Lock 保护共享状态。
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from suisuicode.agent import Agent, ApprovalRequest
    from suisuicode.conversation import Conversation
    from suisuicode.permission import Outcome


class Status(IntEnum):
    RUNNING = 0
    COMPLETED = 1
    FAILED = 2
    CANCELLED = 3

    def __str__(self) -> str:
        return {0: "running", 1: "completed", 2: "failed", 3: "cancelled"}.get(
            int(self), "unknown"
        )


@dataclass
class Usage:
    """Token 用量（与 agent.Usage 对齐）。"""

    input: int = 0
    output: int = 0
    cache_write: int = 0
    cache_read: int = 0


@dataclass
class BackgroundTask:
    """一个后台子 Agent 的完整状态快照（spec F15）。"""

    id: str  # manager 生成的唯一 ID
    name: str = ""  # Agent 工具的 name 参数
    sub_agent: "Agent | None" = None
    conv: "Conversation | None" = None
    task: str = ""  # 初始任务文本
    status: Status = Status.RUNNING
    result: str = ""  # 跑完后的最终文本
    err: BaseException | None = None
    start_time: float = field(default_factory=time.monotonic)
    end_time: float = 0.0
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    handle: asyncio.Task | None = None  # 跑动协程的 asyncio.Task
    usage: Usage = field(default_factory=Usage)
    tool_count: int = 0
    last_activity: str = ""


@dataclass
class PartialState:
    """前台→后台移交时已收集的中间状态（spec F17）。"""

    last_assistant_text: str = ""
    tool_count: int = 0
    last_activity: str = ""
    usage: Usage = field(default_factory=Usage)


class TaskBusy(Exception):
    """send_message 时目标任务非 COMPLETED 状态。"""


class Manager:
    """管理后台任务。协程安全（单事件循环）。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._tasks: dict[str, BackgroundTask] = {}
        self._by_name: dict[str, str] = {}  # name -> id，弱引用
        self._done: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._counter: int = 0
        self._name_reg = None  # ch15: AgentNameRegistry | None
        self._on_done_callbacks: list = []  # ch15: 任务完成回调

    # ── ID 生成 ───────────────────────────────────────

    def _next_id(self) -> str:
        self._counter += 1
        return f"task_{(int(time.time_ns()) ^ self._counter) & 0xFFFFFFFF:08x}"

    # ── 查询 ──────────────────────────────────────────

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list(self) -> list[BackgroundTask]:
        """返回所有非 Terminated 任务，按 start_time 升序。"""
        active = [
            t
            for t in self._tasks.values()
            if t.status in (Status.RUNNING,)
        ]
        active.sort(key=lambda t: t.start_time)
        return active

    def list_all(self) -> list[BackgroundTask]:
        """返回所有任务（含已完结），按 start_time 升序。"""
        return sorted(self._tasks.values(), key=lambda t: t.start_time)

    def subscribe_done(self) -> asyncio.Queue[str]:
        """返回 done 队列（TUI 消费）。"""
        return self._done

    # ── launch ────────────────────────────────────────

    async def launch(
        self,
        ag: "Agent",
        conv: "Conversation",
        name: str,
        task_text: str,
    ) -> str:
        """启动一个后台子 Agent（spec F16）。

        Returns:
            task_id
        """
        task_id = self._next_id()
        bt = BackgroundTask(
            id=task_id,
            name=name,
            sub_agent=ag,
            conv=conv,
            task=task_text,
            status=Status.RUNNING,
            start_time=time.monotonic(),
        )

        async with self._lock:
            self._tasks[task_id] = bt
            if name:
                self._by_name[name] = task_id

        events: asyncio.Queue = asyncio.Queue(maxsize=64)

        async def aggregate_events() -> None:
            """消费 events 队列，更新 bt 字段。"""
            while True:
                ev = await events.get()
                if ev is None:
                    break
                # 简化：event 对象有 tool 字段时累加计数
                if hasattr(ev, "tool") and ev.tool is not None:
                    bt.tool_count += 1
                    bt.last_activity = ev.tool.name
                if hasattr(ev, "usage") and ev.usage is not None:
                    bt.usage.input += getattr(ev.usage, "input", 0)
                    bt.usage.output += getattr(ev.usage, "output", 0)

        aggregator = asyncio.create_task(aggregate_events())

        async def runner() -> None:
            try:
                text = await ag.run_to_completion(conv, task_text, events)
                bt.result = text
                bt.status = Status.COMPLETED
            except asyncio.CancelledError:
                bt.status = Status.CANCELLED
            except BaseException as e:
                bt.status = Status.FAILED
                bt.err = e
                bt.result = str(e)
            finally:
                bt.end_time = time.monotonic()
                aggregator.cancel()
                try:
                    await events.put(None)  # sentinel
                except Exception:
                    pass
                try:
                    self._done.put_nowait(task_id)
                except asyncio.QueueFull:
                    print(
                        f"task manager: done queue full, "
                        f"dropping notification for {task_id}",
                        file=sys.stderr,
                    )
                await self._fire_done_callbacks(task_id)

        bt.handle = asyncio.create_task(runner())
        return task_id

    # ── stop ──────────────────────────────────────────

    async def stop(self, task_id: str) -> bool:
        """取消一个后台任务（spec F20 TaskStop）。"""
        bt = self._tasks.get(task_id)
        if bt is None:
            return False
        if bt.handle is not None:
            bt.handle.cancel()
        return True

    # ── adopt_running ─────────────────────────────────

    async def adopt_running(
        self,
        ag: "Agent",
        conv: "Conversation",
        name: str,
        events: asyncio.Queue,
        handle: asyncio.Task,
        partial: PartialState,
    ) -> str:
        """接管一个已在前台启动的子 Agent（超时/ESC 切后台）。

        不重新调 run_to_completion（已在前台启动），注册 BackgroundTask 状态，
        聚合事件，等 handle 完成后写终态、push done。
        """
        task_id = self._next_id()
        bt = BackgroundTask(
            id=task_id,
            name=name,
            sub_agent=ag,
            conv=conv,
            task="",
            status=Status.RUNNING,
            start_time=time.monotonic(),
            tool_count=partial.tool_count,
            last_activity=partial.last_activity,
            usage=partial.usage,
            handle=handle,
        )

        async with self._lock:
            self._tasks[task_id] = bt
            if name:
                self._by_name[name] = task_id

        async def monitor() -> None:
            try:
                await handle
                bt.status = Status.COMPLETED
            except asyncio.CancelledError:
                bt.status = Status.CANCELLED
            except BaseException as e:
                bt.status = Status.FAILED
                bt.err = e
                bt.result = str(e)
            finally:
                bt.end_time = time.monotonic()
                try:
                    self._done.put_nowait(task_id)
                except asyncio.QueueFull:
                    print(
                        f"task manager: done queue full, "
                        f"dropping notification for {task_id}",
                        file=sys.stderr,
                    )
                await self._fire_done_callbacks(task_id)

        asyncio.create_task(monitor())
        return task_id

    # ── send_message ──────────────────────────────────

    async def send_message(self, name: str, message: str) -> str:
        """给已完成的具名后台 Agent 续派任务（spec F20 SendMessage）。

        Raises:
            TaskBusy: name 对应的任务非 COMPLETED
            LookupError: name 未找到
        """
        task_id = self._by_name.get(name)
        if task_id is None:
            raise LookupError(f"no task with name: {name}")

        bt = self._tasks.get(task_id)
        if bt is None:
            raise LookupError(f"task {task_id} not found")

        if bt.status != Status.COMPLETED:
            raise TaskBusy(f"task {task_id} is not completed (status={bt.status})")

        if bt.conv is None or bt.sub_agent is None:
            raise RuntimeError("task has no conversation or agent")

        # 追加新 user 消息
        bt.conv.add_user(message)

        # 重置状态并重新启动
        bt.status = Status.RUNNING
        bt.result = ""
        bt.err = None
        bt.end_time = 0.0

        events: asyncio.Queue = asyncio.Queue(maxsize=64)

        async def aggregate_events() -> None:
            while True:
                ev = await events.get()
                if ev is None:
                    break
                if hasattr(ev, "tool") and ev.tool is not None:
                    bt.tool_count += 1
                    bt.last_activity = ev.tool.name
                if hasattr(ev, "usage") and ev.usage is not None:
                    bt.usage.input += getattr(ev.usage, "input", 0)
                    bt.usage.output += getattr(ev.usage, "output", 0)

        aggregator = asyncio.create_task(aggregate_events())

        async def runner() -> None:
            try:
                text = await bt.sub_agent.run_to_completion(bt.conv, "", events)
                bt.result = text
                bt.status = Status.COMPLETED
            except asyncio.CancelledError:
                bt.status = Status.CANCELLED
            except BaseException as e:
                bt.status = Status.FAILED
                bt.err = e
                bt.result = str(e)
            finally:
                bt.end_time = time.monotonic()
                aggregator.cancel()
                try:
                    await events.put(None)
                except Exception:
                    pass
                try:
                    self._done.put_nowait(task_id)
                except asyncio.QueueFull:
                    pass
                await self._fire_done_callbacks(task_id)

        bt.handle = asyncio.create_task(runner())
        return task_id

    # ── ch15: name registry ─────────────────────────

    def set_name_registry(self, reg) -> None:
        """ch15: 设置 AgentNameRegistry 引用。"""
        self._name_reg = reg

    # ── ch15: task done callback ────────────────────

    def on_task_done(self, fn) -> None:
        """注册任务完成回调。fn(task_id: str) -> awaitable。"""
        self._on_done_callbacks.append(fn)

    async def _fire_done_callbacks(self, task_id: str) -> None:
        """触发所有 on_task_done 回调。"""
        for fn in self._on_done_callbacks:
            try:
                await fn(task_id)
            except Exception:
                pass

    # ── upgrade_approval ──────────────────────────────

    async def upgrade_approval(
        self, req: "ApprovalRequest"
    ) -> tuple["Outcome", bool]:
        """子 Agent 审批升级回调（spec F13）。

        本期返回 (DENY_ONCE, False)，让 Approval 走子 Agent 自己的 events 队列。
        """
        from suisuicode.permission import Outcome

        return Outcome.DENY_ONCE, False
