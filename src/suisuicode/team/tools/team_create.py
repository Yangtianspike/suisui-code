"""TeamCreate 工具（F20-F21）。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from suisuicode.tool import Result, Tool

if TYPE_CHECKING:
    from suisuicode.team.manager import Manager


class TeamCreateTool(Tool):
    """创建新 Team。"""

    def __init__(self, mgr: Manager) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TeamCreate"

    def description(self) -> str:
        return "创建一个新团队。Lead 升任队长，后续可通过 Agent(team_name=...) 添加队员。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {
                    "type": "string",
                    "description": "团队名（支持中文/英文/空格，自动转为安全路径名）",
                },
                "description": {
                    "type": "string",
                    "description": "团队描述（可选）",
                },
            },
            "required": ["team_name"],
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

        team_name = str(data.get("team_name", ""))
        if not team_name:
            return Result(is_error=True, content="team_name is required")

        description = str(data.get("description", ""))

        try:
            team = await self._mgr.create(team_name, description)
            return Result(content=json.dumps({
                "team_name": team.sanitized_name,
                "backend": str(team.backend),
                "config_path": team.config_path,
            }))
        except Exception as e:
            return Result(is_error=True, content=f"TeamCreate 失败: {e}")
