"""HTTP MCP 传输测试：headers 注入、握手、列工具、工具调用。"""

from __future__ import annotations

from typing import Any

import mcp.types as mtypes
import pytest

from suisuicode.mcp.config import Config, ServerConfig
from suisuicode.mcp.manager import new_manager


# ── Mock 组件 ──────────────────────────────────────────


class _MockTransportCtx:
    """模拟 streamablehttp_client 返回的异步上下文管理器。"""

    def __init__(self, read: Any, write: Any) -> None:
        self._read = read
        self._write = write

    async def __aenter__(self) -> tuple[Any, Any]:
        return (self._read, self._write)

    async def __aexit__(self, *args: Any) -> None:
        pass


class _MockSession:
    """模拟 ClientSession，返回预设工具。"""

    def __init__(self, tools: list[mtypes.Tool]) -> None:
        self._tools = tools
        self.initialize_called = False
        self.list_tools_called = False

    async def initialize(self) -> None:
        self.initialize_called = True

    async def list_tools(self) -> mtypes.ListToolsResult:
        self.list_tools_called = True
        return mtypes.ListToolsResult(tools=self._tools)

    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mtypes.CallToolResult:
        return mtypes.CallToolResult(
            content=[mtypes.TextContent(type="text", text=f"result from {name}")],
        )


class _MockSessionCtx:
    """模拟 ClientSession 的异步上下文管理器。"""

    def __init__(self, session: _MockSession) -> None:
        self._session = session

    async def __aenter__(self) -> _MockSession:
        return self._session

    async def __aexit__(self, *args: Any) -> None:
        pass


# ── 测试 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_http_headers_injected(monkeypatch):
    """验证 streamablehttp_client 收到正确的 url 和 headers。"""
    captured_url: str | None = None
    captured_headers: dict[str, str] | None = None

    def _fake_streamablehttp_client(url: str, headers=None, **kwargs):
        nonlocal captured_url, captured_headers
        captured_url = url
        captured_headers = headers or {}
        return _MockTransportCtx("read", "write")

    monkeypatch.setattr(
        "suisuicode.mcp.manager.streamablehttp_client",
        _fake_streamablehttp_client,
    )

    # 构造一个返回到处工具的 mock ClientSession
    mock_tools = [
        mtypes.Tool(
            name="search", description="搜索文档", inputSchema={"type": "object"}
        ),
    ]
    mock_sess = _MockSession(mock_tools)

    def _fake_client_session(*args, **kwargs):
        return _MockSessionCtx(mock_sess)

    monkeypatch.setattr(
        "suisuicode.mcp.manager.ClientSession",
        _fake_client_session,
    )

    cfg = Config(
        servers={
            "api": ServerConfig(
                type="http",
                url="https://mcp.example.com/mcp",
                headers={"Authorization": "Bearer test-token"},
            ),
        }
    )
    mgr = await new_manager(cfg, "0.1.0")
    try:
        # 验证 URL 和 headers
        assert captured_url == "https://mcp.example.com/mcp"
        assert captured_headers == {"Authorization": "Bearer test-token"}

        # 验证握手和列工具
        assert mock_sess.initialize_called is True
        assert mock_sess.list_tools_called is True

        # 验证工具注册
        tools = mgr.tools()
        assert len(tools) == 1
        assert tools[0].name() == "mcp__api__search"
        assert tools[0].description() == "搜索文档"
    finally:
        await mgr.close()


@pytest.mark.asyncio
async def test_http_with_empty_headers(monkeypatch):
    """HTTP server 不带 headers 时，streamablehttp_client 收到 None。"""
    captured_headers: Any = "NOT_SET"

    def _fake_streamablehttp_client(url: str, headers=None, **kwargs):
        nonlocal captured_headers
        captured_headers = headers
        return _MockTransportCtx("read", "write")

    monkeypatch.setattr(
        "suisuicode.mcp.manager.streamablehttp_client",
        _fake_streamablehttp_client,
    )

    mock_sess = _MockSession([])

    def _fake_client_session(*args, **kwargs):
        return _MockSessionCtx(mock_sess)

    monkeypatch.setattr(
        "suisuicode.mcp.manager.ClientSession",
        _fake_client_session,
    )

    cfg = Config(
        servers={
            "srv": ServerConfig(type="http", url="https://example.com/mcp"),
        }
    )
    mgr = await new_manager(cfg, "0.1.0")
    try:
        # headers 应为 None（ServerConfig 默认 headers={} → or None → None）
        assert captured_headers is None
    finally:
        await mgr.close()


@pytest.mark.asyncio
async def test_http_tool_call_roundtrip(monkeypatch):
    """HTTP MCP 工具完整来回：连接 → 注册 → 调用 → 结果。"""

    def _fake_streamablehttp_client(url: str, headers=None, **kwargs):
        return _MockTransportCtx("read", "write")

    monkeypatch.setattr(
        "suisuicode.mcp.manager.streamablehttp_client",
        _fake_streamablehttp_client,
    )

    mock_sess = _MockSession(
        [
            mtypes.Tool(
                name="echo",
                description="回显",
                inputSchema={"type": "object"},
            ),
        ]
    )

    def _fake_client_session(*args, **kwargs):
        return _MockSessionCtx(mock_sess)

    monkeypatch.setattr(
        "suisuicode.mcp.manager.ClientSession",
        _fake_client_session,
    )

    cfg = Config(
        servers={"srv": ServerConfig(type="http", url="https://example.com/mcp")}
    )
    mgr = await new_manager(cfg, "0.1.0")
    try:
        tool = mgr.tools()[0]
        r = await tool.execute('{"msg": "hello"}')
        assert r.is_error is False
        assert "result from echo" in r.content
    finally:
        await mgr.close()


@pytest.mark.asyncio
async def test_http_and_stdio_mixed(monkeypatch):
    """HTTP + stdio 混合配置：两种类型各自正确构造传输。"""
    http_called = False
    stdio_called = False

    def _fake_http(url: str, headers=None, **kwargs):
        nonlocal http_called
        http_called = True
        return _MockTransportCtx("read-http", "write-http")

    monkeypatch.setattr(
        "suisuicode.mcp.manager.streamablehttp_client",
        _fake_http,
    )

    def _fake_stdio(server, errlog=None):
        nonlocal stdio_called
        stdio_called = True
        return _MockTransportCtx("read-stdio", "write-stdio")

    monkeypatch.setattr(
        "suisuicode.mcp.manager.stdio_client",
        _fake_stdio,
    )

    mock_http = _MockSession(
        [mtypes.Tool(name="web", description="http tool", inputSchema={})]
    )
    mock_stdio = _MockSession(
        [mtypes.Tool(name="local", description="stdio tool", inputSchema={})]
    )

    def _fake_session(*args, **kwargs):
        read = args[0]
        if read == "read-http":
            return _MockSessionCtx(mock_http)
        return _MockSessionCtx(mock_stdio)

    monkeypatch.setattr(
        "suisuicode.mcp.manager.ClientSession",
        _fake_session,
    )

    cfg = Config(
        servers={
            "web": ServerConfig(type="http", url="https://x.com"),
            "local": ServerConfig(type="stdio", command="node"),
        }
    )
    mgr = await new_manager(cfg, "0.1.0")
    try:
        assert http_called is True
        assert stdio_called is True
        tools = mgr.tools()
        assert len(tools) == 2
        assert {t.name() for t in tools} == {"mcp__web__web", "mcp__local__local"}
    finally:
        await mgr.close()
