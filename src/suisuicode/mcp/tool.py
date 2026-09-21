"""把 MCP 远端工具适配为 suisuicode Tool 协议。"""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Protocol

import mcp.types as mtypes

from suisuicode.tool import Result


# ── 常量 ──────────────────────────────────────────────────

_CALL_TIMEOUT: float = 30.0
_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_non_text_warn_once: set[str] = set()


# ── CallerSession Protocol ─────────────────────────────────


class CallerSession(Protocol):
    """MCP 会话的调用接口（生产用 ClientSession，单测用 stub）。"""

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mtypes.CallToolResult: ...


# ── McpTool ────────────────────────────────────────────────


@dataclass
class McpTool:
    """MCP 远端工具适配——实现 suisuicode Tool 协议。

    内部字段用下划线前缀存储；通过方法 / @property 暴露 Tool 协议接口。
    """

    _full_name: str  # "mcp__<server>__<tool>"
    _remote_name: str  # server 上的原始工具名
    _description: str
    _parameters: dict[str, Any]  # JSON Schema 透传
    _read_only: bool
    _caller: CallerSession

    # ── Tool 协议 ────────────────────────────────────────

    def name(self) -> str:
        return self._full_name

    def description(self) -> str:
        return self._description

    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return self._read_only

    async def execute(self, args: str) -> Result:
        """执行 MCP 工具调用。

        args: JSON 字符串（来自 ToolCall.input），解析为 dict 传给 MCP SDK。
        """
        # 解析 JSON 参数
        arg_map: dict[str, Any] | None = None
        if args and args.strip():
            try:
                parsed = json.loads(args)
                if isinstance(parsed, dict):
                    arg_map = parsed
                else:
                    arg_map = {}
            except json.JSONDecodeError:
                return Result(
                    content=f"MCP 工具参数 JSON 解析失败: {args[:200]}", is_error=True
                )
        else:
            arg_map = None

        # 调用远端
        try:
            result = await asyncio.wait_for(
                self._caller.call_tool(self._remote_name, arg_map),
                timeout=_CALL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return Result(content="MCP 工具调用超时 (30s)", is_error=True)
        except Exception as e:
            return Result(content=f"MCP 工具调用失败: {e}", is_error=True)

        # 收集文本内容块
        texts: list[str] = []
        has_non_text = False
        for block in result.content:
            if isinstance(block, mtypes.TextContent):
                texts.append(block.text)
            else:
                has_non_text = True

        # 非 text 块告警（per tool 限一次）
        if has_non_text and self._full_name not in _non_text_warn_once:
            _non_text_warn_once.add(self._full_name)
            print(
                f"[mcp] warn: tool {self._full_name} returned non-text content blocks (dropped)",
                file=sys.stderr,
            )

        content = "\n".join(texts)
        return Result(content=content, is_error=bool(result.isError))


# ── adapt_tool ─────────────────────────────────────────────


def adapt_tool(
    server_name: str, t: mtypes.Tool, session: CallerSession
) -> McpTool | None:
    """将 SDK 的 Tool 适配为 McpTool；返回 None 表示该工具应被跳过（已在 stderr 告警）。"""
    full_name = f"mcp__{server_name}__{t.name}"

    # 禁用字符校验
    if not _VALID_NAME.fullmatch(full_name):
        print(
            f"[mcp] warn: skip tool {full_name}: name contains illegal characters",
            file=sys.stderr,
        )
        return None

    # 描述（空则兜底）
    desc = t.description or f"来自 MCP server {server_name} 的工具 {t.name}"

    # 参数 schema（透传，空则兜底）
    raw_schema = t.inputSchema
    if raw_schema and isinstance(raw_schema, dict):
        params = dict(raw_schema)
    else:
        params = {"type": "object"}

    # 只读性（严格只信 readOnlyHint==True）
    ro = bool(
        getattr(t, "annotations", None) is not None
        and t.annotations is not None
        and getattr(t.annotations, "readOnlyHint", None) is True
    )

    return McpTool(
        _full_name=full_name,
        _remote_name=t.name,
        _description=desc,
        _parameters=params,
        _read_only=ro,
        _caller=session,
    )
