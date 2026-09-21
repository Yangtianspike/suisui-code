"""context7 MCP server 端到端测试（需要 context7 已在 .suisuicode.yaml 中配置且可连接）。"""

from __future__ import annotations

import json

import pytest

from suisuicode.mcp import load_config, new_manager


def _context7_available() -> bool:
    """检查 context7 是否在配置中。"""
    cfg = load_config()
    return "context7" in cfg.servers


@pytest.mark.asyncio
@pytest.mark.skipif(not _context7_available(), reason="context7 not configured")
async def test_context7_connect_and_list_tools():
    """连接 context7 → 列工具 → 验证工具名和只读性。"""
    cfg = load_config()
    mgr = await new_manager(cfg, "0.1.0")
    try:
        tools = mgr.tools()
        tool_names = {t.name() for t in tools}
        assert "mcp__context7__query-docs" in tool_names
        assert "mcp__context7__resolve-library-id" in tool_names

        for t in tools:
            assert t.read_only is True, f"{t.name()} should be read_only"
            assert t.description(), f"{t.name()} should have non-empty description"
            params = t.parameters()
            assert "type" in params, f"{t.name()} params should have type"
    finally:
        await mgr.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not _context7_available(), reason="context7 not configured")
async def test_context7_resolve_library_id():
    """调用 resolve-library-id → 验证返回非错误结果。"""
    cfg = load_config()
    mgr = await new_manager(cfg, "0.1.0")
    try:
        tool = next(
            t for t in mgr.tools() if t.name() == "mcp__context7__resolve-library-id"
        )
        r = await tool.execute(
            json.dumps({"query": "find React documentation", "libraryName": "React"})
        )
        assert r.is_error is False, f"resolve-library-id failed: {r.content}"
        assert len(r.content) > 0, "should return non-empty result"
    finally:
        await mgr.close()


@pytest.mark.asyncio
@pytest.mark.skipif(not _context7_available(), reason="context7 not configured")
async def test_context7_query_docs():
    """调用 query-docs → 验证返回文档内容。"""
    cfg = load_config()
    mgr = await new_manager(cfg, "0.1.0")
    try:
        tool = next(t for t in mgr.tools() if t.name() == "mcp__context7__query-docs")
        r = await tool.execute(
            json.dumps(
                {
                    "libraryId": "/websites/react",
                    "query": "useState hook example",
                }
            )
        )
        assert r.is_error is False, f"query-docs failed: {r.content}"
        assert len(r.content) > 0, "should return documentation content"
    finally:
        await mgr.close()
