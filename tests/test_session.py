"""ch09 会话子包测试：Writer、列表扫描、加载恢复、过期清理。"""

import json
import os
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

from suisuicode import llm
from suisuicode.session.writer import Writer
from suisuicode.session.list import list_sessions
from suisuicode.session.load import load_session, _truncate_orphaned_tool_calls
from suisuicode.session.cleanup import clean_expired


# ── 辅助 ──────────────────────────────────────────


def _make_session_dir() -> str:
    """创建临时会话目录。"""
    return tempfile.mkdtemp()


def _user_msg(content: str) -> llm.Message:
    return llm.Message(role="user", content=content)


def _assistant_msg(content: str) -> llm.Message:
    return llm.Message(role="assistant", content=content)


def _tool_msg(results: list[llm.ToolResult]) -> llm.Message:
    return llm.Message(role="tool", tool_results=results)


# ── Writer 测试 ───────────────────────────────────


def test_writer_append_and_read():
    """写入 3 条消息 → 逐行读回验证 JSON 结构。"""
    d = _make_session_dir()
    with Writer(d) as w:
        w.append(_user_msg("hello"), model="test-model", is_first=True)
        w.append(_assistant_msg("hi"))
        w.append(_user_msg("how are you"))

    jsonl = os.path.join(d, "conversation.jsonl")
    with open(jsonl, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]

    assert len(lines) == 3
    assert lines[0]["role"] == "user"
    assert lines[0]["content"] == "hello"
    assert lines[0]["model"] == "test-model"
    assert lines[1]["role"] == "assistant"
    assert lines[2]["role"] == "user"


def test_writer_compact_marker():
    """写入消息 → compact 标记 → 新消息 → load_session 只返回 compact 后的。"""
    d = _make_session_dir()
    with Writer(d) as w:
        w.append(_user_msg("before"), model="m", is_first=True)
        w.append(_assistant_msg("old"))
        w.write_compact_marker()
        w.append(_user_msg("after compact"), model="m", is_first=False)
        w.append(_assistant_msg("new"))

    msgs = load_session(d)
    assert len(msgs) == 2
    assert msgs[0].content == "after compact"
    assert msgs[1].content == "new"


def test_writer_tool_calls_and_results():
    """工具调用记录：JSONL 中出现 tool_calls 和 tool_results。"""
    d = _make_session_dir()
    with Writer(d) as w:
        w.append(_user_msg("read file"), model="m", is_first=True)
        w.append(
            llm.Message(
                role="assistant",
                content="calling",
                tool_calls=[
                    llm.ToolCall(id="tc1", name="read_file", input='{"path":"x"}')
                ],
            )
        )
        w.append(
            llm.Message(
                role="tool",
                tool_results=[
                    llm.ToolResult(tool_call_id="tc1", content="file content")
                ],
            )
        )

    jsonl = os.path.join(d, "conversation.jsonl")
    with open(jsonl, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]

    assert len(lines) == 3
    assert "tool_calls" in lines[1]
    assert len(lines[1]["tool_calls"]) == 1
    assert lines[1]["tool_calls"][0]["name"] == "read_file"
    assert "tool_results" in lines[2]
    assert lines[2]["tool_results"][0]["content"] == "file content"


# ── load_session 测试 ─────────────────────────────


def test_load_session_bad_line_skip():
    """插入坏行 → 被跳过，其余正常。"""
    d = _make_session_dir()
    jsonl = os.path.join(d, "conversation.jsonl")

    # 手动写 JSONL（含坏行）
    lines = [
        '{"role":"user","content":"hello","ts":1}\n',
        "{this is bad json\n",
        '{"role":"assistant","content":"hi","ts":2}\n',
    ]
    with open(jsonl, "w", encoding="utf-8") as f:
        f.writelines(lines)

    msgs = load_session(d)
    assert len(msgs) == 2
    assert msgs[0].content == "hello"
    assert msgs[1].content == "hi"


def test_load_session_orphaned_tool_calls():
    """末尾是带 tool_calls 的 assistant → 被截断。"""
    msgs = [
        llm.Message(role="user", content="read"),
        llm.Message(
            role="assistant",
            content="",
            tool_calls=[llm.ToolCall(id="tc1", name="read_file", input="{}")],
        ),
    ]
    result = _truncate_orphaned_tool_calls(msgs)
    assert len(result) == 1
    assert result[0].role == "user"


