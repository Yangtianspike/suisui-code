"""摘要 Prompt 模板、对话序列化与输出解析。"""

from __future__ import annotations

import logging
import re

from suisuicode.llm import Message

logger = logging.getLogger(__name__)

SUMMARY_INSTRUCTION: str = """\
You are summarizing a coding agent conversation. Output in two phases.

<analysis>
（在这里写分析草稿，会被丢弃）
</analysis>

<summary>
## 1 主要请求和意图
## 2 关键技术概念
## 3 文件和代码段
## 4 错误和修复
## 5 问题解决过程
## 6 所有用户消息原文
## 7 待办任务
## 8 当前工作（最详细）
## 9 可能的下一步
</summary>

不要调用任何工具，输出纯文本。"""


def serialize_conversation(msgs: list[Message]) -> str:
    """把对话扁平化成可读文本。

    - user/assistant: "role: <content>"
    - assistant 工具调用: "[call <name> id=<id> args=<json>]"
    - tool 消息每条 result: "[result id=<id> is_error=<bool>] <content>"
    """
    lines: list[str] = []
    for m in msgs:
        if m.role in ("user", "assistant"):
            lines.append(f"{m.role}: {m.content}")
            for tc in m.tool_calls:
                lines.append(f"[call {tc.name} id={tc.id} args={tc.input}]")
        elif m.role == "tool":
            for tr in m.tool_results:
                lines.append(
                    f"[result id={tr.tool_call_id} is_error={tr.is_error}] {tr.content}"
                )
    return "\n".join(lines)


def build_summary_prompt(msgs: list[Message]) -> list[Message]:
    """构造摘要请求消息：一条 user 消息，内嵌模板 + 序列化对话。"""
    serialized = serialize_conversation(msgs)
    content = SUMMARY_INSTRUCTION + "\n\n[conversation]\n" + serialized
    return [Message(role="user", content=content)]


def extract_summary(raw: str) -> str:
    """从模型返回文本中提取 <summary>...</summary> 之间的正文。

    找不到标签时返回原文并写一条 warning，避免硬失败。
    """
    matches = re.findall(r"<summary>(.*?)</summary>", raw, re.DOTALL)
    if matches:
        return matches[-1].strip()
    logger.warning("summary tags not found in response")
    return raw
