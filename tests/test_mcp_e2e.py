"""ch07 端到端场景测试（可自动化部分）。

Scene 3: 失败隔离 — bad server 不影响 good server
Scene 5: 凭据展开 — ${VAR} 端到端
Scene 6: 退出干净 — 子进程终止
"""

from __future__ import annotations

import json
import subprocess

import pytest
import yaml

from suisuicode.mcp.config import Config, ServerConfig, load_config
from suisuicode.mcp.manager import new_manager


def _context7_available() -> bool:
    cfg = load_config()
    return "context7" in cfg.servers


# ── Scene 3: 失败隔离 ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(not _context7_available(), reason="context7 not configured")
async def test_scene3_failure_isolation(capsys):
    """配置一个不存在的 server + context7 → 坏的不影响好的。"""
    cfg = Config(
        servers={
            "bad-server": ServerConfig(
                type="stdio", command="/definitely/does/not/exist"
            ),
            "context7": load_config().servers["context7"],
        }
    )
    mgr = await new_manager(cfg, "0.1.0")
    try:
        tools = mgr.tools()
        # 只有 context7 的工具
        tool_names = {t.name() for t in tools}
        assert "mcp__context7__query-docs" in tool_names
        assert "mcp__context7__resolve-library-id" in tool_names
        # bad-server 的工具不应该出现
        assert not any("bad-server" in n for n in tool_names)

        # stderr 应该有 bad-server 失败告警
        err = capsys.readouterr().err
        assert "bad-server" in err

        # 调用 context7 工具正常
        tool = next(t for t in tools if t.name() == "mcp__context7__resolve-library-id")
        r = await tool.execute(
            json.dumps({"query": "react hooks", "libraryName": "React"})
        )
        assert r.is_error is False
    finally:
        await mgr.close()


# ── Scene 5: 凭据展开 ──────────────────────────────────


@pytest.mark.asyncio
async def test_scene5_credential_expansion(tmp_path, monkeypatch, capsys):
    """env 值中的 ${VAR} 展开 + 未定义变量告警。"""
    monkeypatch.setenv("MY_SECRET", "super-secret-123")
    monkeypatch.setenv("HOME", str(tmp_path))

    # 写一个含 ${VAR} 的项目级配置
    proj = tmp_path / ".suisuicode.yaml"
    proj.write_text(
        yaml.dump(
            {
                "mcp_servers": {
                    "test-srv": {
                        "type": "stdio",
                        "command": "echo",
                        "env": {
                            "TOKEN": "${MY_SECRET}",
                            "MISSING": "${UNDEFINED_VAR}",
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    cfg = load_config(root=str(tmp_path))
    srv = cfg.servers["test-srv"]

    # 已定义 → 展开
    assert srv.env["TOKEN"] == "super-secret-123"
    # 未定义 → 空串
    assert srv.env["MISSING"] == ""

    # stderr 有未定义变量告警
    err = capsys.readouterr().err
    assert "UNDEFINED_VAR" in err


# ── Scene 6: 退出干净（子进程终止）─────────────────────


@pytest.mark.asyncio
@pytest.mark.skipif(not _context7_available(), reason="context7 not configured")
async def test_scene6_clean_exit():
    """连接 context7 → 记录子进程 → close → 确认进程已终止。"""
    cfg = load_config()
    assert "context7" in cfg.servers

    # 记录连接前的 npx/node 进程
    before = _get_npx_pids()

    mgr = await new_manager(cfg, "0.1.0")
    try:
        tools = mgr.tools()
        assert len(tools) == 2

        # 确认子进程已启动
        after_connect = _get_npx_pids()
        new_pids = after_connect - before
        assert len(new_pids) > 0, "context7 子进程应已启动"
    finally:
        await mgr.close()

    # 给 OS 一点时间回收进程
    import asyncio

    await asyncio.sleep(0.5)

    after_close = _get_npx_pids()
    leaked = after_close - before
    # 关闭后，新启动的进程应该已终止
    assert len(leaked) == 0, (
        f"子进程泄漏: {leaked}\nbefore: {before}\nafter_close: {after_close}"
    )


def _get_npx_pids() -> set[int]:
    """获取当前 npx/node 相关进程 PID 集合。"""
    pids: set[int] = set()
    try:
        result = subprocess.run(
            ["tasklist", "/fi", "imagename eq node.exe", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            parts = line.replace('"', "").split(",")
            if len(parts) >= 2:
                try:
                    pids.add(int(parts[1].strip()))
                except ValueError:
                    pass
    except Exception:
        pass
    return pids
