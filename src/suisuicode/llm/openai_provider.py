from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, AsyncIterator

import openai

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


def _to_openai_tools(tools: list[ToolDefinition]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def _to_openai_messages(
    msgs: list[Message],
    system_text: str = "",
    reminder: str = "",
) -> list[dict]:
    """构造 OpenAI 消息序列。

    system_text 作为首条 system 消息（stable + environment 拼合），
    reminder 作为尾部 user 消息注入。
    """
    api_msgs: list[dict] = []
    if system_text:
        api_msgs.append({"role": "system", "content": system_text})

    for m in msgs:
        if m.role == ROLE_ASSISTANT and m.tool_calls:
            api_msgs.append(
                {
                    "role": ROLE_ASSISTANT,
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": tc.input or "{}",
                            },
                        }
                        for tc in m.tool_calls
                    ],
                }
            )
        elif m.role == ROLE_TOOL:
            for tr in m.tool_results:
                api_msgs.append(
                    {
                        "role": "tool",
                        "tool_call_id": tr.tool_call_id,
                        "content": tr.content,
                    }
                )
        else:
            api_msgs.append({"role": m.role, "content": m.content})

    if reminder:
        api_msgs.append({"role": "user", "content": reminder})

    return api_msgs


class OpenAIProvider:
    def __init__(self, cfg: ProviderConfig) -> None:
        self._name = cfg.name
        self._model = cfg.model
        self._thinking = cfg.thinking
        self._client = openai.AsyncOpenAI(
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
        # ── 拼合 system 文本 ──────────────────────────
        system_parts: list[str] = []
        if req.system.stable:
            system_parts.append(req.system.stable)
        if req.system.environment:
            system_parts.append(req.system.environment)
        system_text = "\n\n".join(system_parts)

        api_msgs = _to_openai_messages(
            req.messages,
            system_text=system_text,
            reminder=req.reminder,
        )

        params: dict = {
            "model": self._model,
            "messages": api_msgs,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if req.tools:
            params["tools"] = _to_openai_tools(req.tools)

        try:
            s = await self._client.chat.completions.create(**params)  # type: ignore[arg-type]

            tool_calls_buf: dict[int, dict[str, str]] = {}

            async for chunk in s:
                # 流末尾 usage 块：choices 为空但带 usage
                if not chunk.choices and chunk.usage:
                    u = chunk.usage
                    cache_read = (
                        getattr(
                            getattr(u, "prompt_tokens_details", None),
                            "cached_tokens",
                            0,
                        )
                        or 0
                    )
                    yield StreamEvent(
                        usage=Usage(
                            input_tokens=u.prompt_tokens or 0,
                            output_tokens=u.completion_tokens or 0,
                            cache_write=0,
                            cache_read=cache_read,
                        )
                    )
                    continue

                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                if delta.content:
                    yield StreamEvent(text=delta.content)

                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_buf:
                            tool_calls_buf[idx] = {"id": "", "name": "", "args": ""}
                        if tc.id:
                            tool_calls_buf[idx]["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                tool_calls_buf[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_buf[idx]["args"] += tc.function.arguments

                finish = chunk.choices[0].finish_reason
                if finish == "tool_calls":
                    if tool_calls_buf:
                        calls = [
                            ToolCall(
                                id=v["id"],
                                name=v["name"],
                                input=v["args"] or "{}",
                            )
                            for v in tool_calls_buf.values()
                        ]
                        yield StreamEvent(tool_calls=calls)

            yield StreamEvent(done=True)
        except asyncio.CancelledError:
            raise
        except openai.BadRequestError as e:
            code = getattr(getattr(e, "error", None), "code", "")
            if code == "context_length_exceeded":
                wrapped = PromptTooLongError("openai context_length_exceeded")
                wrapped.__cause__ = e
                yield StreamEvent(err=wrapped)
            else:
                yield StreamEvent(err=e)
        except Exception as e:
            yield StreamEvent(err=e)
