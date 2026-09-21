"""tool.filter 测试：多层防线过滤、后台白名单、黑白名单组合。"""

import pytest
from suisuicode.tool.filter import (
    ALL_AGENT_DISALLOWED_TOOLS,
    ASYNC_AGENT_ALLOWED_TOOLS,
    CUSTOM_AGENT_DISALLOWED_TOOLS,
    FilterParams,
    apply_agent_tool_filter,
    is_mcp_or_skill,
)


def test_constants():
    assert "Agent" in ALL_AGENT_DISALLOWED_TOOLS
    assert CUSTOM_AGENT_DISALLOWED_TOOLS == []
    assert "read_file" in ASYNC_AGENT_ALLOWED_TOOLS
    assert "bash" in ASYNC_AGENT_ALLOWED_TOOLS
    assert "Agent" not in ASYNC_AGENT_ALLOWED_TOOLS
    assert "TaskList" not in ASYNC_AGENT_ALLOWED_TOOLS


def test_default_removes_agent():
    p = FilterParams(all=["read_file", "write_file", "Agent", "bash"])
    result = apply_agent_tool_filter(p)
    assert "Agent" not in result
    assert "read_file" in result
    assert "write_file" in result


def test_background_intersection():
    p = FilterParams(
        all=["read_file", "write_file", "edit_file", "Agent", "TaskList", "bash", "glob", "grep"],
        background=True,
    )
    result = apply_agent_tool_filter(p)
    assert "read_file" in result
    assert "bash" in result
    assert "Agent" not in result
    assert "TaskList" not in result


def test_background_preserves_mcp():
    p = FilterParams(
        all=["read_file", "mcp__filesystem__read", "Agent"],
        background=True,
    )
    result = apply_agent_tool_filter(p)
    assert "read_file" in result
    assert "mcp__filesystem__read" in result
    assert "Agent" not in result


def test_disallowed_blacklist():
    p = FilterParams(
        all=["read_file", "write_file", "bash"],
        disallowed=["bash"],
    )
    result = apply_agent_tool_filter(p)
    assert "read_file" in result
    assert "write_file" in result
    assert "bash" not in result


def test_allowed_whitelist():
    p = FilterParams(
        all=["read_file", "write_file", "bash", "grep"],
        allowed=["read_file", "grep"],
    )
    result = apply_agent_tool_filter(p)
    assert result == ["read_file", "grep"]


def test_allowed_and_disallowed_combined():
    """白名单先收窄，黑名单再剔除（spec F29）。"""
    p = FilterParams(
        all=["read_file", "write_file", "bash", "grep"],
        allowed=["read_file", "write_file", "bash"],
        disallowed=["bash"],
    )
    result = apply_agent_tool_filter(p)
    assert "read_file" in result
    assert "write_file" in result
    assert "bash" not in result


def test_empty_allowed_no_narrow():
    """空白名单 = 不再收窄。"""
    p = FilterParams(
        all=["read_file", "write_file", "bash"],
        allowed=[],
        disallowed=["bash"],
    )
    result = apply_agent_tool_filter(p)
    assert "read_file" in result
    assert "write_file" in result
    assert "bash" not in result


def test_custom_disallowed_not_applied_to_builtin():
    """source < 2 时不应用 CUSTOM_AGENT_DISALLOWED_TOOLS。"""
    # CUSTOM 本期为空，所以此测试主要验证代码路径
    p = FilterParams(
        all=["read_file", "write_file", "bash"],
        source=0,  # BUILTIN
    )
    result = apply_agent_tool_filter(p)
    assert "read_file" in result
    assert "write_file" in result


# ── is_mcp_or_skill ───────────────────────────────────────


def test_is_mcp_or_skill():
    assert is_mcp_or_skill("mcp__server__tool") is True
    assert is_mcp_or_skill("mcp__") is True
    assert is_mcp_or_skill("read_file") is False
    assert is_mcp_or_skill("bash") is False
