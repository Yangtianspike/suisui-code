"""T12-T17 + layer2 单元测试。"""

import pytest

from suisuicode.compact.layer2 import (
    _join_after_summary,
    group_by_user_turn,
    pick_recent_tail,
)
from suisuicode.llm import Message, ROLE_USER, ROLE_ASSISTANT, ROLE_TOOL, ToolCall, ToolResult


def test_pick_recent_tail_boundary():
    """构造足够 token 和条数的场景，验证两个下界都满足才停手。"""
    msgs = [
        Message(role=ROLE_USER, content="u1"),
        Message(role=ROLE_ASSISTANT, content="a1"),
        Message(role=ROLE_TOOL, tool_results=[ToolResult(tool_call_id="t1", content="r1")]),
        Message(role=ROLE_USER, content="u2"),
        Message(role=ROLE_ASSISTANT, content="a2"),
        Message(role=ROLE_TOOL, tool_results=[ToolResult(tool_call_id="t2", content="r2")]),
    ]
    tail = pick_recent_tail(msgs)
    assert len(tail) <= len(msgs)


def test_pick_recent_tail_pair_fix():
    """截断点落在孤立的 tool 消息时应前推到 tool_use。"""
    msgs = [
        Message(role=ROLE_USER, content="u"),
        Message(role=ROLE_ASSISTANT, content="a", tool_calls=[
            ToolCall(id="c1", name="read", input="{}")
        ]),
        Message(role=ROLE_TOOL, tool_results=[ToolResult(tool_call_id="c1", content="result")]),
    ]
    tail = pick_recent_tail(msgs)
    # 第一条不应该是 tool
    assert tail[0].role != ROLE_TOOL


def test_join_after_summary_avoids_consecutive_user():
    summary_msg = Message(role=ROLE_USER, content="summary")
    recent = [Message(role=ROLE_USER, content="next user")]
    result = _join_after_summary(summary_msg, recent)
    # 中间应该插入了 assistant 占位
    assert len(result) == 3
    assert result[0].role == ROLE_USER
    assert result[1].role == ROLE_ASSISTANT
    assert result[2].role == ROLE_USER


def test_join_after_summary_empty_recent():
    summary_msg = Message(role=ROLE_USER, content="summary")
    result = _join_after_summary(summary_msg, [])
    assert len(result) == 1
    assert result[0] == summary_msg


def test_join_after_summary_assistant_start():
    summary_msg = Message(role=ROLE_USER, content="summary")
    recent = [Message(role=ROLE_ASSISTANT, content="ok")]
    result = _join_after_summary(summary_msg, recent)
    assert len(result) == 2
    assert result[1].role == ROLE_ASSISTANT


def test_group_by_user_turn():
    msgs = [
        Message(role=ROLE_USER, content="u1"),
        Message(role=ROLE_ASSISTANT, content="a1"),
        Message(role=ROLE_TOOL, tool_results=[ToolResult(tool_call_id="t1", content="r1")]),
        Message(role=ROLE_USER, content="u2"),
        Message(role=ROLE_ASSISTANT, content="a2"),
    ]
    groups = group_by_user_turn(msgs)
    assert len(groups) == 2
    assert len(groups[0]) == 3
    assert len(groups[1]) == 2


def test_group_by_user_turn_empty():
    assert group_by_user_turn([]) == []
