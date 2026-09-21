"""TaskUpdate 工具（F29）。"""

from __future__ import annotations

import json

from suisuicode.team.tasks import Patch, Status
from suisuicode.tool import Result, Tool


class TaskUpdateTool(Tool):
    """更新共享任务。"""

    def __init__(self, store_getter=None) -> None:
        self._store_getter = store_getter

    def name(self) -> str:
        return "TaskUpdate"

    def description(self) -> str:
        return (
            "更新任务字段。支持修改标题/描述/状态/负责人/依赖关系。"
            "add_blocked_by 和 add_blocks 会自动维护双向依赖。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "任务 ID"},
                "title": {"type": "string", "description": "新标题（可选）"},
                "description": {"type": "string", "description": "新描述（可选）"},
                "status": {
                    "type": "string",
                    "description": "新状态：pending / in_progress / completed / blocked（可选）",
                },
                "assignee": {"type": "string", "description": "新负责人（可选）"},
                "add_blocks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "添加被此任务阻塞的依赖（可选）",
                },
                "add_blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "添加阻塞此任务的依赖（可选）",
                },
                "remove_blocks": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "移除被此任务阻塞的依赖（可选）",
                },
                "remove_blocked_by": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "移除阻塞此任务的依赖（可选）",
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
        except (json.JSONDecodeError, TypeError):
            return Result(is_error=True, content="invalid args")
        if not isinstance(data, dict):
            return Result(is_error=True, content="invalid args")

        task_id = str(data.get("task_id", ""))
        if not task_id:
            return Result(is_error=True, content="task_id is required")

        try:
            store = self._get_store()

            p = Patch()
            if "title" in data:
                p.title = str(data["title"])
            if "description" in data:
                p.description = str(data["description"])
            if "status" in data:
                try:
                    p.status = Status(str(data["status"]))
                except ValueError:
                    return Result(
                        is_error=True,
                        content=f"非法 status: {data['status']!r}",
                    )
            if "assignee" in data:
                p.assignee = str(data["assignee"])
            if "add_blocks" in data:
                p.add_blocks = data["add_blocks"] or []
            if "add_blocked_by" in data:
                p.add_blocked_by = data["add_blocked_by"] or []
            if "remove_blocks" in data:
                p.remove_blocks = data["remove_blocks"] or []
            if "remove_blocked_by" in data:
                p.remove_blocked_by = data["remove_blocked_by"] or []

            await store.update(task_id, p)
            return Result(content=json.dumps({"updated": task_id}))
        except LookupError as e:
            return Result(is_error=True, content=str(e))
        except Exception as e:
            return Result(is_error=True, content=f"TaskUpdate 失败: {e}")

    def _get_store(self):
        if self._store_getter is not None:
            return self._store_getter()
        raise RuntimeError("TaskUpdate: store_getter 未设置")
