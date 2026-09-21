"""TaskList 工具（F28）。"""

from __future__ import annotations

import json

from suisuicode.team.tasks import Filter, Status
from suisuicode.tool import Result, Tool


class TaskListTool(Tool):
    """列出共享任务。"""

    def __init__(self, store_getter=None) -> None:
        self._store_getter = store_getter

    def name(self) -> str:
        return "TeamTaskList"

    def description(self) -> str:
        return (
            "列出团队共享任务列表。可按 pending/in_progress/completed/blocked 过滤。"
            "返回任务含 is_ready 字段（blocked_by 全部 completed）。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "按状态过滤：pending / in_progress / completed / blocked（可选）",
                },
                "assignee": {
                    "type": "string",
                    "description": "按负责人过滤（可选）",
                },
            },
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

        try:
            store = self._get_store()

            f = Filter()
            raw_status = data.get("status")
            if raw_status:
                try:
                    f.status = Status(str(raw_status))
                except ValueError:
                    pass
            raw_assignee = data.get("assignee")
            if raw_assignee:
                f.assignee = str(raw_assignee)

            tasks = await store.list_(f)

            result = []
            for t in tasks:
                d = t.to_dict()
                d["is_ready"] = getattr(t, "is_ready", True)
                result.append(d)

            return Result(content=json.dumps({"tasks": result}))
        except Exception as e:
            return Result(is_error=True, content=f"TaskList 失败: {e}")

    def _get_store(self):
        if self._store_getter is not None:
            return self._store_getter()
        raise RuntimeError("TaskList: store_getter 未设置")
