"""TaskCreate 工具（F26）。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from suisuicode.team.tasks import Store, Task, Status
from suisuicode.tool import Result, Tool

if TYPE_CHECKING:
    pass


class TaskCreateTool(Tool):
    """创建共享任务。"""

    def __init__(self, store_getter=None) -> None:
        """store_getter: 可调用，返回当前 Team 的 tasks.Store。"""
        self._store_getter = store_getter

    def name(self) -> str:
        return "TaskCreate"

    def description(self) -> str:
        return "在团队共享任务列表中创建新任务。可指定负责人和依赖。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "任务标题",
                },
                "description": {
                    "type": "string",
                    "description": "任务详细描述（可选）",
                },
                "assignee": {
                    "type": "string",
                    "description": "负责人名称（可选）",
                },
                "blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "此任务依赖的任务 ID 列表（可选）",
                },
            },
            "required": ["title"],
        }

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args) if isinstance(args, str) else args
        except (json.JSONDecodeError, TypeError):
            return Result(is_error=True, content="invalid args")
        if not isinstance(data, dict):
            return Result(is_error=True, content="invalid args")

        title = str(data.get("title", ""))
        if not title:
            return Result(is_error=True, content="title is required")

        try:
            store = self._get_store()
            t = Task(
                id="",
                title=title,
                description=str(data.get("description", "")),
                status=Status.PENDING,
                assignee=str(data.get("assignee", "")),
                blocked_by=data.get("blocked_by", []) or [],
            )
            task_id = await store.create(t)
            return Result(content=json.dumps({
                "task_id": task_id,
                "title": title,
            }))
        except Exception as e:
            return Result(is_error=True, content=f"TaskCreate 失败: {e}")

    def _get_store(self) -> Store:
        if self._store_getter is not None:
            return self._store_getter()
        raise RuntimeError("TaskCreate: store_getter 未设置")
