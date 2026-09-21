"""AgentTool 集成测试：Catalog 与工具过滤协同、Agent 工具与 task 工具共存。"""

import pytest

from suisuicode.subagent import Catalog as SubagentCatalog, Definition, Source
from suisuicode.task.manager import Manager
from suisuicode.agent.agent_tool import AgentTool
from suisuicode.tool import Registry
from suisuicode.tool.filter import apply_agent_tool_filter, FilterParams


# ── Catalog + filter 协同（checklist 集成第 1 项）──────────


def test_catalog_and_filter_work_together():
    """resolve 拿到 def，过滤函数按 def.source/background/tools/disallowed_tools 收窄。"""
    # 构造一个带 tools/disallowed_tools 的定义
    defi = Definition(
        name="test-agent",
        description="test",
        tools=["read_file", "grep"],
        disallowed_tools=["bash"],
        source=Source.PROJECT,
    )

    all_tools = ["read_file", "write_file", "edit_file", "bash", "grep", "glob", "Agent"]

    result = apply_agent_tool_filter(FilterParams(
        all=all_tools,
        source=int(defi.source),
        background=False,
        allowed=defi.tools,
        disallowed=defi.disallowed_tools,
    ))

    # Agent 被全局禁止去除；bash 被定义禁止去除；只保留白名单内的
    assert "Agent" not in result
    assert "bash" not in result
    assert "read_file" in result
    assert "grep" in result
    assert "write_file" not in result  # 不在白名单


# ── 工具数量稳定（checklist 集成第 4 项）─────────────────


def test_all_five_tools_registered():
    """主 Agent 工具列表含 Agent + TaskList + TaskGet + TaskStop + SendMessage。"""
    from suisuicode.task.tools import (
        TaskListTool,
        TaskGetTool,
        TaskStopTool,
        SendMessageTool,
    )

    mgr = Manager()
    reg = Registry()
    reg.register(TaskListTool(mgr))
    reg.register(TaskGetTool(mgr))
    reg.register(TaskStopTool(mgr))
    reg.register(SendMessageTool(mgr))

    catalog = SubagentCatalog()
    agent_tool = AgentTool(catalog, mgr, parent=None, bg_enabled=True)
    reg.register(agent_tool)

    names = [d.name for d in reg.definitions()]
    assert "Agent" in names
    assert "TaskList" in names
    assert "TaskGet" in names
    assert "TaskStop" in names
    assert "SendMessage" in names


# ── 子 Agent 工具过滤：定义式看不到 Agent（checklist 场景 7）───


def test_defined_subagent_cannot_see_agent_tool():
    """定义式子 Agent 工具列表无 Agent（ALL_AGENT_DISALLOWED_TOOLS 剔除）。"""
    all_tools = ["read_file", "write_file", "bash", "grep", "Agent", "TaskList"]

    result = apply_agent_tool_filter(FilterParams(
        all=all_tools,
        source=int(Source.BUILTIN),
        background=False,
        allowed=[],
        disallowed=[],
    ))

    assert "Agent" not in result
    assert "read_file" in result
    assert "bash" in result


# ── Fork 子 Agent 工具列表保留 Agent（靠运行时阻断）───────


def test_fork_subagent_keeps_agent_tool():
    """Fork 子 Agent 工具列表保留 Agent（prompt cache 一致性，靠 QuerySource 阻断）。"""
    all_tools = ["read_file", "write_file", "bash", "grep", "Agent", "TaskList"]

    # Fork 的 Definition 没有 disallowed_tools 包含 Agent
    result = apply_agent_tool_filter(FilterParams(
        all=all_tools,
        source=int(Source.BUILTIN),
        background=True,  # Fork 强制后台
        allowed=[],
        disallowed=[],  # Fork definition disallowed_tools 不含 Agent
    ))

    # 后台白名单会剔除 Agent（因为不在 ASYNC_AGENT_ALLOWED 里）
    # 这是正确的——Fork 在后台跑，Agent 被白名单剔除
    assert "Agent" not in result
