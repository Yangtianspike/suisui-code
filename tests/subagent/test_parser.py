"""subagent.parser 测试：frontmatter 解析、字段校验、错误处理。"""

import pytest

from suisuicode.subagent.definition import Source
from suisuicode.subagent.parser import (
    AgentParseError,
    parse_definition,
    parse_file,
    parse_frontmatter_and_body,
)


# ── parse_frontmatter_and_body ──────────────────────────


def test_parse_basic_frontmatter():
    raw = "---\nname: test\n---\n\nHello world"
    fm, body = parse_frontmatter_and_body(raw)
    assert fm == {"name": "test"}
    assert body == "Hello world"


def test_parse_frontmatter_missing_delimiter():
    raw = "no frontmatter here\njust text"
    with pytest.raises(AgentParseError, match="缺少 frontmatter 分隔符"):
        parse_frontmatter_and_body(raw)


def test_parse_frontmatter_content_before_dashes():
    raw = "garbage\n---\nname: test\n---\nbody"
    with pytest.raises(AgentParseError, match="不应有非空白内容"):
        parse_frontmatter_and_body(raw)


def test_parse_frontmatter_not_dict():
    raw = "---\n- list item\n---\nbody"
    with pytest.raises(AgentParseError, match="YAML mapping"):
        parse_frontmatter_and_body(raw)


# ── parse_definition ─────────────────────────────────────


def make_def(fm: str, body: str = "test body") -> bytes:
    return f"---\n{fm}\n---\n\n{body}".encode("utf-8")


def test_valid_minimal():
    data = make_def("name: my-agent\ndescription: A test agent")
    d = parse_definition(data, "test.md", Source.USER)
    assert d.name == "my-agent"
    assert d.description == "A test agent"
    assert d.system_prompt == "test body"
    assert d.source == Source.USER
    assert d.model == "inherit"
    assert d.max_turns == 0
    assert d.dont_ask is False


def test_all_fields():
    data = make_def(
        "name: full-agent\n"
        "description: Full featured agent\n"
        "tools:\n  - read_file\n  - grep\n"
        "disallowedTools:\n  - write_file\n"
        "model: haiku\n"
        "maxTurns: 10\n"
        "permissionMode: acceptEdits\n"
        "background: true\n"
        "isolation: worktree\n"
    )
    d = parse_definition(data, "full.md", Source.PROJECT)
    assert d.name == "full-agent"
    assert d.tools == ["read_file", "grep"]
    assert d.disallowed_tools == ["write_file"]
    assert d.model == "haiku"
    assert d.max_turns == 10
    assert d.permission_mode is not None
    assert d.background is True
    assert d.dont_ask is False


def test_permission_mode_dont_ask():
    data = make_def(
        "name: auto-agent\n"
        "description: Auto agent\n"
        "permissionMode: dontAsk\n"
    )
    d = parse_definition(data, "auto.md", Source.USER)
    assert d.dont_ask is True


def test_permission_mode_plan():
    data = make_def(
        "name: plan-agent\n"
        "description: Plan agent\n"
        "permissionMode: plan\n"
    )
    d = parse_definition(data, "plan.md", Source.USER)
    assert d.dont_ask is False


def test_missing_name():
    data = make_def("description: No name")
    with pytest.raises(AgentParseError, match="缺少必填字段: name"):
        parse_definition(data, "bad.md", Source.USER)


def test_missing_description():
    data = make_def("name: no-desc")
    with pytest.raises(AgentParseError, match="缺少必填字段: description"):
        parse_definition(data, "bad.md", Source.USER)


def test_invalid_model_fallback(capsys):
    data = make_def("name: bad-model\ndescription: test\nmodel: gpt-4")
    d = parse_definition(data, "bad.md", Source.USER)
    assert d.model == "inherit"
    captured = capsys.readouterr()
    assert "unknown model" in captured.err


def test_invalid_permission_mode_fallback(capsys):
    data = make_def(
        "name: bad-mode\ndescription: test\npermissionMode: weirdMode"
    )
    d = parse_definition(data, "bad.md", Source.USER)
    assert d.dont_ask is False
    captured = capsys.readouterr()
    assert "unknown permissionMode" in captured.err


def test_name_regex_valid():
    # 大小写开头都合法
    for name in ["general-purpose", "Explore", "Plan", "my_agent", "A"]:
        data = make_def(f"name: {name}\ndescription: test")
        d = parse_definition(data, "test.md", Source.USER)
        assert d.name == name


@pytest.mark.parametrize("bad_name", ["-bad", "_bad", "a" * 33])
def test_name_regex_invalid(bad_name):
    data = make_def(f"name: {bad_name}\ndescription: test")
    with pytest.raises(AgentParseError):
        parse_definition(data, "test.md", Source.USER)


def test_name_none_raises():
    """YAML 中 name: 后面无值 → null → 视为空。"""
    data = b"---\nname:\ndescription: test\n---\n\nbody"
    with pytest.raises(AgentParseError, match="缺少必填字段: name"):
        parse_definition(data, "test.md", Source.USER)


def test_bom_handling():
    data = b"\xef\xbb\xbf" + make_def("name: bom-test\ndescription: BOM test")
    d = parse_definition(data, "bom.md", Source.USER)
    assert d.name == "bom-test"


def test_empty_tools():
    data = make_def("name: simple\ndescription: test")
    d = parse_definition(data, "simple.md", Source.USER)
    assert d.tools == []
    assert d.disallowed_tools == []


# ── isolation ──────────────────────────────────────────


def test_isolation_worktree():
    """isolation: worktree 正确解析。"""
    data = make_def("name: wt-agent\ndescription: WT agent\nisolation: worktree\n")
    d = parse_definition(data, "wt.md", Source.PROJECT)
    assert d.isolation == "worktree"


def test_isolation_default_empty():
    """不写 isolation 时默认为空字符串。"""
    data = make_def("name: no-iso\ndescription: No isolation")
    d = parse_definition(data, "noiso.md", Source.USER)
    assert d.isolation == ""


def test_isolation_invalid_fallback(capsys):
    """非法 isolation 值 stderr 警告并回落到空字符串。"""
    data = make_def("name: bad-iso\ndescription: Bad isolation\nisolation: gibberish\n")
    d = parse_definition(data, "bad.md", Source.USER)
    assert d.isolation == ""
    captured = capsys.readouterr()
    assert "isolation" in captured.err.lower() or "gibberish" in captured.err


# ── parse_file ───────────────────────────────────────────


def test_parse_file(tmp_path):
    p = tmp_path / "test.md"
    p.write_text("---\nname: file-agent\ndescription: From file\n---\n\nFile body")
    d = parse_file(str(p), Source.PROJECT)
    assert d.name == "file-agent"
    assert d.system_prompt == "File body"
    assert d.source == Source.PROJECT
    assert d.file_path == str(p)


# ── Definition.is_fork ───────────────────────────────────


def test_is_fork():
    from suisuicode.subagent.definition import Definition

    d = Definition(name="__fork__", description="fork")
    assert d.is_fork() is True

    d2 = Definition(name="Explore", description="explore")
    assert d2.is_fork() is False
