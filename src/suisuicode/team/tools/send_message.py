"""SendMessage 工具（F31-F34）。"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from suisuicode.team.mailbox import Box, Message
from suisuicode.team.mailbox.message import MessageType
from suisuicode.tool import Result, Tool

if TYPE_CHECKING:
    from suisuicode.team.manager import Manager


class SendMessageTool(Tool):
    """队员间发送消息。"""

    def __init__(self, mgr: Manager) -> None:
        self._mgr = mgr

    def name(self) -> str:
        return "TeamSendMessage"

    def description(self) -> str:
        return (
            "向团队中其他队员发送消息。to 可以是队员名/agent_id/'*'(广播)。"
            "type 可选: text / shutdown_request / shutdown_response / plan_approval_response。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "收件人：队员名 / agent_id / '*' 广播",
                },
                "summary": {
                    "type": "string",
                    "description": "消息摘要（5-10 词，纯文本时必填）",
                },
                "message": {
                    "type": "string",
                    "description": "消息正文（可选）",
                },
                "type": {
                    "type": "string",
                    "description": "消息类型：text / shutdown_request / shutdown_response / plan_approval_response",
                },
                "payload": {
                    "type": "object",
                    "description": "结构化消息载荷（如 plan_approval_response 的 approve/feedback）",
                },
            },
            "required": ["to", "summary"],
        }

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        try:
            data = json.loads(args) if isinstance(args, str) else args
        except (json.JSONDecodeError, TypeError):
            return Result(is_error=True, content="invalid args")
        if not isinstance(data, dict):
            return Result(is_error=True, content="invalid args")

        to = str(data.get("to", ""))
        summary = str(data.get("summary", ""))
        content = str(data.get("message", ""))
        raw_type = str(data.get("type", "text"))
        payload = data.get("payload")

        if not to:
            return Result(is_error=True, content="to is required")

        try:
            msg_type = MessageType.TEXT
            try:
                msg_type = MessageType(raw_type)
            except ValueError:
                pass

            # 找调用者所属 Team
            # 简化：遍历所有 Team 找第一个有 mailbox_dir 的
            teams = self._mgr.list_()
            if not teams:
                return Result(is_error=True, content="没有活跃的 Team")

            team = teams[0]  # 默认取第一个 Team
            if not team.mailbox_dir:
                return Result(is_error=True, content="Team 无邮箱目录")

            box = Box(team.mailbox_dir)

            # 解析收件人
            if to == "*":
                # 广播：发给所有非 lead 成员
                target_ids = [
                    m.agent_id
                    for m in team.members
                    if m.name != "lead" and m.agent_id
                ]
            else:
                # 通过 registry 解析
                agent_id = to
                if self._mgr.registry is not None:
                    resolved = self._mgr.registry.resolve(to)
                    if resolved:
                        agent_id = resolved
                target_ids = [agent_id]

            now = int(time.time())

            if not target_ids:
                return Result(content=json.dumps({
                    "delivered_to": [],
                    "timestamp": now,
                    "note": "no non-lead members to broadcast to",
                }))
            delivered: list[str] = []
            for aid in target_ids:
                msg = Message(
                    from_="lead",  # 简化
                    to=aid,
                    type=msg_type,
                    summary=summary,
                    content=content,
                    payload=payload,
                    timestamp=now,
                )
                await box.write(aid, msg)
                delivered.append(aid)

            return Result(content=json.dumps({
                "delivered_to": delivered,
                "timestamp": now,
            }))
        except Exception as e:
            return Result(is_error=True, content=f"SendMessage 失败: {e}")
