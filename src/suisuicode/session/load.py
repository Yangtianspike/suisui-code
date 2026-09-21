"""会话加载恢复：从 JSONL 还原消息列表，坏行跳过，孤立截断。"""

from __future__ import annotations

import json
import os

from suisuicode import llm


def load_session(session_dir: str) -> list[llm.Message]:
    """从 conversation.jsonl 恢复消息列表。

    从最后一个 compact 标记之后开始加载，
    跳过 JSON 解析失败的坏行，
    截断孤立的带 tool_calls 的 assistant 消息。
    """
    jsonl_path = os.path.join(session_dir, "conversation.jsonl")

    if not os.path.exists(jsonl_path):
        return []

    # 逐行读取，记录最后一个 compact 标记行号
    lines: list[dict] = []
    last_compact_idx = -1

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # 坏行容错：插入占位 None 保持行号对应
                    lines.append(None)  # type: ignore[arg-type]
                    continue

                if obj.get("type") == "compact":
                    last_compact_idx = len(lines)
                lines.append(obj)
    except OSError:
        return []

    # 从最后一个 compact 标记之后开始（不包括 compact 行本身）
    start_idx = last_compact_idx + 1 if last_compact_idx >= 0 else 0
    raw = lines[start_idx:]

    # 过滤坏行
    raw = [r for r in raw if r is not None]

    # 转换为 Message 列表
    msgs = [_dict_to_message(r) for r in raw]

    # 截断孤立工具调用
    msgs = _truncate_orphaned_tool_calls(msgs)

    return msgs


def _dict_to_message(obj: dict) -> llm.Message:
    """将 JSONL 行 dict 转为 llm.Message。"""
    role = obj.get("role", "")
    content = obj.get("content", "")
    tool_calls = []
    tool_results = []

    if "tool_calls" in obj and obj["tool_calls"]:
        tool_calls = [
            llm.ToolCall(id=tc["id"], name=tc["name"], input=tc.get("input", "{}"))
            for tc in obj["tool_calls"]
        ]

    if "tool_results" in obj and obj["tool_results"]:
        tool_results = [
            llm.ToolResult(
                tool_call_id=tr["tool_call_id"],
                content=tr.get("content", ""),
                is_error=tr.get("is_error", False),
            )
            for tr in obj["tool_results"]
        ]

    return llm.Message(
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_results=tool_results,
    )


def _truncate_orphaned_tool_calls(msgs: list[llm.Message]) -> list[llm.Message]:
    """如果最后一条是带 tool_calls 的 assistant 且无后续 tool 消息，截断掉。

    独立函数便于单独测试。
    """
    if not msgs:
        return msgs

    # 从尾部扫描：如果最后一条是 assistant 且有 tool_calls，
    # 检查其后面（尾部方向）是否有 tool 消息
    last = msgs[-1]
    if last.role == "assistant" and last.tool_calls:
        # 检查最后一条之后是否有 tool 消息 —— 因为它是最后一条所以没有
        return msgs[:-1]

    # 还要检查：如果倒数第二条是带 tool_calls 的 assistant，
    # 但倒数第一条不是 tool 消息，也需要截断
    if len(msgs) >= 2:
        second_last = msgs[-2]
        if second_last.role == "assistant" and second_last.tool_calls:
            if last.role != "tool":
                return msgs[:-2]

    return msgs
