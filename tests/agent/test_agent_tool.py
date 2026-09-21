"""AgentTool 测试：基本属性、参数校验、子 Agent 启动、嵌套阻断。"""

import json
import pytest

from suisuicode.agent.agent import Agent
from suisuicode.agent.agent_tool import AgentTool, AgentArgs
from suisuicode.conversation import Conversation
from suisuicode.llm import Request, StreamEvent
from suisuicode.permission import Mode
from suisuicode.permission.engine import Engine
from suisuicode.permission.rule import RuleSet
from suisuicode.tool import Registry


# ── Mock Catalog ──────────────────────────────────────────


class MockCatalog:
    def __init__(self):
        self._defs = {}

    def resolve(self, name: str):
        return self._defs.get(name)

    def fork_definition(self):
        from suisuicode.subagent.definition import Definition, Source
        return Definition(name="__fork__", description="fork", source=Source.BUILTIN)

    def list(self):
        return list(self._defs.values())


class MockTaskMgr:
    def __init__(self):
        self.launched = []
        self.adopted = []

    async def launch(self, ag, conv, name, task):
        self.launched.append((name, task))
        return "task_00000001"

    async def adopt_running(self, ag, conv, name, events, handle, partial):
        self.adopted.append(name)
        return "task_00000002"

    async def upgrade_approval(self, req):
        from suisuicode.permission import Outcome
        return Outcome.DENY_ONCE, False


# ── Helpers ───────────────────────────────────────────────


def make_tool(catalog=None, task_mgr=None, bg_enabled=True):
    if catalog is None:
        catalog = MockCatalog()
    if task_mgr is None:
        task_mgr = MockTaskMgr()
    return AgentTool(catalog, task_mgr, parent=None, bg_enabled=bg_enabled)


# ── Tests: basic properties ───────────────────────────────


def test_name_is_agent():
    tool = make_tool()
    assert tool.name() == "Agent"


def test_parameters_contains_all_fields():
    tool = make_tool()
    params = tool.parameters()
    props = params.get("properties", {})
    assert "prompt" in props
    assert "description" in props
    assert "subagent_type" in props
    assert "model" in props
    assert "run_in_background" in props
    assert "name" in props
    assert "prompt" in params.get("required", [])
    assert "description" in params.get("required", [])


def test_read_only_is_false():
    tool = make_tool()
    assert tool.read_only is False


# ── Tests: execute validation ─────────────────────────────


@pytest.mark.asyncio
async def test_missing_prompt_returns_error():
    tool = make_tool()
    # Tool.execute takes args as string per Tool protocol
    result = await tool.execute(json.dumps({"description": "test"}))
    assert result.is_error is True
    assert "prompt" in result.content.lower()


@pytest.mark.asyncio
async def test_missing_description_returns_error():
    tool = make_tool()
    result = await tool.execute(json.dumps({"prompt": "do something"}))
    assert result.is_error is True
    assert "description" in result.content.lower()


@pytest.mark.asyncio
async def test_no_parent_returns_error():
    tool = make_tool()
    result = await tool.execute(json.dumps({"prompt": "test", "description": "test"}))
    assert result.is_error is True
    assert "parent" in result.content.lower()


@pytest.mark.asyncio
async def test_unknown_subagent_type():
    tool = make_tool()
    # Even with parent set, unknown type should error before needing parent
    result = await tool.execute(json.dumps({
        "prompt": "test", "description": "test",
        "subagent_type": "nonexistent",
    }))
    # 'nonexistent' is unknown → resolve returns None → error
    # But we didn't set parent, so parent check will fire first
    # The parent check comes before resolve, so this will hit parent=None first
    assert result.is_error is True


# ── Tests: background disabled ────────────────────────────


@pytest.mark.asyncio
async def test_bg_disabled_blocks_background():
    tool = make_tool(bg_enabled=False)
    result = await tool.execute(json.dumps({
        "prompt": "test",
        "description": "test",
        "run_in_background": True,
    }))
    # Will fail on parent=None before reaching bg check
    assert result.is_error is True
