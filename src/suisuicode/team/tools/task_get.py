"""TaskGet 工具（F27）。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from suisuicode.tool import Result, Tool

if TYPE_CHECKING:
    from suisuicode.team.tasks import Store


class TaskGetTool(Tool):
    """查询单个任务详情。"""

    def __init__(self, store_getter=None) -> None:
        self._store_getter = store_getter

    def name(self) -> str:
        return "TeamTaskGet"

    def description(self) -> str:
        return "按 task_id 查询任务详情，含依赖关系和当前状态。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "任务 ID（如 task_a1b2c3）",
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
        except (json.JSONDecodeError, TypeError):
            return Result(is_error=True, content="invalid args")
        if not isinstance(data, dict):
            return Result(is_error=True, content="invalid args")

        task_id = str(data.get("task_id", ""))
        if not task_id:
            return Result(is_error=True, content="task_id is required")

        try:
            store = self._get_store()
            t = await store.get(task_id)
            if t is None:
                return Result(is_error=True, content=f"任务不存在: {task_id}")
            return Result(content=json.dumps(t.to_dict()))
        except Exception as e:
            return Result(is_error=True, content=f"TaskGet 失败: {e}")

    def _get_store(self) -> Store:
        if self._store_getter is not None:
            return self._store_getter()
        raise RuntimeError("TaskGet: store_getter 未设置")
