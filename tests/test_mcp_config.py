"""T2 测试：两层合并 / ${VAR} 展开 / 字段校验 / 降级。"""

from __future__ import annotations

from pathlib import Path

import yaml

from suisuicode.mcp.config import (
    _apply_expansion,
    _expand_vars,
    _load_file,
    _merge_servers,
    _RawServer,
    _validate_server,
    load_config,
)


# ── _expand_vars ──────────────────────────────────────────


def test_expand_defined_var(monkeypatch):
    monkeypatch.setenv("FOO", "bar")
    result, undef = _expand_vars("hello ${FOO} world")
    assert result == "hello bar world"
    assert undef == []


def test_expand_undefined_var():
    result, undef = _expand_vars("token=${MISSING}")
    assert result == "token="
    assert "MISSING" in undef


def test_expand_no_vars():
    result, undef = _expand_vars("plain text")
    assert result == "plain text"
    assert undef == []


def test_expand_multiple_vars(monkeypatch):
    monkeypatch.setenv("A", "1")
    result, undef = _expand_vars("${A}-${B}-${C}")
    assert result == "1--"
    assert sorted(undef) == ["B", "C"]


# ── _apply_expansion ──────────────────────────────────────


def test_apply_expansion_env(capsys):
    srv = _RawServer(env={"X": "v=${FOO}"}, headers={})
    _apply_expansion("test", srv)
    assert srv.env["X"] == "v="
    captured = capsys.readouterr()
    assert "undefined env var ${FOO}" in captured.err
    assert "server test" in captured.err


def test_apply_expansion_headers(monkeypatch):
    monkeypatch.setenv("TOKEN", "secret")
    srv = _RawServer(headers={"Authorization": "Bearer ${TOKEN}"}, env={})
    _apply_expansion("srv", srv)
    assert srv.headers["Authorization"] == "Bearer secret"


def test_apply_expansion_dedup_warning(capsys):
    srv = _RawServer(env={"A": "${X}", "B": "${X}"}, headers={"C": "${X}"})
    _apply_expansion("dedup", srv)
    captured = capsys.readouterr()
    # ${X} should only warn once per server
    assert captured.err.count("undefined env var ${X}") == 1


# ── _load_file ────────────────────────────────────────────


def test_load_missing_file():
    result = _load_file(Path("/nonexistent/path.yaml"))
    assert result == {}


def test_load_invalid_yaml(tmp_path, capsys):
    f = tmp_path / "bad.yaml"
    f.write_text("[: invalid yaml", encoding="utf-8")
    result = _load_file(f)
    assert result == {}
    assert "warn" in capsys.readouterr().err


