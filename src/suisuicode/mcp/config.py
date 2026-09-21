"""两层 YAML 配置加载、合并、${VAR} 展开与字段校验。"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml


# ── 对外类型 ──────────────────────────────────────────────


@dataclass
class ServerConfig:
    """单个 MCP server 的完整定义（已展开 ${VAR}、已校验）。"""

    type: Literal["stdio", "http"]
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Config:
    """mcp_servers 在内存中的归一化形式（已合并）。"""

    servers: dict[str, ServerConfig] = field(default_factory=dict)


# ── 内部类型 ──────────────────────────────────────────────


@dataclass
class _RawServer:
    """从 YAML 直读的原始字段（全可选，未校验）。"""

    type: str | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    headers: dict[str, str] | None = None


# ── 正则 ──────────────────────────────────────────────────

_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


# ── 内部函数 ──────────────────────────────────────────────


def _load_file(path: Path) -> dict[str, _RawServer]:
    """加载单个配置文件；文件不存在或格式非法返回空 dict，不抛异常。"""
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        print(f"[mcp] warn: load {path} failed: {e}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        print(
            f"[mcp] warn: load {path} failed: top-level must be a dict", file=sys.stderr
        )
        return {}
    servers_raw = data.get("mcp_servers")
    if servers_raw is None or not isinstance(servers_raw, dict):
        return {}
    result: dict[str, _RawServer] = {}
    for name, raw in servers_raw.items():
        if not isinstance(raw, dict):
            print(
                f"[mcp] warn: skip server {name}: value is not a dict", file=sys.stderr
            )
            continue
        result[name] = _RawServer(
            type=raw.get("type"),
            command=raw.get("command"),
            args=raw.get("args"),
            env=raw.get("env"),
            url=raw.get("url"),
            headers=raw.get("headers"),
        )
    return result


def _expand_vars(s: str) -> tuple[str, list[str]]:
    """展开字符串中的 ${VAR}；返回 (展开后字符串, [未定义变量名列表])。"""
    undefined: list[str] = []

    def _replacer(m: re.Match[str]) -> str:
        var = m.group(1)
        if var in os.environ:
            return os.environ[var]
        undefined.append(var)
        return ""

    # mypy 对 re.sub 的 replacer callback 接受 Match[str] 返回 str
    return _VAR_RE.sub(_replacer, s), undefined


def _apply_expansion(name: str, srv: _RawServer) -> None:
    """对 env / headers 的值展开 ${VAR}（原地修改）。"""
    seen_undefined: set[str] = set()

    def _warn_undefined(vars_list: list[str]) -> None:
        for v in vars_list:
            if v not in seen_undefined:
                seen_undefined.add(v)
                print(
                    f"[mcp] warn: undefined env var ${{{v}}} referenced by server {name}",
                    file=sys.stderr,
                )

    if srv.env:
        for k, v in list(srv.env.items()):
            expanded, undef = _expand_vars(v)
            srv.env[k] = expanded
            _warn_undefined(undef)

    if srv.headers:
        for k, v in list(srv.headers.items()):
            expanded, undef = _expand_vars(v)
            srv.headers[k] = expanded
            _warn_undefined(undef)


def _merge_servers(
    user: dict[str, _RawServer], project: dict[str, _RawServer]
) -> dict[str, _RawServer]:
    """按 server 名合并：项目级同名整对象覆盖用户级。"""
    merged: dict[str, _RawServer] = dict(user)
    for name, srv in project.items():
        merged[name] = srv
    return merged


def _validate_server(name: str, srv: _RawServer) -> ServerConfig | None:
    """校验单条 server 定义；不合法返回 None 并 stderr 告警。"""
    t = srv.type
    if t not in ("stdio", "http"):
        reason = (
            f"type must be 'stdio' or 'http', got {t!r}"
            if t is not None
            else "missing type field"
        )
        print(f"[mcp] warn: skip server {name}: {reason}", file=sys.stderr)
        return None

    if t == "stdio":
        if not srv.command:
            print(
                f"[mcp] warn: skip server {name}: stdio requires command",
                file=sys.stderr,
            )
            return None
        return ServerConfig(
            type="stdio",
            command=srv.command or "",
            args=srv.args or [],
            env=srv.env or {},
        )

    # http
    if not srv.url:
        print(f"[mcp] warn: skip server {name}: http requires url", file=sys.stderr)
        return None
    return ServerConfig(
        type="http",
        url=srv.url or "",
        headers=srv.headers or {},
    )


# ── 公开入口 ──────────────────────────────────────────────


def load_config(root: str | None = None) -> Config:
    """加载并合并两层配置，返回归一化 Config（永不抛出）。"""
    root_path = Path(root) if root else Path.cwd()

    # 用户级
    user_servers: dict[str, _RawServer] = {}
    try:
        user_path = Path.home() / ".suisuicode" / "config.yaml"
    except Exception:
        user_path = None
    if user_path is not None:
        user_servers = _load_file(user_path)
        for name, srv in user_servers.items():
            _apply_expansion(name, srv)

    # 项目级
    project_path = root_path / ".suisuicode.yaml"
    project_servers = _load_file(project_path)
    for name, srv in project_servers.items():
        _apply_expansion(name, srv)

    # 合并
    merged = _merge_servers(user_servers, project_servers)

    # 校验
    validated: dict[str, ServerConfig] = {}
    for name, srv in merged.items():
        v = _validate_server(name, srv)
        if v is not None:
            validated[name] = v

    return Config(servers=validated)