def test_load_session_orphaned_penultimate():
    """倒数第二条是带 tool_calls 的 assistant，最后一条不是 tool → 截断。"""
    msgs = [
        llm.Message(role="user", content="read"),
        llm.Message(
            role="assistant",
            content="",
            tool_calls=[llm.ToolCall(id="tc1", name="read_file", input="{}")],
        ),
        llm.Message(role="assistant", content="orphaned reply"),
    ]
    result = _truncate_orphaned_tool_calls(msgs)
    assert len(result) == 1


def test_load_session_normal_tool_sequence():
    """正常工具调用序列：user → assistant(tool_calls) → tool → assistant → 不截断。"""
    msgs = [
        llm.Message(role="user", content="read"),
        llm.Message(
            role="assistant",
            content="",
            tool_calls=[llm.ToolCall(id="tc1", name="read_file", input="{}")],
        ),
        llm.Message(
            role="tool",
            tool_results=[llm.ToolResult(tool_call_id="tc1", content="result")],
        ),
        llm.Message(role="assistant", content="done"),
    ]
    result = _truncate_orphaned_tool_calls(msgs)
    assert len(result) == 4


def test_load_session_empty_dir():
    """不存在的目录 → 返回空列表。"""
    msgs = load_session("/nonexistent/path/12345")
    assert msgs == []


# ── list_sessions 测试 ────────────────────────────


def test_list_sessions():
    """创建 3 个 session 目录 → 列表返回 3 项，按时间倒序。"""
    d = _make_session_dir()
    sessions_dir = os.path.join(d, "sessions")

    # 创建 3 个会话
    ids = ["20260601-120000-a1b2", "20260602-120000-c3d4", "20260603-120000-e5f6"]
    for sid in ids:
        sdir = os.path.join(sessions_dir, sid)
        os.makedirs(sdir, exist_ok=True)
        jsonl = os.path.join(sdir, "conversation.jsonl")
        with open(jsonl, "w", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "role": "user",
                        "content": f"title for {sid}",
                        "ts": 1717200000,
                        "model": "test-model",
                    }
                )
                + "\n"
            )
        # 确保不同 mtime
        time.sleep(0.05)

    result = list_sessions(sessions_dir)
    assert len(result) == 3
    # 按时间倒序：最新在前
    assert result[0].id == "20260603-120000-e5f6"
    assert result[2].id == "20260601-120000-a1b2"


def test_list_sessions_skips_old_format():
    """混合新旧格式目录 → 只返回新格式。"""
    d = _make_session_dir()
    sessions_dir = os.path.join(d, "sessions")

    os.makedirs(os.path.join(sessions_dir, "1717000000-abc12345"), exist_ok=True)
    Path(
        os.path.join(sessions_dir, "1717000000-abc12345", "conversation.jsonl")
    ).touch()

    new_dir = os.path.join(sessions_dir, "20260601-120000-a1b2")
    os.makedirs(new_dir, exist_ok=True)
    with open(os.path.join(new_dir, "conversation.jsonl"), "w", encoding="utf-8") as f:
        f.write(
            json.dumps({"role": "user", "content": "new format", "ts": 1, "model": "m"})
            + "\n"
        )

    result = list_sessions(sessions_dir)
    assert len(result) == 1
    assert result[0].id == "20260601-120000-a1b2"


def test_list_sessions_empty():
    """不存在的目录 → 返回空列表。"""
    result = list_sessions("/nonexistent/path")
    assert result == []


# ── clean_expired 测试 ────────────────────────────


def test_clean_expired():
    """创建一个 31 天前和一个 1 天前的目录 → 只删 31 天前的。"""
    d = _make_session_dir()
    sessions_dir = os.path.join(d, "sessions")

    # 31 天前
    old_time = datetime.now() - timedelta(days=31)
    old_id = old_time.strftime("%Y%m%d-%H%M%S") + "-dead"
    old_dir = os.path.join(sessions_dir, old_id)
    os.makedirs(old_dir)
    Path(os.path.join(old_dir, "conversation.jsonl")).touch()

    # 1 天前
    new_time = datetime.now() - timedelta(days=1)
    new_id = new_time.strftime("%Y%m%d-%H%M%S") + "-alive"
    new_dir = os.path.join(sessions_dir, new_id)
    os.makedirs(new_dir)
    Path(os.path.join(new_dir, "conversation.jsonl")).touch()

    # 旧格式目录
    old_fmt_dir = os.path.join(sessions_dir, "1717000000-abc12345")
    os.makedirs(old_fmt_dir)
    Path(os.path.join(old_fmt_dir, "conversation.jsonl")).touch()

    clean_expired(sessions_dir, timedelta(days=30))

    assert not os.path.exists(old_dir)  # 过期，已删除
    assert os.path.exists(new_dir)  # 未过期，保留
    assert os.path.exists(old_fmt_dir)  # 旧格式，保留
