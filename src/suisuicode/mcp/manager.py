"""MCP server 连接管理器——顺序启动、生命周期管理、优雅关闭。"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass

import mcp.types as mtypes
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from suisuicode.mcp.config import Config, ServerConfig
from suisuicode.mcp.tool import McpTool, adapt_tool


# ── 模块级变量（非常量：单测可 monkeypatch 改小）─────────────

connect_timeout: float = 30.0
close_timeout: float = 5.0


# ── 内部类型 ──────────────────────────────────────────────


@dataclass
class _Session:
    name: str
    session: ClientSession


# ── Manager ────────────────────────────────────────────────


class Manager:
    """持有所有 MCP 会话与工具；外部通过 cli 装配。"""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: list[_Session] = []
        self._tools: list[McpTool] = []
        self._stack = AsyncExitStack()

    def tools(self) -> list[McpTool]:
        """返回适配好的工具列表副本。"""
        return list(self._tools)

    async def close(self) -> None:
        """关闭所有会话；总超时 close_timeout 秒兜底，绝不阻塞退出。"""
        try:
            async with asyncio.timeout(close_timeout):
                await self._stack.aclose()
        except TimeoutError:
            print(
                f"[mcp] warn: close timeout ({close_timeout}s), some sessions may leak",
                file=sys.stderr,
            )


# ── new_manager ────────────────────────────────────────────


async def new_manager(cfg: Config, version: str) -> Manager:
    """顺序连接所有 server；每个 server 30s 超时；失败仅跳过 + 告警。

    使用 asyncio.timeout() 而非 asyncio.wait_for，避免 context manager
    跨 task 清理导致的 RuntimeError。
    """
    mgr = Manager()
    await mgr._stack.__aenter__()

    for name, srv in cfg.servers.items():
        await _connect_one(mgr, name, srv, version)

    # 稳定排序
    mgr._tools.sort(key=lambda t: t._full_name)
    return mgr


# ── 内部连接函数 ──────────────────────────────────────────


async def _connect_one(
    mgr: Manager, name: str, srv: ServerConfig, version: str
) -> None:
    """连接单个 server；异常 / 超时均捕获并告警，不向外传播。"""
    try:
        async with asyncio.timeout(connect_timeout):
            await _do_connect(mgr, name, srv, version)
    except TimeoutError:
        print(
            f"[mcp] warn: connect server {name} timeout after {connect_timeout}s",
            file=sys.stderr,
        )
    except Exception as e:
        print(f"[mcp] warn: connect server {name} failed: {e}", file=sys.stderr)


async def _do_connect(mgr: Manager, name: str, srv: ServerConfig, version: str) -> None:
    """建立传输 → 进入 ClientSession → 握手 → 列工具 → 适配。"""
    # 构造传输上下文
    if srv.type == "stdio":
        params = StdioServerParameters(
            command=srv.command,
            args=srv.args,
            env={**os.environ, **srv.env} if srv.env else None,
        )
        transport_ctx = stdio_client(params)
    else:  # http
        transport_ctx = streamablehttp_client(
            srv.url,
            headers=srv.headers or None,
            timeout=connect_timeout,
        )

    # 进入传输上下文
    transport = await mgr._stack.enter_async_context(transport_ctx)
    read_stream = transport[0]
    write_stream = transport[1]

    # 进入 ClientSession 上下文
    session = await mgr._stack.enter_async_context(
        ClientSession(
            read_stream,
            write_stream,
            client_info=mtypes.Implementation(name="suisuicode", version=version),
        )
    )

    # 握手
    await session.initialize()

    # 列出工具
    listed = await session.list_tools()

    # 适配工具
    adapted: list[McpTool] = []
    for t in listed.tools:
        at = adapt_tool(name, t, session)
        if at is not None:
            adapted.append(at)

    # 原子写入共享状态
    async with mgr._lock:
        mgr._sessions.append(_Session(name=name, session=session))
        mgr._tools.extend(adapted)
