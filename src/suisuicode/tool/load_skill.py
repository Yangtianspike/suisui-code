"""LoadSkill 工具：Agent 按需激活 Skill，SOP 钉到环境上下文。"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from suisuicode.tool import Result, Tool

if TYPE_CHECKING:
    from suisuicode.agent.agent import Agent
    from suisuicode.skills.catalog import Catalog


class LoadSkillTool(Tool):
    """系统工具：按需激活 Skill。is_system_tool=True，不受权限拦截。"""

    is_system_tool = True

    def __init__(self) -> None:
        self._loader: Catalog | None = None
        self._agent: Agent | None = None

    def set_loader(self, loader: Catalog) -> None:
        self._loader = loader

    def set_agent(self, agent: Agent) -> None:
        self._agent = agent

    def name(self) -> str:
        return "LoadSkill"

    def description(self) -> str:
        return (
            "按需激活一个 Skill。激活后 Skill 的 SOP（标准操作流程）"
            "会被钉到环境上下文，在当前对话中持续可见。"
            "当用户的请求与某个可用 Skill 的描述匹配时调用此工具。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要激活的 Skill 名称",
                },
            },
            "required": ["name"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, args: str) -> Result:
        if self._loader is None or self._agent is None:
            return Result(
                is_error=True,
                content="LoadSkill not properly initialized",
            )

        try:
            params = json.loads(args)
        except json.JSONDecodeError:
            return Result(is_error=True, content="LoadSkill 参数必须是有效 JSON")

        name = params.get("name")
        if not name or not isinstance(name, str):
            return Result(is_error=True, content="LoadSkill 缺少必填参数: name")

        skill = self._loader.get(name)
        if skill is None:
            catalog_names = self._loader.names()
            names_str = ", ".join(catalog_names) if catalog_names else "(空)"
            return Result(
                is_error=True,
                content=f"未知 skill: {name}。可用 skill: {names_str}",
            )

        self._agent.activate_skill(skill.name, skill.prompt_body)
        return Result(
            content=f"Skill '{name}' activated. SOP pinned to environment context."
        )
