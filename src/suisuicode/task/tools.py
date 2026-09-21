"""4 个后台任务工具：TaskList / TaskGet / TaskStop / SendMessage（spec F20）。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from suisuicode.tool import Result, Tool

if TYPE_CHECKING:
    from suisuicode.task.manager import Manager


class TaskListTool(Tool):
    """列出当前所有后台任务。"""

    is_system_tool = True

    def __init__(self, manager: "Manager") -> None:
        self._mgr = manager

    def name(self) -> str:
        return "TaskList"

    def description(self) -> str:
        return "列出当前所有后台任务（id、name、status、tool_count、last_activity）"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, args: str) -> Result:
        tasks = self._mgr.list_all()
        items = [
            {
                "id": t.id,
                "name": t.name,
                "status": str(t.status),
                "tool_count": t.tool_count,
                "last_activity": t.last_activity,
            }
            for t in tasks
        ]
        return Result(content=json.dumps(items, ensure_ascii=False))


class TaskGetTool(Tool):
    """获取指定任务的完整状态。"""

    is_system_tool = True

    def __init__(self, manager: "Manager") -> None:
        self._mgr = manager

    def name(self) -> str:
        return "TaskGet"

    def description(self) -> str:
        return "获取指定后台任务的完整状态（含 result / err）"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID",
                },
            },
            "required": ["task_id"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args) if isinstance(args, str) else args
            task_id = data.get("task_id", "")
        except (json.JSONDecodeError, TypeError):
            return Result(is_error=True, content="invalid args: expected JSON with task_id")

        bt = self._mgr.get(task_id)
        if bt is None:
            return Result(is_error=True, content=f"task not found: {task_id}")

        info = {
            "id": bt.id,
            "name": bt.name,
            "status": str(bt.status),
            "result": bt.result,
            "err": str(bt.err) if bt.err else None,
            "tool_count": bt.tool_count,
            "last_activity": bt.last_activity,
            "start_time": bt.start_time,
            "end_time": bt.end_time,
            "usage": {
                "input": bt.usage.input,
                "output": bt.usage.output,
                "cache_write": bt.usage.cache_write,
                "cache_read": bt.usage.cache_read,
            },
        }
        return Result(content=json.dumps(info, ensure_ascii=False))


class TaskStopTool(Tool):
    """取消一个后台任务。"""

    is_system_tool = True

    def __init__(self, manager: "Manager") -> None:
        self._mgr = manager

    def name(self) -> str:
        return "TaskStop"

    def description(self) -> str:
        return "取消一个后台任务（触发 asyncio.CancelledError）"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID",
                },
            },
            "required": ["task_id"],
        }

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args) if isinstance(args, str) else args
            task_id = data.get("task_id", "")
        except (json.JSONDecodeError, TypeError):
            return Result(is_error=True, content="invalid args: expected JSON with task_id")

        ok = await self._mgr.stop(task_id)
        if not ok:
            return Result(is_error=True, content=f"task not found: {task_id}")
        return Result(content=json.dumps({"status": "cancellation_requested"}))


class SendMessageTool(Tool):
    """给已完成的后台 Agent 续派任务。"""

    is_system_tool = True

    def __init__(self, manager: "Manager") -> None:
        self._mgr = manager

    def name(self) -> str:
        return "SendMessage"

    def description(self) -> str:
        return "给一个已跑完的后台 Agent 续派任务（name → 找到 task，追加消息，重新启动）"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "目标 Agent 的名字",
                },
                "message": {
                    "type": "string",
                    "description": "要追加的消息/任务",
                },
            },
            "required": ["name", "message"],
        }

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args) if isinstance(args, str) else args
            name = data.get("name", "")
            message = data.get("message", "")
        except (json.JSONDecodeError, TypeError):
            return Result(is_error=True, content="invalid args: expected JSON with name and message")

        if not name:
            return Result(is_error=True, content="name is required")
        if not message:
            return Result(is_error=True, content="message is required")

        try:
            task_id = await self._mgr.send_message(name, message)
        except LookupError as e:
            return Result(is_error=True, content=str(e))
        except Exception as e:
            return Result(is_error=True, content=f"send_message failed: {e}")

        return Result(content=json.dumps({"task_id": task_id, "status": "resumed"}))
