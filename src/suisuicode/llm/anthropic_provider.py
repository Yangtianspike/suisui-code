from __future__ import annotations

import json
import asyncio
from typing import TYPE_CHECKING, AsyncIterator

import anthropic

from suisuicode.llm import (
    Message,
    PromptTooLongError,
    Request,
    StreamEvent,
    ToolCall,
    ToolDefinition,
    Usage,
    ROLE_ASSISTANT,
    ROLE_TOOL,
)

if TYPE_CHECKING:
    from suisuicode.config import ProviderConfig


def _to_anthropic_tools(tools: list[ToolDefinition]) -> list[dict]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.input_schema,
        }
        for t in tools
    ]


def _to_anthropic_messages(msgs: list[Message]) -> list[dict]:
    api_msgs: list[dict] = []
    for m in msgs:
        if m.role == ROLE_ASSISTANT and m.tool_calls:
            content: list[dict] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                try:
                    parsed = json.loads(tc.input) if tc.input else {}
                except json.JSONDecodeError:
                    parsed = {}
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc.id,
                        "name": tc.name,
                        "input": parsed,
                    }
                )
            api_msgs.append({"role": ROLE_ASSISTANT, "content": content})
        elif m.role == ROLE_TOOL:
            tool_content = []
            for tr in m.tool_results:
                tool_content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tr.tool_call_id,
                        "content": tr.content,
                        "is_error": tr.is_error,
                    }
                )
            if tool_content:
                api_msgs.append({"role": "user", "content": tool_content})
            else:
                api_msgs.append({"role": "user", "content": ""})
        else:
            api_msgs.append({"role": m.role, "content": m.content})
    return api_msgs


def _has_tool_history(msgs: list[Message]) -> bool:
    """检查消息历史中是否存在工具调用或工具结果。"""
    for m in msgs:
        if m.tool_calls or m.tool_results:
            return True
    return False


def _append_reminder_anthropic(api_msgs: list[dict], reminder: str) -> None:
    """将 reminder 安全地织入 Anthropic 消息通道。

    末条为 user → 追加 text block 到其 content（避免连续 user）。
    末条为 assistant → 新起一条 user 消息兜底。
    """
    if not api_msgs:
        api_msgs.append(
            {"role": "user", "content": [{"type": "text", "text": reminder}]}
        )
        return

    last = api_msgs[-1]
    if last["role"] == "user":
        content = last["content"]
        text_block = {"type": "text", "text": reminder}
        if isinstance(content, str):
            # 字符串 content → 转为 list 后追加
            if content:
                last["content"] = [{"type": "text", "text": content}, text_block]
            else:
                last["content"] = [text_block]
        elif isinstance(content, list):
            # list content → 追加 text block
            content.append(text_block)
        else:
            last["content"] = [text_block]
    else:
        # 末条为 assistant → 新起一条 user 消息
        api_msgs.append(
            {"role": "user", "content": [{"type": "text", "text": reminder}]}
        )


class AnthropicProvider:
    def __init__(self, cfg: ProviderConfig) -> None:
        self._name = cfg.name
        self._model = cfg.model
        self._thinking = cfg.thinking
        self._client = anthropic.AsyncAnthropic(
            api_key=cfg.api_key,
            **({"base_url": cfg.base_url} if cfg.base_url else {}),
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        # ── 构造 system 段（两块：稳定 + 环境）────────
        system: list[dict] = []
        if req.system.stable:
            system.append(
                {
                    "type": "text",
                    "text": req.system.stable,
                    "cache_control": {"type": "ephemeral"},
                }
            )
        if req.system.environment:
            system.append({"type": "text", "text": req.system.environment})

        api_msgs = _to_anthropic_messages(req.messages)

        # ── 注入 reminder ────────────────────────────
        if req.reminder:
            _append_reminder_anthropic(api_msgs, req.reminder)

        params: dict = {
            "model": self._model,
            "max_tokens": 4096,
            "system": system,
            "messages": api_msgs,
        }

        if req.tools:
            params["tools"] = _to_anthropic_tools(req.tools)

        # 含工具历史的请求关闭 thinking（避免 400）
        if self._thinking and not _has_tool_history(req.messages):
            params["thinking"] = {"type": "enabled", "budget_tokens": 2048}
            params["max_tokens"] = 16000

        try:
            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield StreamEvent(text=delta.text)
                        # thinking_delta / input_json_delta: 跳过（SDK 已累加）

                # 流结束后取 final_message 收集 tool_use
                final_message = await stream.get_final_message()
                if final_message.stop_reason == "tool_use":
                    calls: list[ToolCall] = []
                    for block in final_message.content:
                        if block.type == "tool_use":
                            calls.append(
                                ToolCall(
                                    id=block.id,
                                    name=block.name,
                                    input=json.dumps(block.input),
                                )
                            )
                    if calls:
                        yield StreamEvent(tool_calls=calls)

                # 上抛本轮 token 用量（含缓存字段）
                if final_message.usage:
                    u = final_message.usage
                    yield StreamEvent(
                        usage=Usage(
                            input_tokens=u.input_tokens,
                            output_tokens=u.output_tokens,
                            cache_write=getattr(u, "cache_creation_input_tokens", 0)
                            or 0,
                            cache_read=getattr(u, "cache_read_input_tokens", 0) or 0,
                        )
                    )

            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except anthropic.BadRequestError as e:
            msg = str(getattr(e, "message", ""))
            body = str(getattr(e, "body", ""))
            if "prompt is too long" in msg or "prompt is too long" in body:
                wrapped = PromptTooLongError("anthropic prompt too long")
                wrapped.__cause__ = e
                yield StreamEvent(err=wrapped)
            else:
                yield StreamEvent(err=e)
        except Exception as e:
            yield StreamEvent(err=e)
