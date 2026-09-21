"""三段恢复：最近读过的文件快照 + 当前可用工具 + 边界提示。"""

from __future__ import annotations

import io
import json

from suisuicode.compact.const import (
    ESTIMATE_CHARS_PER_TOKEN,
    RECOVERY_FILE_LIMIT,
    RECOVERY_TOKENS_PER_FILE,
)
from suisuicode.compact.state import FileReadRecord
from suisuicode.llm import ToolDefinition

BOUNDARY_NOTICE: str = (
    "需要文件原文、错误原文、用户原话时，请使用文件读取工具重新读取对应路径，"
    "不要依据摘要内容做猜测。"
)


def render_file_block(rec: FileReadRecord) -> str:
    """渲染单个文件快照：路径、时间戳、内容片段（必要时截断）。"""
    char_limit = int(RECOVERY_TOKENS_PER_FILE * ESTIMATE_CHARS_PER_TOKEN)
    content = rec.content
    truncated = False
    if len(content) > char_limit:
        content = content[:char_limit]
        truncated = True

    buf = io.StringIO()
    buf.write(f"### {rec.path}\n")
    buf.write(f"[read at] {rec.timestamp.isoformat()}\n")
    buf.write(content)
    if truncated:
        buf.write("\n(content truncated)")
    buf.write("\n")
    return buf.getvalue()


def render_tools_block(defs: list[ToolDefinition]) -> str:
    """渲染工具列表：每个工具一行名 + 描述 + 参数 schema 紧凑 JSON。"""
    if not defs:
        return "(无)\n"
    buf = io.StringIO()
    for d in defs:
        schema_json = json.dumps(
            d.input_schema, separators=(",", ":"), ensure_ascii=False
        )
        buf.write(f"- {d.name}: {d.description}\n")
        buf.write(f"  schema: {schema_json}\n")
    return buf.getvalue()


def build_recovery_attachment(
    snapshot: list[FileReadRecord],
    tool_defs: list[ToolDefinition],
) -> str:
    """构造摘要后的"恢复三段"内容（纯文本 str）。

    调用方必须在 run_summary 入口先拍好快照再传入，
    避免恢复段渲染期间另一个 task 通过 record_file 改变状态导致漂移。

    三段：
      1. 最近读过的文件快照（取前 RECOVERY_FILE_LIMIT 个，已按时间戳倒序）
      2. 当前可用工具列表（与 stream 调用同一列表引用）
      3. 边界提示固定文案
    """
    buf = io.StringIO()

    # 第一段：最近读过的文件
    buf.write("## 最近读过的文件\n")
    recent = snapshot[:RECOVERY_FILE_LIMIT]
    if recent:
        for rec in recent:
            buf.write(render_file_block(rec))
    else:
        buf.write("(无)\n")

    # 第二段：当前可用工具
    buf.write("\n## 当前可用工具\n")
    buf.write(render_tools_block(tool_defs))

    # 第三段：边界提示
    buf.write("\n## 边界提示\n")
    buf.write(BOUNDARY_NOTICE)
    buf.write("\n")

    return buf.getvalue()
