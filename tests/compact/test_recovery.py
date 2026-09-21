"""T10-T11 + recovery 单元测试。"""

from datetime import datetime, timezone

from suisuicode.compact.recovery import (
    BOUNDARY_NOTICE,
    build_recovery_attachment,
    render_file_block,
    render_tools_block,
)
from suisuicode.compact.state import FileReadRecord
from suisuicode.llm import ToolDefinition


def test_render_file_block_truncate():
    rec = FileReadRecord(
        path="/test.txt",
        content="A" * 20000,  # 超 5000 * 3.5 = 17500 字符
        timestamp=datetime.now(timezone.utc),
    )
    block = render_file_block(rec)
    assert "(content truncated)" in block
    assert "/test.txt" in block
    # 头部内容保留（前 17500 字符）
    assert block.index("A") < block.index("(content truncated)")


def test_render_tools_block():
    defs = [
        ToolDefinition(name="read", description="读文件", input_schema={"type": "object"}),
        ToolDefinition(name="write", description="写文件", input_schema={"type": "object"}),
    ]
    block = render_tools_block(defs)
    assert "read" in block
    assert "write" in block


def test_build_recovery_attachment_limit():
    now = datetime.now(timezone.utc)
    records = [
        FileReadRecord(path=f"/f{i}.txt", content="c", timestamp=now)
        for i in range(7)
    ]
    defs = [ToolDefinition(name="t1", description="d", input_schema={})]
    output = build_recovery_attachment(records, defs)
    # 只展示前 5 个
    assert "/f0.txt" in output or "/f1.txt" in output
    assert "/f6.txt" not in output


def test_build_recovery_attachment_tools_exact():
    defs = [
        ToolDefinition(name="tool_a", description="desc a", input_schema={"type": "object"}),
        ToolDefinition(name="tool_b", description="desc b", input_schema={"type": "object"}),
    ]
    output = build_recovery_attachment([], defs)
    assert "tool_a" in output
    assert "tool_b" in output


def test_boundary_notice_stable():
    a = build_recovery_attachment([], [])
    b = build_recovery_attachment([], [])
    assert a == b
    assert BOUNDARY_NOTICE in a
