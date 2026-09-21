"""T3 测试：工具适配——命名拼接 / 禁用字符 / Execute 各分支。"""

from __future__ import annotations

import asyncio
from typing import Any

import mcp.types as mtypes
import pytest

from suisuicode.mcp.tool import (
    McpTool,
    adapt_tool,
    _VALID_NAME,
)


# ── Stub CallerSession ─────────────────────────────────────


class StubSession:
    """可控制的 stub，用于测试 McpTool.execute 各分支。"""

    def __init__(
        self,
        *,
        result: mtypes.CallToolResult | None = None,
        exception: Exception | None = None,
        block_event: asyncio.Event | None = None,
    ) -> None:
        self._result = result
        self._exception = exception
        self._block = block_event

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mtypes.CallToolResult:
        if self._block:
            await self._block.wait()
        if self._exception:
            raise self._exception
        if self._result:
            return self._result
        return mtypes.CallToolResult(content=[])


def _make_text_result(*texts: str, is_error: bool = False) -> mtypes.CallToolResult:
    return mtypes.CallToolResult(
        content=[mtypes.TextContent(type="text", text=t) for t in texts],
        isError=is_error,
    )


def _make_mixed_result(
    texts: list[str], extra_count: int = 1, is_error: bool = False
) -> mtypes.CallToolResult:
    blocks: list[Any] = [mtypes.TextContent(type="text", text=t) for t in texts]
    for _ in range(extra_count):
        blocks.append(
            mtypes.ImageContent(type="image", data="AAAA", mimeType="image/png")
        )
    return mtypes.CallToolResult(content=blocks, isError=is_error)


# ── adapt_tool 测试 ────────────────────────────────────────


def test_adapt_valid_name():
    """合法 server 名 + 工具名 → 返回 McpTool。"""
    t = mtypes.Tool(name="echo", description="回显工具", inputSchema={"type": "object"})
    stub = StubSession()
    tool = adapt_tool("demo", t, stub)
    assert tool is not None
    assert tool.name() == "mcp__demo__echo"
    assert tool.description() == "回显工具"
    assert tool.parameters() == {"type": "object"}
    assert tool.read_only is False


def test_adapt_empty_description():
    """description 为空 → 兜底文案。"""
    t = mtypes.Tool(name="foo", description="", inputSchema={})
    stub = StubSession()
    tool = adapt_tool("srv", t, stub)
    assert tool is not None
    assert "来自 MCP server srv 的工具 foo" in tool.description()


def test_adapt_empty_schema():
    """inputSchema 为空 dict → {"type": "object"} 兜底。"""
    t = mtypes.Tool(name="foo", description="d", inputSchema={})
    stub = StubSession()
    tool = adapt_tool("srv", t, stub)
    assert tool is not None
    assert tool.parameters() == {"type": "object"}


def test_adapt_read_only_true():
    """annotations.readOnlyHint==True → read_only is True。"""
    ann = mtypes.ToolAnnotations(readOnlyHint=True)
    t = mtypes.Tool(name="ro", description="r", inputSchema={}, annotations=ann)
    stub = StubSession()
    tool = adapt_tool("srv", t, stub)
    assert tool is not None
    assert tool.read_only is True


def test_adapt_annotations_none():
    """annotations is None → read_only is False，不报错。"""
    t = mtypes.Tool(name="x", description="d", inputSchema={}, annotations=None)
    stub = StubSession()
    tool = adapt_tool("srv", t, stub)
    assert tool is not None
    assert tool.read_only is False


def test_adapt_illegal_chars_dot(capsys):
    """server 名含 '.' → adapt_tool 返回 None + stderr 告警。"""
    t = mtypes.Tool(name="echo", description="d", inputSchema={})
    stub = StubSession()
    tool = adapt_tool("bad.name", t, stub)
    assert tool is None
    assert "illegal characters" in capsys.readouterr().err


def test_adapt_illegal_chars_at(capsys):
    """工具名含 '@' → 返回 None + 告警。"""
    t = mtypes.Tool(name="bad@tool", description="d", inputSchema={})
    stub = StubSession()
    tool = adapt_tool("srv", t, stub)
    assert tool is None
    assert "illegal characters" in capsys.readouterr().err


