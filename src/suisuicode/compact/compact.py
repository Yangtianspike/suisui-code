"""manage_context 主入口——编排第 1 层与第 2 层调用。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from suisuicode.compact.const import AUTO_SAFETY_MARGIN, SUMMARY_RESERVE

if TYPE_CHECKING:
    from suisuicode.compact.state import (
        CompactCircuitBreaker,
        ContentReplacementState,
        RecoveryState,
        SessionContext,
    )
    from suisuicode.conversation import Conversation
    from suisuicode.llm import Provider, ToolDefinition

logger = logging.getLogger(__name__)


class TriggerKind(Enum):
    AUTO = "auto"
    MANUAL = "manual"
    EMERGENCY = "emergency"


@dataclass
class ManageInput:
    conv: Conversation
    provider: Provider
    model: str
    context_window: int
    tool_defs: list[
        ToolDefinition
    ]  # 本轮迭代开头按 mode 选好的工具定义（与 stream 共用）
    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    usage_anchor: int  # 上一次主对话 stream 真实 usage 之和
    anchor_msg_len: int  # anchor 当时 conv.length()
    estimated_token: int  # 调用方算好的本轮估算 token
    trigger: TriggerKind


@dataclass
class ManageOutput:
    before_tokens: int
    after_tokens: int
    spilled_count: int = 0  # 本轮 layer1 新增落盘数


async def manage_context(in_: ManageInput) -> ManageOutput:
    """Agent 每轮请求前必调的唯一入口。

    MANUAL 分支：跳过 layer1、阈值、熔断；直接 force_compact。
    EMERGENCY 分支：先强制跑 layer1，再 force_compact。
    AUTO 分支：先 layer1 → 重估 token → 若超阈值且未熔断则 auto_compact。
    """
    from suisuicode.compact.layer1 import offload_and_snip
    from suisuicode.compact.layer2 import auto_compact, force_compact
    from suisuicode.compact.token import estimate_tokens

    total_spilled = 0

    # ── MANUAL ──────────────────────────────────────
    if in_.trigger == TriggerKind.MANUAL:
        new_msgs, before_tok, after_tok = await force_compact(in_)
        in_.conv.replace_history(new_msgs)
        logger.info("manual compact done before=%d after=%d", before_tok, after_tok)
        return ManageOutput(before_tokens=before_tok, after_tokens=after_tok)

    # ── EMERGENCY ───────────────────────────────────
    if in_.trigger == TriggerKind.EMERGENCY:
        # 先强制跑一次 layer1 把大工具结果挪走
        layer1_out, spilled = offload_and_snip(
            in_.conv.messages(), in_.replacement, in_.session
        )
        in_.conv.replace_history(layer1_out)
        total_spilled += spilled

        new_msgs, before_tok, after_tok = await force_compact(in_)
        in_.conv.replace_history(new_msgs)
        logger.info("emergency compact done before=%d after=%d", before_tok, after_tok)
        return ManageOutput(
            before_tokens=before_tok,
            after_tokens=after_tok,
            spilled_count=total_spilled,
        )

    # ── AUTO ────────────────────────────────────────
    # a. layer1
    layer1_out, spilled = offload_and_snip(
        in_.conv.messages(), in_.replacement, in_.session
    )
    in_.conv.replace_history(layer1_out)
    total_spilled += spilled

    # b. 用 layer1 之后的 updated_msgs 重新估算 token
    est_tokens = estimate_tokens(in_.usage_anchor, layer1_out, in_.anchor_msg_len)

    # c. sanity check：context_window 过小则跳过自动 layer2
    if in_.context_window <= SUMMARY_RESERVE + AUTO_SAFETY_MARGIN:
        logger.warning(
            "context_window=%d 过小（≤ %d），跳过自动 layer2",
            in_.context_window,
            SUMMARY_RESERVE + AUTO_SAFETY_MARGIN,
        )
        return ManageOutput(
            before_tokens=in_.estimated_token,
            after_tokens=est_tokens,
            spilled_count=total_spilled,
        )

    # d. 阈值判断
    threshold = in_.context_window - SUMMARY_RESERVE - AUTO_SAFETY_MARGIN
    if est_tokens < threshold or in_.auto_tracking.tripped():
        return ManageOutput(
            before_tokens=in_.estimated_token,
            after_tokens=est_tokens,
            spilled_count=total_spilled,
        )

    # e. auto_compact
    new_msgs, before_tok, after_tok = await auto_compact(in_)
    in_.conv.replace_history(new_msgs)
    logger.info("auto compact done before=%d after=%d", before_tok, after_tok)
    return ManageOutput(
        before_tokens=before_tok,
        after_tokens=after_tok,
        spilled_count=total_spilled,
    )
