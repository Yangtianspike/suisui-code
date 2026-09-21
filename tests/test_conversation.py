from suisuicode.conversation import Conversation
from suisuicode.llm import ToolCall, ToolResult, ROLE_USER, ROLE_ASSISTANT, ROLE_TOOL


def test_add_and_messages():
    conv = Conversation()
    conv.add_user("hello")
    conv.add_assistant("hi there")
    conv.add_user("how are you")
    msgs = conv.messages()
    assert len(msgs) == 3
    assert msgs[0].role == "user"
    assert msgs[0].content == "hello"
    assert msgs[1].role == "assistant"
    assert msgs[2].role == "user"


def test_messages_returns_copy():
    conv = Conversation()
    conv.add_user("test")
    msgs = conv.messages()
    msgs.clear()
    assert len(conv.messages()) == 1


def test_empty():
    conv = Conversation()
    assert conv.messages() == []


def test_tool_calls_and_results():
    conv = Conversation()
    conv.add_user("read file")
    conv.add_assistant_with_tool_calls(
        "",
        [
            ToolCall(id="tc1", name="read_file", input='{"path":"x"}'),
        ],
    )
    conv.add_tool_results(
        [
            ToolResult(tool_call_id="tc1", content="file content"),
        ]
    )
    conv.add_assistant("done")
    msgs = conv.messages()
    assert len(msgs) == 4
    assert msgs[0].role == ROLE_USER
    assert msgs[1].role == ROLE_ASSISTANT
    assert len(msgs[1].tool_calls) == 1
    assert msgs[1].tool_calls[0].name == "read_file"
    assert msgs[2].role == ROLE_TOOL
    assert len(msgs[2].tool_results) == 1
    assert msgs[2].tool_results[0].content == "file content"
    assert msgs[3].role == ROLE_ASSISTANT
    assert msgs[3].content == "done"


# ── ch08 replace_history 测试 ──────────────────────────

def test_replace_history():
    conv = Conversation()
    conv.add_user("hello")
    conv.add_assistant("hi")
    assert conv.length() == 2

    from suisuicode.llm import Message
    new_msgs = [Message(role=ROLE_USER, content="replaced")]
    conv.replace_history(new_msgs)
    assert conv.length() == 1
    assert conv.messages()[0].content == "replaced"


def test_replace_history_deep_copy():
    conv = Conversation()
    conv.add_user("hello")

    from suisuicode.llm import Message
    new_msgs = [Message(role=ROLE_USER, content="original")]
    conv.replace_history(new_msgs)
    # 修改原列表不影响 conversation 内部
    new_msgs[0].content = "modified"
    assert conv.messages()[0].content == "original"


def test_replace_history_empty():
    conv = Conversation()
    conv.add_user("hello")
    conv.replace_history([])
    assert conv.length() == 0


# ── ch09 回调测试 ─────────────────────────────────────


def test_on_append_callback():
    """验证每次 add_* 触发 on_append 回调。"""
    captured = []

    def on_append(msg):
        captured.append(msg)

    conv = Conversation(on_append=on_append)
    conv.add_user("hello")
    conv.add_assistant("hi")
    conv.add_assistant_with_tool_calls(
        "calling",
        [ToolCall(id="tc1", name="read_file", input='{"path":"x"}')],
    )
    conv.add_tool_results(
        [ToolResult(tool_call_id="tc1", content="result")]
    )

    assert len(captured) == 4
    assert captured[0].role == ROLE_USER
    assert captured[0].content == "hello"
    assert captured[1].role == ROLE_ASSISTANT
    assert captured[1].content == "hi"
    assert captured[2].role == ROLE_ASSISTANT
    assert len(captured[2].tool_calls) == 1
    assert captured[3].role == ROLE_TOOL


def test_on_replace_callback():
    """验证 replace_history 触发 on_replace 回调。"""
    captured = []

    def on_replace(msgs):
        captured.append(list(msgs))

    conv = Conversation(on_replace=on_replace)
    conv.add_user("hello")
    conv.add_assistant("hi")

    from suisuicode.llm import Message

    new_msgs = [Message(role=ROLE_USER, content="replaced")]
    conv.replace_history(new_msgs)

    assert len(captured) == 1
    assert len(captured[0]) == 1
    assert captured[0][0].content == "replaced"


def test_no_callback_backward_compat():
    """验证不传回调时行为与 ch08 完全一致。"""
    conv = Conversation()  # 无回调
    conv.add_user("hello")
    conv.add_assistant("hi")
    assert conv.length() == 2
    # replace_history 也应正常
    from suisuicode.llm import Message

    conv.replace_history([Message(role=ROLE_USER, content="x")])
    assert conv.length() == 1


def test_from_messages():
    """验证 from_messages 从已有消息列表创建会话。"""
    from suisuicode.llm import Message

    msgs = [
        Message(role=ROLE_USER, content="hello"),
        Message(role=ROLE_ASSISTANT, content="hi"),
    ]
    conv = Conversation.from_messages(msgs)
    assert conv.length() == 2
    assert conv.messages()[0].content == "hello"
    assert conv.messages()[1].content == "hi"
    # 修改原列表不影响 conversation 内部
    msgs.clear()
    assert conv.length() == 2


def test_from_messages_with_callbacks():
    """验证 from_messages 支持回调。"""
    from suisuicode.llm import Message

    captured = []

    def on_append(msg):
        captured.append(msg)

    msgs = [Message(role=ROLE_USER, content="existing")]
    conv = Conversation.from_messages(msgs, on_append=on_append)
    # 已有消息不触发回调
    assert len(captured) == 0
    # 新增消息触发
    conv.add_assistant("new")
    assert len(captured) == 1
    assert captured[0].content == "new"