# ── _VALID_NAME ────────────────────────────────────────────


def test_valid_name_re():
    assert _VALID_NAME.fullmatch("mcp__srv__tool") is not None
    assert _VALID_NAME.fullmatch("mcp__github__create_issue") is not None
    assert _VALID_NAME.fullmatch("mcp__srv.tool") is None
    assert _VALID_NAME.fullmatch("mcp__srv__tool@v1") is None


# ── execute 测试 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_success_single_text():
    """成功调用，单 text 块。"""
    stub = StubSession(result=_make_text_result("hello world"))
    tool = McpTool("mcp__s__t", "t", "desc", {}, False, stub)
    r = await tool.execute('{"key": "val"}')
    assert r.is_error is False
    assert r.content == "hello world"


@pytest.mark.asyncio
async def test_execute_success_multi_text():
    """多 text 块按顺序拼接。"""
    stub = StubSession(result=_make_text_result("line1", "line2", "line3"))
    tool = McpTool("mcp__s__t", "t", "desc", {}, False, stub)
    r = await tool.execute("{}")
    assert r.is_error is False
    assert r.content == "line1\nline2\nline3"


@pytest.mark.asyncio
async def test_execute_remote_is_error():
    """远端 isError=True 映射。"""
    stub = StubSession(result=_make_text_result("something went wrong", is_error=True))
    tool = McpTool("mcp__s__t", "t", "desc", {}, False, stub)
    r = await tool.execute("{}")
    assert r.is_error is True
    assert "something went wrong" in r.content


@pytest.mark.asyncio
async def test_execute_call_exception():
    """call_tool 抛异常 → is_error=True + 失败文案。"""
    stub = StubSession(exception=RuntimeError("connection lost"))
    tool = McpTool("mcp__s__t", "t", "desc", {}, False, stub)
    r = await tool.execute("{}")
    assert r.is_error is True
    assert "MCP 工具调用失败" in r.content
    assert "connection lost" in r.content


@pytest.mark.asyncio
async def test_execute_timeout(monkeypatch):
    """超时 → is_error=True + 超时文案。"""
    # 让 call_tool 永久阻塞，超时设短
    stub = StubSession(block_event=asyncio.Event())
    tool = McpTool("mcp__s__t", "t", "desc", {}, False, stub)
    # 修改模块超时为 0.2s
    import suisuicode.mcp.tool as mcp_tool

    monkeypatch.setattr(mcp_tool, "_CALL_TIMEOUT", 0.2)
    r = await tool.execute("{}")
    assert r.is_error is True
    assert "超时" in r.content


@pytest.mark.asyncio
async def test_execute_non_text_blocks(capsys, monkeypatch):
    """非 text 块跳过 + 单 tool 限一次告警。"""
    stub = StubSession(result=_make_mixed_result(["text only"], extra_count=2))
    tool = McpTool("mcp__s__t", "t", "desc", {}, False, stub)

    # 清除全局状态
    monkeypatch.setattr("suisuicode.mcp.tool._non_text_warn_once", set())

    r1 = await tool.execute("{}")
    captured1 = capsys.readouterr()
    assert r1.content == "text only"
    assert "non-text content blocks" in captured1.err

    # 第二次调用：不再告警
    r2 = await tool.execute("{}")
    captured2 = capsys.readouterr()
    assert r2.content == "text only"
    assert "non-text content blocks" not in captured2.err


@pytest.mark.asyncio
async def test_execute_empty_args():
    """空参数 → arg_map 为 None。"""
    stub = StubSession(result=_make_text_result("ok"))
    tool = McpTool("mcp__s__t", "t", "desc", {}, False, stub)
    r = await tool.execute("")
    assert r.is_error is False
    assert r.content == "ok"


@pytest.mark.asyncio
async def test_execute_bad_json():
    """非法 JSON → 解析失败 is_error。"""
    stub = StubSession()
    tool = McpTool("mcp__s__t", "t", "desc", {}, False, stub)
    r = await tool.execute("not json")
    assert r.is_error is True
    assert "JSON 解析失败" in r.content
