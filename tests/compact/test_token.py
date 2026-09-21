"""T5 + token 估算 单元测试。"""

from suisuicode.compact.token import estimate_tokens, message_chars, usage_anchor
from suisuicode.llm import Message, Usage


def test_estimate_tokens_zero():
    assert estimate_tokens(0, [], 0) == 0


def test_estimate_tokens_pure_chars():
    msg = Message(role="user", content="hello world")
    # 11 chars / 3.5 = ceil(3.14) = 4
    assert estimate_tokens(0, [msg], 0) == 4


def test_estimate_tokens_anchor():
    m1 = Message(role="user", content="first")
    m2 = Message(role="assistant", content="second message")
    # anchor=1000, anchor_msg_len=1 → only m2 counted
    # m2 = 14 chars / 3.5 = ceil(4) = 4
    assert estimate_tokens(1000, [m1, m2], 1) == 1004


def test_estimate_tokens_anchor_msg_len_exceeds():
    msg = Message(role="user", content="test")
    assert estimate_tokens(500, [msg], 99) == 500


def test_usage_anchor_sum():
    u = Usage(input_tokens=100, output_tokens=50, cache_read=20, cache_write=10)
    assert usage_anchor(u) == 180