def test_load_valid_servers(tmp_path):
    f = tmp_path / "valid.yaml"
    f.write_text(
        yaml.dump(
            {
                "mcp_servers": {
                    "gh": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@mcp/server-github"],
                        "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    result = _load_file(f)
    assert "gh" in result
    assert result["gh"].type == "stdio"
    assert result["gh"].command == "npx"


def test_load_no_mcp_servers_key(tmp_path):
    f = tmp_path / "no_mcp.yaml"
    f.write_text("other_key: value\n", encoding="utf-8")
    result = _load_file(f)
    assert result == {}


# ── _merge_servers ────────────────────────────────────────


def test_merge_project_overrides_user():
    user = {"srv": _RawServer(type="stdio", command="user-cmd")}
    project = {"srv": _RawServer(type="stdio", command="project-cmd")}
    merged = _merge_servers(user, project)
    assert merged["srv"].command == "project-cmd"


def test_merge_disjoint():
    user = {"a": _RawServer(type="stdio", command="cmd-a")}
    project = {"b": _RawServer(type="http", url="http://example.com")}
    merged = _merge_servers(user, project)
    assert len(merged) == 2
    assert merged["a"].command == "cmd-a"
    assert merged["b"].url == "http://example.com"


# ── _validate_server ──────────────────────────────────────


def test_validate_stdio_ok():
    cfg = _validate_server("s", _RawServer(type="stdio", command="npx"))
    assert cfg is not None
    assert cfg.type == "stdio"
    assert cfg.command == "npx"


def test_validate_stdio_missing_command(capsys):
    cfg = _validate_server("s", _RawServer(type="stdio"))
    assert cfg is None
    assert "requires command" in capsys.readouterr().err


def test_validate_http_ok():
    cfg = _validate_server("s", _RawServer(type="http", url="https://x.com"))
    assert cfg is not None
    assert cfg.type == "http"
    assert cfg.url == "https://x.com"


def test_validate_http_missing_url(capsys):
    cfg = _validate_server("s", _RawServer(type="http"))
    assert cfg is None
    assert "requires url" in capsys.readouterr().err


def test_validate_illegal_type(capsys):
    cfg = _validate_server("s", _RawServer(type="ws"))
    assert cfg is None
    assert "must be 'stdio' or 'http'" in capsys.readouterr().err


def test_validate_missing_type(capsys):
    cfg = _validate_server("s", _RawServer())
    assert cfg is None
    assert "missing type field" in capsys.readouterr().err


# ── load_config 集成 ─────────────────────────────────────


def test_load_config_no_files(tmp_path, monkeypatch):
    """两文件都不存在 → Config.servers 为空。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = load_config(root=str(tmp_path))
    assert cfg.servers == {}


def test_load_config_only_project(tmp_path, monkeypatch):
    """仅项目级配置存在。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    proj = tmp_path / ".suisuicode.yaml"
    proj.write_text(
        yaml.dump({"mcp_servers": {"srv": {"type": "stdio", "command": "proj-cmd"}}}),
        encoding="utf-8",
    )
    cfg = load_config(root=str(tmp_path))
    assert "srv" in cfg.servers
    assert cfg.servers["srv"].command == "proj-cmd"


def test_load_config_only_user(tmp_path, monkeypatch):
    """仅用户级配置存在。"""
    home = tmp_path / "home"
    home.mkdir()
    suisuicode_dir = home / ".suisuicode"
    suisuicode_dir.mkdir()
    (suisuicode_dir / "config.yaml").write_text(
        yaml.dump({"mcp_servers": {"u": {"type": "http", "url": "http://u.com"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    cfg = load_config(root=str(tmp_path))
    assert "u" in cfg.servers
    assert cfg.servers["u"].url == "http://u.com"


def test_load_config_merge_override(tmp_path, monkeypatch):
    """同名 server 项目级覆盖用户级——整对象覆盖。"""
    home = tmp_path / "home"
    home.mkdir()
    suisuicode_dir = home / ".suisuicode"
    suisuicode_dir.mkdir()
    (suisuicode_dir / "config.yaml").write_text(
        yaml.dump({"mcp_servers": {"srv": {"type": "stdio", "command": "user-cmd"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)

    (tmp_path / ".suisuicode.yaml").write_text(
        yaml.dump(
            {"mcp_servers": {"srv": {"type": "stdio", "command": "project-cmd"}}}
        ),
        encoding="utf-8",
    )
    cfg = load_config(root=str(tmp_path))
    assert cfg.servers["srv"].command == "project-cmd"


def test_load_config_invalid_file(tmp_path, monkeypatch, capsys):
    """项目级格式非法 → 跳过该层、其余正常。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    # 写一个合法的用户级
    home = tmp_path / "home"
    home.mkdir()
    suisuicode_dir = home / ".suisuicode"
    suisuicode_dir.mkdir()
    (suisuicode_dir / "config.yaml").write_text(
        yaml.dump({"mcp_servers": {"good": {"type": "http", "url": "http://g.com"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: home)

    # 写非法的项目级（不存在的锚点引用触发 YAML 解析错误）
    (tmp_path / ".suisuicode.yaml").write_text("*bad_alias_reference\n", encoding="utf-8")
    cfg = load_config(root=str(tmp_path))
    # 用户级 server 仍在
    assert "good" in cfg.servers
    assert "warn" in capsys.readouterr().err


def test_load_config_var_expansion(tmp_path, monkeypatch):
    """${VAR} 展开生效，未定义变量告警但不阻塞。"""
    monkeypatch.setenv("MY_TOKEN", "secret123")
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".suisuicode.yaml").write_text(
        yaml.dump(
            {
                "mcp_servers": {
                    "srv": {
                        "type": "http",
                        "url": "http://x.com",
                        "headers": {"Authorization": "Bearer ${MY_TOKEN}"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(root=str(tmp_path))
    assert cfg.servers["srv"].headers["Authorization"] == "Bearer secret123"


def test_load_config_skip_invalid_server(tmp_path, monkeypatch, capsys):
    """type 缺失 / 非法 / stdio 缺 command / http 缺 url 均跳过。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".suisuicode.yaml").write_text(
        yaml.dump(
            {
                "mcp_servers": {
                    "bad1": {"type": "stdio"},  # 缺 command
                    "bad2": {"type": "http"},  # 缺 url
                    "bad3": {"type": "ws"},  # 非法 type
                    "bad4": {},  # 缺 type
                    "good": {"type": "stdio", "command": "ok"},
                }
            }
        ),
        encoding="utf-8",
    )
    cfg = load_config(root=str(tmp_path))
    assert list(cfg.servers.keys()) == ["good"]
    err = capsys.readouterr().err
    assert "bad1" in err
    assert "bad2" in err
    assert "bad3" in err
    assert "bad4" in err
