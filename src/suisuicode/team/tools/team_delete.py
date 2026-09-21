"""TeamDelete 工具（F22-F23）。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from suisuicode.tool import Result, Tool

if TYPE_CHECKING:
    from suisuicode.team.manager import Manager


class TeamDeleteTool(Tool):
    """删除 Team。"""

    def __init__(self, mgr: Manager) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TeamDelete"

    def description(self) -> str:
        return "删除团队。force=true 时强制删除即使有活跃队员。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "team_name": {
                    "type": "string",
                    "description": "要删除的团队名",
                },
                "force": {
                    "type": "boolean",
                    "description": "强制删除，即使有活跃队员（默认 false）",
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
        force = bool(data.get("force", False))

        if not team_name:
            return Result(is_error=True, content="team_name is required")

        try:
            await self._mgr.delete(team_name, force=force)
            return Result(content=json.dumps({
                "deleted": team_name,
                "force": force,
            }))
        except Exception as e:
            return Result(is_error=True, content=f"TeamDelete 失败: {e}")
