"""subagent.catalog 测试：多来源加载、覆盖、错误处理。"""

import os
import pytest
from pathlib import Path

from suisuicode.subagent.catalog import Catalog, load_catalog, _load_from_dir
from suisuicode.subagent.definition import Definition, Source
from suisuicode.subagent.embed import builtin_definitions


# ── builtin_definitions ──────────────────────────────────


def test_builtin_returns_three():
    defs = builtin_definitions()
    names = {d.name for d in defs}
    assert "general-purpose" in names
    assert "Explore" in names
    assert "Plan" in names
    assert len(defs) == 3

    # 验证 Explore 的字段
    explore = next(d for d in defs if d.name == "Explore")
    assert explore.model == "haiku"
    assert "write_file" in explore.disallowed_tools
    assert explore.source == Source.BUILTIN
    assert len(explore.system_prompt) > 0


# ── Catalog basic operations ─────────────────────────────


def test_catalog_resolve():
    c = Catalog()
    d = Definition(name="test", description="A test", source=Source.USER)
    c._add_all([d], Source.USER)
    assert c.resolve("test") is d
    assert c.resolve("nonexistent") is None


def test_catalog_list_sorted():
    c = Catalog()
    c._add_all(
        [Definition(name="z", description="z"), Definition(name="a", description="a")],
        Source.USER,
    )
    names = [d.name for d in c.list()]
    assert names == ["a", "z"]


def test_catalog_list_by_source():
    c = Catalog()
    c._add_all([Definition(name="b", description="b")], Source.BUILTIN)
    c._add_all([Definition(name="p", description="p")], Source.PROJECT)
    assert len(c.list_by_source(Source.BUILTIN)) == 1
    assert len(c.list_by_source(Source.PROJECT)) == 1
    assert len(c.list_by_source(Source.USER)) == 0


def test_catalog_fork_definition():
    c = Catalog()
    f = c.fork_definition()
    assert f.is_fork()
    assert f.name == "__fork__"
    assert f.model == "inherit"
    assert f.max_turns == 25


# ── Priority override ────────────────────────────────────


def test_override_higher_source_wins():
    """项目级 > 用户级 > 内置级。"""
    c = Catalog()
    c._add_all(
        [Definition(name="X", description="builtin", source=Source.BUILTIN)],
        Source.BUILTIN,
    )
    c._add_all(
        [Definition(name="X", description="user", source=Source.USER)],
        Source.USER,
    )
    c._add_all(
        [Definition(name="X", description="project", source=Source.PROJECT)],
        Source.PROJECT,
    )
    d = c.resolve("X")
    assert d is not None
    assert d.description == "project"
    assert d.source == Source.PROJECT


def test_override_user_over_builtin():
    c = Catalog()
    c._add_all(
        [Definition(name="X", description="builtin", source=Source.BUILTIN)],
        Source.BUILTIN,
    )
    c._add_all(
        [Definition(name="X", description="user", source=Source.USER)],
        Source.USER,
    )
    d = c.resolve("X")
    assert d is not None
    assert d.description == "user"


# ── load_catalog integration ─────────────────────────────


def test_load_catalog_with_project_override(tmp_path, monkeypatch):
    """项目级 .suisuicode/agents/explore.md 覆盖内置 Explore。"""
    # 伪造 HOME（用户级）
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    # 项目级定义
    agents_dir = tmp_path / ".suisuicode" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "explore.md").write_text(
        "---\nname: Explore\ndescription: project-level explore\nmaxTurns: 10\n---\n\nProject explore body"
    )

    c = load_catalog(str(tmp_path))
    d = c.resolve("Explore")
    assert d is not None
    assert d.description == "project-level explore"
    assert d.source == Source.PROJECT
    assert d.max_turns == 10
    assert "Project explore body" in d.system_prompt


def test_load_catalog_user_level(tmp_path, monkeypatch):
    """用户级定义被加载 — 直接测 _load_from_dir 和 Catalog。"""
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()
    (agents_dir / "custom.md").write_text(
        "---\nname: custom\ndescription: user custom agent\n---\n\nCustom body"
    )

    from suisuicode.subagent.catalog import _load_from_dir
    defs = _load_from_dir(agents_dir, Source.USER)
    assert len(defs) == 1
    assert defs[0].name == "custom"
    assert defs[0].source == Source.USER
    assert defs[0].description == "user custom agent"


def test_load_catalog_skip_bad_file(tmp_path, monkeypatch, capsys):
    """非法 frontmatter 文件被跳过，其他正常文件仍加载。"""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))

    agents_dir = tmp_path / ".suisuicode" / "agents"
    agents_dir.mkdir(parents=True)
    # 非法文件：缺 description
    (agents_dir / "bad.md").write_text("---\nname: bad\n---\n\nbody")
    # 正常文件
    (agents_dir / "good.md").write_text(
        "---\nname: good\ndescription: a good one\n---\n\nbody"
    )

    c = load_catalog(str(tmp_path))
    assert c.resolve("bad") is None
    assert c.resolve("good") is not None
    captured = capsys.readouterr()
    assert "缺少必填字段" in captured.err


def test_load_catalog_missing_dir_ok(tmp_path, monkeypatch):
    """目录不存在时正常返回空（不报错）。"""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # 不创建 agents 目录
    c = load_catalog(str(tmp_path))
    # 至少内置的 3 个还在
    assert len(c.list()) >= 3


# ── _load_from_dir ───────────────────────────────────────


def test_load_from_dir_empty(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    defs = _load_from_dir(empty, Source.USER)
    assert defs == []


def test_load_from_dir_not_exists(tmp_path):
    defs = _load_from_dir(tmp_path / "nonexistent", Source.USER)
    assert defs == []
