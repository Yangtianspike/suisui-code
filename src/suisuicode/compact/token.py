"""Token 估算——锚定真实 usage + 字符增量，不依赖精确 tokenizer。"""

from __future__ import annotations

import json
import math

from suisuicode.compact.const import ESTIMATE_CHARS_PER_TOKEN
from suisuicode.llm import Message, Usage


def usage_anchor(u: Usage) -> int:
    """把 stream 尾事件中的 usage 合并成单一锚点值。

    等价于 input + output + cache_read + cache_write 之和。
    """
    return u.input_tokens + u.output_tokens + u.cache_read + u.cache_write


def message_chars(msgs: list[Message]) -> int:
    """计算消息列表的 UTF-8 字节总量。

    累加 content + tool_calls input + tool_results content。
    """
    total = 0
    for m in msgs:
        if m.content:
            total += len(m.content.encode("utf-8"))
        for tc in m.tool_calls:
            inp = tc.input
            if isinstance(inp, str):
                total += len(inp.encode("utf-8"))
            elif inp is not None:
                total += len(json.dumps(inp).encode("utf-8"))
        for tr in m.tool_results:
            if tr.content:
                total += len(tr.content.encode("utf-8"))
    return total


def estimate_tokens(
    anchor: int,
    all_msgs: list[Message],
    anchor_msg_len: int,
) -> int:
    """锚定最近 provider usage + 之后新增消息的字符增量估算。

    anchor 为上一次主对话 stream 真实 usage 之和（int）。
    anchor_msg_len 为 anchor 记录时 conv 的消息条数。
    函数只对 all_msgs[anchor_msg_len:] 做字符增量估算，避免重复计算历史。
    anchor=0、anchor_msg_len=0 时退化为纯字符估算。
    """
    start = max(0, anchor_msg_len)
    tail = all_msgs[start:]
    chars = message_chars(tail)
    return anchor + math.ceil(chars / ESTIMATE_CHARS_PER_TOKEN)
