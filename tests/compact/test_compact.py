"""T18 + manage_context 集成测试。"""

import pytest

from suisuicode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    ManageInput,
    ManageOutput,
    RecoveryState,
    SessionContext,
    TriggerKind,
    manage_context,
    new_session_context,
)
from suisuicode.compact.const import AUTO_SAFETY_MARGIN, SUMMARY_RESERVE
from suisuicode.compact.token import estimate_tokens
from suisuicode.conversation import Conversation
from suisuicode.llm import (
    Message,
    Request,
    ROLE_ASSISTANT,
    ROLE_USER,
    StreamEvent,
    Usage,
    ToolDefinition,
)
from tests.compact.conftest import FakeCompactProvider


def _summary_ok_script():
    """返回一个标准的摘要成功脚本。"""
    return [
        # 第一次 stream：摘要请求（tools=None）
        [
            StreamEvent(text="<analysis>draft</analysis>\n<summary>## 1 主要请求和意图\ntest\n## 2 关键技术概念\nnone\n## 3 文件和代码段\nnone\n## 4 错误和修复\nnone\n## 5 问题解决过程\nnone\n## 6 所有用户消息原文\nhello\n## 7 待办任务\nnone\n## 8 当前工作\ntesting\n## 9 可能的下一步\ncontinue</summary>"),
            StreamEvent(
                usage=Usage(input_tokens=100, output_tokens=50)
            ),
            StreamEvent(done=True),
        ],
    ]


def _make_manage_input(
    provider, conv, tool_defs, replacement, recovery, auto_tracking,
    session, context_window=200000, estimated_token=1000, trigger=TriggerKind.AUTO,
):
    return ManageInput(
        conv=conv,
        provider=provider,
        model="fake",
        context_window=context_window,
        tool_defs=tool_defs,
        replacement=replacement,
        recovery=recovery,
        auto_tracking=auto_tracking,
        session=session,
        usage_anchor=0,
        anchor_msg_len=0,
        estimated_token=estimated_token,
        trigger=trigger,
    )


@pytest.mark.asyncio
async def test_manage_context_auto_skipped_below_threshold(tmp_path):
    """估算 token 远低于阈值时不触发 layer2。"""
    conv = Conversation()
    conv.add_user("hello")
    conv.add_assistant("hi")

    session = new_session_context(str(tmp_path))
    replacement = ContentReplacementState()
    recovery = RecoveryState()
    auto_tracking = CompactCircuitBreaker()
    tool_defs = [ToolDefinition(name="read", description="读", input_schema={})]

    provider = FakeCompactProvider(_summary_ok_script())

    in_ = _make_manage_input(
        provider, conv, tool_defs, replacement, recovery, auto_tracking,
        session, estimated_token=100,
    )
    out = await manage_context(in_)
    # 摘要请求不应被触发（tools=None 的 stream 调用次数为 0）
    assert provider.summarize_calls == 0


@pytest.mark.asyncio
async def test_manage_context_auto_triggers_on_threshold(tmp_path):
    """估算 token 超过阈值时触发 layer2。"""
    conv = Conversation()
    # 构造足够大的消息内容以越过阈值
    cw = 70000  # 使用较小窗口，这样阈值较低
    threshold = cw - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN  # = 37000
    # 需要约 37000 * 3.5 = 129500 字符的内容
    big_content = "x" * 130000
    conv.add_user(big_content)
    conv.add_assistant("hi")

    session = new_session_context(str(tmp_path))
    replacement = ContentReplacementState()
    recovery = RecoveryState()
    auto_tracking = CompactCircuitBreaker()
    tool_defs = [ToolDefinition(name="read", description="读", input_schema={})]

    provider = FakeCompactProvider(_summary_ok_script())

    in_ = _make_manage_input(
        provider, conv, tool_defs, replacement, recovery, auto_tracking,
        session, context_window=cw, estimated_token=threshold + 100,
    )
    out = await manage_context(in_)
    assert provider.summarize_calls == 1
    # 摘要后 conversation 被替换（消息数减少）
    assert out.before_tokens > 0
    assert out.after_tokens > 0


@pytest.mark.asyncio
async def test_manage_context_manual_bypasses_threshold(tmp_path):
    """手动路径无视阈值。"""
    conv = Conversation()
    conv.add_user("hello")
    conv.add_assistant("hi")

    session = new_session_context(str(tmp_path))
    replacement = ContentReplacementState()
    recovery = RecoveryState()
    auto_tracking = CompactCircuitBreaker()
    tool_defs = [ToolDefinition(name="read", description="读", input_schema={})]

    provider = FakeCompactProvider(_summary_ok_script())

    in_ = _make_manage_input(
        provider, conv, tool_defs, replacement, recovery, auto_tracking,
        session, estimated_token=500, trigger=TriggerKind.MANUAL,
    )
    out = await manage_context(in_)
    # 手动路径无条件触发摘要
    assert provider.summarize_calls == 1


@pytest.mark.asyncio
async def test_manage_context_auto_skipped_when_tripped(tmp_path):
    """熔断后跳过自动 layer2。"""
    conv = Conversation()
    cw = 70000
    big_content = "x" * 130000
    conv.add_user(big_content)
    conv.add_assistant("hi")

    session = new_session_context(str(tmp_path))
    replacement = ContentReplacementState()
    recovery = RecoveryState()
    auto_tracking = CompactCircuitBreaker()
    for _ in range(3):
        auto_tracking.record_failure()
    assert auto_tracking.tripped()

    tool_defs = [ToolDefinition(name="read", description="读", input_schema={})]
    provider = FakeCompactProvider(_summary_ok_script())

    in_ = _make_manage_input(
        provider, conv, tool_defs, replacement, recovery, auto_tracking,
        session, context_window=cw, estimated_token=50000,
    )
    out = await manage_context(in_)
    assert provider.summarize_calls == 0


@pytest.mark.asyncio
async def test_manage_context_emergency_bypass_tracking(tmp_path):
    """紧急路径绕过熔断器。"""
    conv = Conversation()
    conv.add_user("hello")
    conv.add_assistant("hi")

    session = new_session_context(str(tmp_path))
    replacement = ContentReplacementState()
    recovery = RecoveryState()
    auto_tracking = CompactCircuitBreaker()
    for _ in range(3):
        auto_tracking.record_failure()
    assert auto_tracking.tripped()

    tool_defs = [ToolDefinition(name="read", description="读", input_schema={})]
    provider = FakeCompactProvider(_summary_ok_script())

    in_ = _make_manage_input(
        provider, conv, tool_defs, replacement, recovery, auto_tracking,
        session, trigger=TriggerKind.EMERGENCY,
    )
    out = await manage_context(in_)
    # 紧急路径无视熔断
    assert provider.summarize_calls == 1
