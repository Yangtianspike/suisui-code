"""第 2 层：LLM 摘要 + 恢复 + 近期原文 + PTL 重试 + 熔断。"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

from suisuicode.compact.const import (
    ESTIMATE_CHARS_PER_TOKEN,
    PTL_DROP_PERCENTAGE,
    PTL_RETRY_LIMIT,
    RECENT_KEEP_MESSAGES,
    RECENT_KEEP_TOKENS,
)
from suisuicode.compact.recovery import build_recovery_attachment
from suisuicode.compact.summary_prompt import build_summary_prompt, extract_summary
from suisuicode.compact.token import estimate_tokens, message_chars
from suisuicode.llm import ROLE_ASSISTANT, ROLE_TOOL, ROLE_USER, Message, Request

if TYPE_CHECKING:
    from suisuicode.compact.compact import ManageInput

logger = logging.getLogger(__name__)


# ── 近期原文边界 ───────────────────────────────────────


def pick_recent_tail(msgs: list[Message]) -> list[Message]:
    """从 msgs 尾部累加，直到累计 token ≥ RECENT_KEEP_TOKENS 且条数 ≥ RECENT_KEEP_MESSAGES。

    两个下界都满足后才停手（择宽语义）。
    再做配对修正：截断点不得夹在 tool_use 与 tool_result 之间。
    """
    if not msgs:
        return []

    n = len(msgs)
    accumulated_tokens = 0
    accumulated_count = 0
    start_idx = n

    for i in range(n - 1, -1, -1):
        accumulated_tokens += math.ceil(
            message_chars([msgs[i]]) / ESTIMATE_CHARS_PER_TOKEN
        )
        accumulated_count += 1
        start_idx = i
        if (
            accumulated_tokens >= RECENT_KEEP_TOKENS
            and accumulated_count >= RECENT_KEEP_MESSAGES
        ):
            break

    # 配对修正：若截断点正好是孤立的 tool 消息，向前推到 tool_use 之前
    while start_idx < n and msgs[start_idx].role == ROLE_TOOL:
        # 向前找最近的有 tool_calls 的 assistant 消息
        j = start_idx - 1
        while j >= 0:
            if msgs[j].role == ROLE_ASSISTANT and msgs[j].tool_calls:
                start_idx = j
                break
            j -= 1
        else:
            # 找不到配对 assistant，跳过这条 tool
            start_idx += 1
            break

    return list(msgs[start_idx:])


# ── role 衔接修正 ──────────────────────────────────────


def _join_after_summary(
    summary_and_recovery: Message, recent: list[Message]
) -> list[Message]:
    """拼接摘要消息与近期原文，保证不出现 user/user 连续。

    summary_and_recovery 固定 role="user"。
    """
    if not recent:
        return [summary_and_recovery]

    first = recent[0]

    if first.role == ROLE_USER:
        # 插入 assistant 衔接占位
        placeholder = Message(
            role=ROLE_ASSISTANT,
            content="（已加载上下文摘要与恢复信息。请继续。）",
        )
        return [summary_and_recovery, placeholder] + list(recent)

    if first.role == ROLE_TOOL:
        # 防御性处理：pair fix 应该已经解决，这里跳过孤立的 tool
        logger.warning("recent tail starts with orphan tool, searching back")
        for j in range(1, len(recent)):
            if recent[j].role != ROLE_TOOL:
                return [summary_and_recovery] + list(recent[j:])
        # 全是 tool？极少见，直接跳过
        return [summary_and_recovery]

    # assistant 开头：正常拼接
    return [summary_and_recovery] + list(recent)


# ── 消息分组（用于 PTL 重试） ──────────────────────────


def group_by_user_turn(msgs: list[Message]) -> list[list[Message]]:
    """按"用户提交 → 一组 assistant/tool 往返"分组。

    每遇到 role=="user" 就开新组。
    第一条不是 user 时（极少见）塞入第 0 组防止丢失。
    """
    if not msgs:
        return []

    groups: list[list[Message]] = []
    current: list[Message] = []

    for m in msgs:
        if m.role == ROLE_USER and current:
            groups.append(current)
            current = []
        current.append(m)

    if current:
        groups.append(current)

    return groups


# ── 单次摘要请求 ───────────────────────────────────────


async def summarize_once(in_: ManageInput, msgs: list[Message]) -> str:
    """发一次摘要请求（不带 tools），返回 <summary> 解析后的文本。

    流中 ev.err 非 None 时立即抛出（含 PromptTooLongError），
    由调用方 isinstance 判断走 ptl_retry。
    摘要请求结束后不更新 SessionRuntime.usage_anchor。
    """

    req = Request(
        messages=build_summary_prompt(msgs),
        tools=None,
    )
    text_buf: list[str] = []

    async for ev in in_.provider.stream(req):
        if ev.err is not None:
            # PTL 也走这条路径抛出
            raise ev.err

        if ev.text:
            text_buf.append(ev.text)

        # 忽略 ev.usage（摘要不更新主对话锚点）
        # 忽略 ev.tool_calls、ev.done

    return extract_summary("".join(text_buf))


# ── PTL 自重试 ─────────────────────────────────────────


async def ptl_retry(in_: ManageInput, msgs: list[Message], first_err: Exception) -> str:
    """摘要请求自身撞 PTL 时的丢消息组重试策略。

    前 PTL_RETRY_LIMIT 次：每次丢最旧 1 组。
    之后再失败：每次按 PTL_DROP_PERCENTAGE 丢弃（至少 1 组）。
    全部丢光仍失败则抛最后一次 err。
    中间任何非 PTL 异常立即上抛。
    """
    from suisuicode.llm import PromptTooLongError

    groups = group_by_user_turn(msgs)
    attempt = 0
    last_err = first_err

    while groups:
        if attempt < PTL_RETRY_LIMIT:
            # 丢最旧 1 组（即 groups[0]）
            groups = groups[1:]
        else:
            # 按比例丢
            drop = max(1, math.ceil(len(groups) * PTL_DROP_PERCENTAGE))
            groups = groups[drop:]

        attempt += 1

        if not groups:
            break

        flattened = [m for g in groups for m in g]
        try:
            return await summarize_once(in_, flattened)
        except PromptTooLongError as e:
            last_err = e
            continue
        # 非 PTL 异常立即上抛

    # 全部丢光：抛最后一次 err
    raise last_err


# ── 摘要 + 恢复 + 近期原文 ─────────────────────────────


async def run_summary(in_: ManageInput) -> list[Message]:
    """摘要核心：构造 prompt → 发请求 → 解析 → 拼接恢复段 → 追加近期原文。

    入口先拍一次 recovery_snapshot，整个摘要过程只用这份快照。
    """
    from suisuicode.llm import PromptTooLongError

    old_msgs = in_.conv.messages()
    recovery_snapshot = in_.recovery.snapshot()

    # 发摘要请求（含 PTL 自重试）
    try:
        summary_text = await summarize_once(in_, old_msgs)
    except PromptTooLongError as e:
        summary_text = await ptl_retry(in_, old_msgs, e)

    # 构造恢复三段
    recovery_text = build_recovery_attachment(recovery_snapshot, in_.tool_defs)

    # 合并摘要 + 恢复到同一条 user 消息
    combined_content = "## 历史会话摘要\n" + summary_text + "\n\n" + recovery_text
    summary_msg = Message(role=ROLE_USER, content=combined_content)

    # 近期原文
    recent_tail = pick_recent_tail(old_msgs)

    # 拼接（保证 role 交替）
    return _join_after_summary(summary_msg, recent_tail)


# ── auto / force compact ───────────────────────────────


async def auto_compact(
    in_: ManageInput,
) -> tuple[list[Message], int, int]:
    """自动路径压缩：含熔断更新。

    成功 → record_success()、返回 (new_msgs, before, after)。
    失败 → record_failure()、raise。
    """
    before_tok = in_.estimated_token

    try:
        new_msgs = await run_summary(in_)
    except Exception:
        in_.auto_tracking.record_failure()
        raise

    in_.auto_tracking.record_success()
    after_tok = estimate_tokens(0, new_msgs, 0)
    return new_msgs, before_tok, after_tok


async def force_compact(
    in_: ManageInput,
) -> tuple[list[Message], int, int]:
    """手动 / 紧急路径压缩：跳过熔断器，失败不计入熔断。"""
    before_tok = in_.estimated_token
    new_msgs = await run_summary(in_)
    after_tok = estimate_tokens(0, new_msgs, 0)
    return new_msgs, before_tok, after_tok
