"""T9 + summary_prompt 单元测试。"""

from suisuicode.compact.summary_prompt import (
    build_summary_prompt,
    extract_summary,
    serialize_conversation,
)
from suisuicode.llm import Message


def test_extract_summary_standard():
    raw = "some text <analysis>draft</analysis> <summary>the summary</summary> more"
    assert extract_summary(raw) == "the summary"


def test_extract_summary_missing():
    raw = "no tags here"
    assert extract_summary(raw) == raw


def test_extract_summary_nested():
    # 非贪婪匹配取最后一个 <summary>...</summary> 对
    raw = "<analysis>x</analysis> <summary>inner <summary>nested</summary> outer</summary>"
    result = extract_summary(raw)
    # re.findall 非贪婪匹配找到两个：["inner <summary>nested", "nested"]
    # [-1] 取最后一个即 "nested"
    assert "nested" in result


def test_build_summary_prompt_shape():
    msgs = [Message(role="user", content="hello")]
    result = build_summary_prompt(msgs)
    assert len(result) == 1
    assert result[0].role == "user"
    content = result[0].content
    assert "<analysis>" in content
    assert "<summary>" in content
    assert "主要请求和意图" in content
    assert "关键技术概念" in content
    assert "文件和代码段" in content
    assert "错误和修复" in content
    assert "问题解决过程" in content
    assert "所有用户消息原文" in content
    assert "待办任务" in content
    assert "当前工作" in content
    assert "可能的下一步" in content
    assert "不要调用任何工具" in content
    assert "[conversation]" in content


def test_serialize_conversation_deterministic():
    msgs = [Message(role="user", content="hi")]
    a = serialize_conversation(msgs)
    b = serialize_conversation(msgs)
    assert a == b
