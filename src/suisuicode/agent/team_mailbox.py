"""队员 Loop 头部 inbox 注入 — 每轮 LLM 调用前读取未读消息。

依赖注入：通过 TeammateContext 携带的 read_unread / mark_read 闭包访问邮箱，
避免 agent 包直接依赖 mailbox 包。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from suisuicode.agent.agent import Agent
    from suisuicode.agent.runtime import SessionRuntime


def build_incoming_messages_reminder(
    messages: list,  # list[IncomingMessage]
) -> str:
    """构造 <incoming-messages> system reminder（F42）。

    每条消息截取前 200 字 content。
    """
    parts = [f"<incoming-messages>\n收到 {len(messages)} 条新消息:"]
    for i, m in enumerate(messages, 1):
        summary = getattr(m, "summary", "") or ""
        from_ = getattr(m, "from_", "") or "?"
        msg_type = getattr(m, "type", "text") or "text"
        ts = getattr(m, "timestamp", 0) or 0
        content = getattr(m, "content", "") or ""
        if len(content) > 200:
            content = content[:200] + "..."

        parts.append(
            f"[{i}] 来自 {from_} (type={msg_type}, ts={ts}): {summary}\n"
            f"    {content}"
        )
    parts.append("</incoming-messages>")
    return "\n".join(parts)


async def ingest_team_mailbox(
    agent: Agent,
    runtime: SessionRuntime,
    ctx: dict,
) -> str | None:
    """队员 Loop 头部的邮箱检查入口。

    若 ctx 中有 TeammateContext 且有 read_unread 闭包：
    1. 读未读消息
    2. 标记已读
    3. 构造 <incoming-messages> reminder 追加到 runtime.pending_reminders
    4. 若有 plan_approval_response(approve=True)，自动切权限模式

    返回构造的 reminder 文本，或 None（无消息/无上下文）。
    """
    from suisuicode.agent.team_hook import teammate_context_from_ctx

    tc = teammate_context_from_ctx(ctx)
    if tc is None:
        return None

    if tc.read_unread is None:
        return None

    # 读未读消息
    indices, messages = await tc.read_unread()

    if not messages:
        return None

    # 标记已读
    if tc.mark_read is not None:
        await tc.mark_read(indices)

    # 检查 plan_approval_response
    for m in messages:
        msg_type = getattr(m, "type", "text")
        if msg_type == "plan_approval_response":
            payload = getattr(m, "payload", None)
            if isinstance(payload, dict):
                if payload.get("approve", False):
                    # 切到 default 模式
                    from suisuicode.permission import Mode as PermissionMode

                    if hasattr(agent, "set_permission_mode"):
                        agent.set_permission_mode(PermissionMode.DEFAULT)
                    # 注：reminder 文本会反映这一切换
                else:
                    _feedback = payload.get("feedback", "计划未获批准")
                    # TODO: 把 feedback 当作用户消息注入

    # 构造 reminder
    reminder = build_incoming_messages_reminder(messages)

    if runtime is not None:
        runtime.pending_reminders.append(reminder)

    return reminder
