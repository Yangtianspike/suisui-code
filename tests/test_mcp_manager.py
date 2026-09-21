"""T4 测试：连接管理器——成功/失败/超时/close 不死锁/并发安全。"""

from __future__ import annotations

import asyncio
from typing import Any

import mcp.types as mtypes
import pytest

from suisuicode.mcp.config import Config, ServerConfig
from suisuicode.mcp.manager import Manager, new_manager
from suisuicode.mcp.tool import McpTool


# ── 辅助函数 ──────────────────────────────────────────────


def _make_cfg(**servers: ServerConfig) -> Config:
    return Config(servers=dict(servers))


# ── 空配置 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_config():
    """空 cfg → tools() 为空、close() 立即返回。"""
    mgr = await new_manager(Config(), "0.0.0")
    assert mgr.tools() == []
    await mgr.close()


# ── 成功连接（通过 monkeypatch _do_connect）───────────────


@pytest.mark.asyncio
async def test_successful_connect(monkeypatch):
    """注入 stub _do_connect → stub 工具被注册。"""

    async def stub_connect(mgr, name, srv, version):
        tool = McpTool(
            _full_name=f"mcp__{name}__echo",
            _remote_name="echo",
            _description="test tool",
            _parameters={},
            _read_only=False,
            _caller=_FakeSession(),
        )
        async with mgr._lock:
            from suisuicode.mcp.manager import _Session

            mgr._sessions.append(_Session(name=name, session=None))  # type: ignore[arg-type]
            mgr._tools.append(tool)

    monkeypatch.setattr("suisuicode.mcp.manager._do_connect", stub_connect)

    cfg = _make_cfg(
        srv1=ServerConfig(type="stdio", command="ok"),
        srv2=ServerConfig(type="http", url="http://ok.com"),
    )
    mgr = await new_manager(cfg, "0.0.0")
    tools = mgr.tools()
    assert len(tools) == 2
    assert tools[0].name() == "mcp__srv1__echo"
    assert tools[1].name() == "mcp__srv2__echo"
    await mgr.close()


# ── 失败隔离 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failure_isolation(monkeypatch, capsys):
    """一个 server 失败、一个成功 → 成功工具在，失败 stderr 有告警。"""
    call_count = 0

    async def stub_connect(mgr, name, srv, version):
        nonlocal call_count
        call_count += 1
        if name == "bad":
            raise RuntimeError("boom")
        # 成功
        tool = McpTool(
            _full_name=f"mcp__{name}__t",
            _remote_name="t",
            _description="ok",
            _parameters={},
            _read_only=False,
            _caller=_FakeSession(),
        )
        async with mgr._lock:
            from suisuicode.mcp.manager import _Session

            mgr._sessions.append(_Session(name=name, session=None))  # type: ignore[arg-type]
            mgr._tools.append(tool)

    monkeypatch.setattr("suisuicode.mcp.manager._do_connect", stub_connect)

    cfg = _make_cfg(
        bad=ServerConfig(type="stdio", command="nonexistent"),
        good=ServerConfig(type="http", url="http://ok.com"),
    )
    mgr = await new_manager(cfg, "0.0.0")
    tools = mgr.tools()
    assert len(tools) == 1
    assert tools[0].name() == "mcp__good__t"

    err = capsys.readouterr().err
    assert "bad" in err
    assert "boom" in err

    await mgr.close()


# ── 超时收尾 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_timeout(monkeypatch, capsys):
    """连接卡住 → 超时后跳过 + stderr 告警。"""
    monkeypatch.setattr("suisuicode.mcp.manager.connect_timeout", 0.2)

    async def stub_connect_blocking(mgr, name, srv, version):
        await asyncio.Event().wait()  # 永久阻塞

    monkeypatch.setattr("suisuicode.mcp.manager._do_connect", stub_connect_blocking)

    cfg = _make_cfg(stuck=ServerConfig(type="stdio", command="sleep"))
    mgr = await new_manager(cfg, "0.0.0")
    assert mgr.tools() == []

    err = capsys.readouterr().err
    assert "timeout" in err
    assert "stuck" in err

    await mgr.close()


# ── close 兜底 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_timeout_does_not_deadlock(monkeypatch, capsys):
    """注入阻塞 close 的 context → close() 在 close_timeout 内返回。"""
    monkeypatch.setattr("suisuicode.mcp.manager.close_timeout", 0.2)

    mgr = Manager()
    await mgr._stack.__aenter__()

    # 注入一个 __aexit__ 永久阻塞的 context manager
    class _BlockingExit:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await asyncio.Event().wait()

    await mgr._stack.enter_async_context(_BlockingExit())

    await mgr.close()

    err = capsys.readouterr().err
    assert "close timeout" in err


# ── 排序稳定性 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tools_sorted(monkeypatch):
    """工具按 full_name 稳定排序。"""

    async def stub_connect(mgr, name, srv, version):
        tool = McpTool(
            _full_name=f"mcp__{name}__tool",
            _remote_name="tool",
            _description="t",
            _parameters={},
            _read_only=False,
            _caller=_FakeSession(),
        )
        async with mgr._lock:
            from suisuicode.mcp.manager import _Session

            mgr._sessions.append(_Session(name=name, session=None))  # type: ignore[arg-type]
            mgr._tools.append(tool)

    monkeypatch.setattr("suisuicode.mcp.manager._do_connect", stub_connect)

    cfg = _make_cfg(
        z_srv=ServerConfig(type="stdio", command="z"),
        a_srv=ServerConfig(type="http", url="http://a.com"),
    )
    mgr = await new_manager(cfg, "0.0.0")
    tools = mgr.tools()
    assert tools[0].name() == "mcp__a_srv__tool"
    assert tools[1].name() == "mcp__z_srv__tool"
    await mgr.close()


# ── Stub ───────────────────────────────────────────────────


class _FakeSession:
    async def call_tool(
        self, name: str, arguments: dict[str, Any] | None = None
    ) -> mtypes.CallToolResult:
        return mtypes.CallToolResult(content=[])
