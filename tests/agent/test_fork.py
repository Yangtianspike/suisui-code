"""agent.fork 测试：消息构造与上下文识别。"""

from suisuicode.agent.fork import (
    FORK_BOILERPLATE,
    FORK_BOILERPLATE_TAG,
    build_forked_messages,
    is_fork_context,
)
from suisuicode.llm import Message, ToolCall, ToolResult, ROLE_USER, ROLE_ASSISTANT, ROLE_TOOL


def test_build_forked_messages_empty_parent():
    msgs = build_forked_messages([], "do something")
    assert len(msgs) == 1
    assert msgs[0].role == ROLE_USER
    assert FORK_BOILERPLATE_TAG in msgs[0].content
    assert "do something" in msgs[0].content


def test_build_forked_messages_with_parent():
    parent = [
        Message(role=ROLE_USER, content="hello"),
        Message(role=ROLE_ASSISTANT, content="hi there"),
    ]
    msgs = build_forked_messages(parent, "fork task")
    assert len(msgs) == 3  # 2 parent + 1 new user
    assert msgs[0].content == "hello"
    assert msgs[1].content == "hi there"
    assert FORK_BOILERPLATE_TAG in msgs[2].content
    assert "fork task" in msgs[2].content


def test_build_forked_messages_unpaired_tool_use():
    """末尾 assistant 有悬空 tool_use → 生成 placeholder。"""
    parent = [
        Message(role=ROLE_USER, content="read file"),
        Message(
            role=ROLE_ASSISTANT,
            content="",
            tool_calls=[ToolCall(id="tc1", name="read_file", input='{"path":"x"}')],
        ),
        # 缺少对应的 RoleTool 消息
    ]
    msgs = build_forked_messages(parent, "task")
    # 应有: user, assistant(含 tool_call), RoleTool(placeholder), user(boilerplate)
    assert len(msgs) == 4
    tool_msg = msgs[2]
    assert tool_msg.role == ROLE_TOOL
    assert len(tool_msg.tool_results) == 1
    assert tool_msg.tool_results[0].tool_call_id == "tc1"
    assert tool_msg.tool_results[0].is_error is True
    assert "[forked, skipped]" in tool_msg.tool_results[0].content


def test_build_forked_messages_paired_tool_use():
    """末尾 assistant tool_use 有配对 → 不生成 placeholder。"""
    parent = [
        Message(role=ROLE_USER, content="read file"),
        Message(
            role=ROLE_ASSISTANT,
            content="",
            tool_calls=[ToolCall(id="tc1", name="read_file", input='{"path":"x"}')],
        ),
        Message(
            role=ROLE_TOOL,
            tool_results=[ToolResult(tool_call_id="tc1", content="file content")],
        ),
        Message(role=ROLE_ASSISTANT, content="done"),
    ]
    msgs = build_forked_messages(parent, "task")
    # 应有: user, assistant(tool_call), tool, assistant, user(boilerplate)
    assert len(msgs) == 5
    assert msgs[3].content == "done"
    assert FORK_BOILERPLATE_TAG in msgs[4].content


def test_build_forked_messages_multiple_unpaired():
    """多个悬空 tool_call → 全部生成 placeholder。"""
    parent = [
        Message(role=ROLE_USER, content="do things"),
        Message(
            role=ROLE_ASSISTANT,
            content="",
            tool_calls=[
                ToolCall(id="tc1", name="read_file", input="{}"),
                ToolCall(id="tc2", name="grep", input="{}"),
            ],
        ),
    ]
    msgs = build_forked_messages(parent, "task")
    assert len(msgs) == 4
    tool_msg = msgs[2]
    assert tool_msg.role == ROLE_TOOL
    assert len(tool_msg.tool_results) == 2


# ── is_fork_context ───────────────────────────────────────


def test_is_fork_context_true_in_user():
    msgs = [Message(role=ROLE_USER, content=FORK_BOILERPLATE + "task")]
    assert is_fork_context(msgs) is True


def test_is_fork_context_true_in_tool():
    msgs = [
        Message(role=ROLE_USER, content="hello"),
        Message(
            role=ROLE_TOOL,
            tool_results=[ToolResult(tool_call_id="x", content=FORK_BOILERPLATE_TAG)],
        ),
    ]
    assert is_fork_context(msgs) is True


def test_is_fork_context_false():
    msgs = [
        Message(role=ROLE_USER, content="hello"),
        Message(role=ROLE_ASSISTANT, content="world"),
    ]
    assert is_fork_context(msgs) is False


def test_is_fork_context_empty():
    assert is_fork_context([]) is False
