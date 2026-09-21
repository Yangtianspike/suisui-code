"""T6-T8 + layer1 单元测试。"""

import os
import tempfile
from pathlib import Path

from suisuicode.compact.layer1 import (
    _head_preview,
    build_preview,
    offload_and_snip,
    spill_single,
)
from suisuicode.compact.state import ContentReplacementState, SessionContext
from suisuicode.llm import Message, ROLE_TOOL, ToolResult


def _make_msg(results: list[tuple[str, str]]) -> Message:
    """构造一条 role=tool 的消息，每个 tuple 为 (tool_call_id, content)。"""
    trs = [ToolResult(tool_call_id=tid, content=c) for tid, c in results]
    return Message(role=ROLE_TOOL, tool_results=trs)


def test_spill_single_idempotent(tmp_path):
    sid = "test-session"
    spill_dir = str(tmp_path / "tool-results")
    Path(spill_dir).mkdir(parents=True)
    session = SessionContext(session_id=sid, session_dir=str(Path(spill_dir).parent), spill_dir=spill_dir)

    spill_single(session, "call-1", "hello world")
    path = Path(spill_dir) / "call-1"
    mtime1 = os.stat(path).st_mtime_ns
    # 第二次不重写
    spill_single(session, "call-1", "different content")
    mtime2 = os.stat(path).st_mtime_ns
    assert mtime1 == mtime2
    assert path.read_bytes() == b"hello world"


def test_offload_single_result(tmp_path):
    spill_dir = str(tmp_path / "tool-results")
    Path(spill_dir).mkdir(parents=True)
    session = SessionContext(session_id="s1", session_dir=str(Path(spill_dir).parent), spill_dir=spill_dir)
    state = ContentReplacementState()

    big = "x" * 60000
    msgs = [_make_msg([("call-1", big)])]

    out, spilled = offload_and_snip(msgs, state, session)
    result = out[0].tool_results[0]

    # 验证替换
    assert "original size:" in result.content
    assert "60000" in result.content
    assert "[saved to]" in result.content
    assert "[head preview]" in result.content
    assert "文件读取工具" in result.content

    # 预览体包含四项信息，其中头部预览内容 ≤20 行且 ≤2048 字节
    preview_text = result.content
    head_marker = "[head preview]\n"
    head_start = preview_text.index(head_marker) + len(head_marker)
    head_content = preview_text[head_start:]
    tail_marker = "完整内容已保存"
    if tail_marker in head_content:
        head_content = head_content[: head_content.index(tail_marker)]
    assert head_content.count("\n") <= 20
    assert len(head_content.encode("utf-8")) <= 2048 + 1  # +1: build_preview 补的 \n

    # 落盘文件存在
    spilled = Path(spill_dir) / "call-1"
    assert spilled.exists()
    assert len(spilled.read_bytes()) == 60000


def test_offload_aggregate(tmp_path):
    spill_dir = str(tmp_path / "tool-results")
    Path(spill_dir).mkdir(parents=True)
    session = SessionContext(session_id="s1", session_dir=str(Path(spill_dir).parent), spill_dir=spill_dir)
    state = ContentReplacementState()

    # 3 条 80000 字节 → 合计 240000 > 200000
    big = "y" * 80000
    msgs = [_make_msg([("a", big), ("b", big), ("c", big)])]

    out, _spilled = offload_and_snip(msgs, state, session)
    results = out[0].tool_results

    # 至少 2 条被替换
    replaced = sum(
        1 for r in results if "original size:" in r.content
    )
    assert replaced >= 2

    # 剩余聚合 ≤ 200000
    kept_bytes = sum(
        len(r.content.encode("utf-8"))
        for r in results
        if "original size:" not in r.content
    )
    assert kept_bytes <= 200000


def test_offload_decision_freeze(tmp_path):
    spill_dir = str(tmp_path / "tool-results")
    Path(spill_dir).mkdir(parents=True)
    session = SessionContext(session_id="s1", session_dir=str(Path(spill_dir).parent), spill_dir=spill_dir)
    state = ContentReplacementState()

    big = "z" * 60000
    msgs = [_make_msg([("call-x", big)])]

    out1, _ = offload_and_snip(msgs, state, session)
    out2, _ = offload_and_snip(msgs, state, session)

    c1 = out1[0].tool_results[0].content
    c2 = out2[0].tool_results[0].content
    assert c1 == c2


def test_preview_stable():
    p1 = build_preview(1000, "head content", "/tmp/test")
    p2 = build_preview(1000, "head content", "/tmp/test")
    assert p1 == p2


def test_head_preview_truncation():
    """验证 _head_preview 截断逻辑。"""
    # 超过 20 行的内容
    long_lines = "\n".join(f"line {i}" for i in range(30))
    head = _head_preview(long_lines)
    assert head.count("\n") <= 20

    # 超过 2048 字节的内容
    huge = "A" * 3000
    head2 = _head_preview(huge)
    assert len(head2.encode("utf-8")) <= 2048
