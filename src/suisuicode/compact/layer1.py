"""第 1 层预防性压缩：单条落盘 + 聚合落盘 + 决策冻结。"""

from __future__ import annotations

import copy
import io
import logging
from pathlib import Path

from suisuicode.compact.const import (
    MESSAGE_AGGREGATE_LIMIT,
    PREVIEW_HEAD_BYTES,
    PREVIEW_HEAD_LINES,
    SINGLE_RESULT_LIMIT,
)
from suisuicode.compact.state import ContentReplacementState, SessionContext
from suisuicode.llm import Message

logger = logging.getLogger(__name__)


def spill_single(session: SessionContext, tool_use_id: str, content: str) -> None:
    """把单条 tool_result 内容写入 spill_dir/<tool_use_id>。

    幂等：文件已存在则不重写、不报错。
    失败抛 OSError 由上层捕获。
    """
    path = Path(session.spill_dir) / tool_use_id
    if path.exists():
        return
    path.write_bytes(content.encode("utf-8"))


def _head_preview(content: str) -> str:
    """截取预览体头部：先按行截到 PREVIEW_HEAD_LINES 行，再按字节截到 PREVIEW_HEAD_BYTES。

    返回的字符串行数 ≤ PREVIEW_HEAD_LINES 且字节数 ≤ PREVIEW_HEAD_BYTES。
    """
    lines = content.splitlines(keepends=True)
    if len(lines) > PREVIEW_HEAD_LINES:
        lines = lines[:PREVIEW_HEAD_LINES]
    head = "".join(lines)
    head_bytes = head.encode("utf-8")
    if len(head_bytes) > PREVIEW_HEAD_BYTES:
        # 按字节截断，保证不割裂 UTF-8 码点
        truncated = head_bytes[:PREVIEW_HEAD_BYTES]
        head = truncated.decode("utf-8", errors="replace")
    return head


def build_preview(original_bytes: int, head: str, spill_path: str) -> str:
    """构造替换体字符串。

    包含：原始字节数元信息、头部预览、落盘路径、重读提示。
    固定格式确保逐字节稳定，满足 prompt cache 确定性要求。
    """
    buf = io.StringIO()
    buf.write(f"[content offloaded] original size: {original_bytes} bytes\n")
    buf.write(f"[saved to] {spill_path}\n")
    buf.write("[head preview]\n")
    buf.write(head)
    if not head.endswith("\n"):
        buf.write("\n")
    buf.write(
        "完整内容已保存到上述路径，如需查看请用文件读取工具读取该路径，"
        "不要凭头部预览猜测全文"
    )
    return buf.getvalue()


def offload_and_snip(
    msgs: list[Message],
    state: ContentReplacementState,
    session: SessionContext,
) -> tuple[list[Message], int]:
    """遍历 msgs 对每条 RoleTool 消息做单条 + 聚合落盘判定。

    返回 (新消息列表, 本轮新增落盘数)。
    """
    out = copy.deepcopy(msgs)
    spilled = 0

    for msg in out:
        if msg.role != "tool":
            continue
        results = msg.tool_results
        if not results:
            continue

        # 第一步：分离已决策与未决策项
        candidates: list[tuple[int, str, str]] = []  # (index, id, content)
        for j, tr in enumerate(results):
            tid = tr.tool_call_id
            # 通过 decide_once + skip 探测已决策项（skip 不写账本）
            new_content = state.decide_once(tid, tr.content, lambda: ("skip", ""))
            if tid in state._seen_ids:
                # 已决策：直接使用存量结果
                tr.content = new_content
            else:
                candidates.append((j, tid, tr.content))

        if not candidates:
            continue

        # 第二步：按字节倒序排序
        candidates.sort(key=lambda x: len(x[2].encode("utf-8")), reverse=True)

        # 第三步：计算聚合预算
        kept_total = sum(
            len(tr.content.encode("utf-8"))
            for j2, tr2 in enumerate(results)
            if j2 not in {c[0] for c in candidates}
        )
        budget_remaining = MESSAGE_AGGREGATE_LIMIT - kept_total

        # 第四步：逐项决策
        for idx, tid, content in candidates:
            content_bytes = len(content.encode("utf-8"))
            must_spill = content_bytes > SINGLE_RESULT_LIMIT
            over_budget = budget_remaining > 0 and content_bytes > budget_remaining

            if not must_spill and not over_budget:
                # 保留原文
                state.decide_once(tid, content, lambda: ("kept", ""))
                budget_remaining -= content_bytes
                # content 保持原文（decide_once 返回 original）
                continue

            # 需要落盘
            def _decide() -> tuple[str, str]:
                try:
                    spill_single(session, tid, content)
                except OSError:
                    logger.warning("落盘失败 tool_use_id=%s", tid, exc_info=True)
                    return ("skip", "")
                spill_path = str(Path(session.spill_dir) / tid)
                preview = build_preview(
                    content_bytes, _head_preview(content), spill_path
                )
                return ("replaced", preview)

            new_content = state.decide_once(tid, content, _decide)
            results[idx].content = new_content
            if "original size:" in new_content:
                spilled += 1

    return out, spilled
