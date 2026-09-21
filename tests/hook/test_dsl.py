"""DSL 表达式解析器 + $VAR 变量替换 单元测试。"""

from __future__ import annotations

import pytest

from suisuicode.hook.dsl import parse_if_expression, substitute_variables


# ── parse_if_expression ───────────────────────────────


class TestParseIfExpression:
    def test_single_exact(self):
        """'tool == "WriteFile"' → all_of: tool_name exact WriteFile"""
        cond, err = parse_if_expression('tool == "WriteFile"')
        assert err is None
        assert cond is not None
        assert len(cond.atoms) == 1
        assert cond.atoms[0].field == "tool_name"
        assert cond.atoms[0].matcher.match("WriteFile")
        assert not cond.atoms[0].matcher.match("ReadFile")

    def test_single_glob(self):
        """'args.path ~= "*.py"' → tool_input.path glob *.py"""
        cond, err = parse_if_expression('args.path ~= "*.py"')
        assert err is None
        assert cond is not None
        assert cond.atoms[0].field == "tool_input.path"
        assert cond.atoms[0].matcher.match("main.py")
        assert not cond.atoms[0].matcher.match("README.md")

    def test_single_regex(self):
        """'args.command ~= /rm\\s+-rf/' → regex"""
        cond, err = parse_if_expression(r'args.command ~= /rm\s+-rf/')
        assert err is None
        assert cond is not None
        assert cond.atoms[0].field == "tool_input.command"

    def test_and_chain(self):
        """'a && b && c' → all_of with 3 atoms"""
        cond, err = parse_if_expression(
            'tool == "Bash" && args.command ~= /rm/ && is_error == "False"'
        )
        assert err is None
        assert cond is not None
        assert cond.mode.value == "all_of"
        assert len(cond.atoms) == 3

    def test_or_chain(self):
        """'a || b' → any_of with 2 atoms"""
        cond, err = parse_if_expression(
            'tool == "WriteFile" || tool == "EditFile"'
        )
        assert err is None
        assert cond is not None
        assert cond.mode.value == "any_of"
        assert len(cond.atoms) == 2

    def test_not_equal(self):
        """'tool != "Bash"' → not exact"""
        cond, err = parse_if_expression('tool != "Bash"')
        assert err is None
        assert cond is not None
        assert cond.atoms[0].matcher.match("ReadFile")
        assert not cond.atoms[0].matcher.match("Bash")

    def test_not_glob(self):
        """'args.path !!= "vendor/*"' → not glob"""
        cond, err = parse_if_expression('args.path !!= "vendor/*"')
        assert err is None
        assert cond is not None
        assert cond.atoms[0].matcher.match("src/main.py")
        assert not cond.atoms[0].matcher.match("vendor/x.py")

    def test_single_quotes(self):
        """'tool == 'WriteFile'' → same as double quotes"""
        cond, err = parse_if_expression("tool == 'WriteFile'")
        assert err is None
        assert cond is not None
        assert cond.atoms[0].matcher.match("WriteFile")

    def test_mixed_and_or_forbidden(self):
        """'a && b || c' → error"""
        cond, err = parse_if_expression(
            'tool == "A" && tool == "B" || tool == "C"'
        )
        assert cond is None
        assert err is not None
        assert "mixed" in err.lower()

    def test_empty(self):
        cond, err = parse_if_expression("")
        assert cond is None
        assert err is not None

    def test_missing_value(self):
        """'tool == ' → error"""
        cond, err = parse_if_expression("tool ==")
        assert cond is None
        assert err is not None

    def test_invalid_regex(self):
        """'/unclosed → error"""
        cond, err = parse_if_expression("args.command ~= /unclosed")
        assert cond is None
        assert err is not None


# ── substitute_variables ──────────────────────────────


class TestSubstituteVariables:
    def test_file_path(self):
        result = substitute_variables(
            "black $FILE_PATH",
            {"tool_input": {"path": "src/main.py"}},
        )
        assert result == "black src/main.py"

    def test_command(self):
        result = substitute_variables(
            "lint $COMMAND",
            {"tool_input": {"command": "git push"}},
        )
        assert result == "lint git push"

    def test_tool_name(self):
        result = substitute_variables(
            "tool: $TOOL_NAME",
            {"tool_name": "write_file"},
        )
        assert result == "tool: write_file"

    def test_args_json(self):
        result = substitute_variables(
            "args: $ARGS",
            {"tool_input": {"path": "x.py"}},
        )
        assert '"path"' in result
        assert "x.py" in result

    def test_braced_form(self):
        result = substitute_variables(
            "black ${FILE_PATH}",
            {"tool_input": {"path": "main.py"}},
        )
        assert result == "black main.py"

    def test_generic_path(self):
        result = substitute_variables(
            "read $tool_input.path",
            {"tool_input": {"path": "README.md"}},
        )
        assert result == "read README.md"

    def test_multiple_vars(self):
        result = substitute_variables(
            "$TOOL_NAME: $FILE_PATH",
            {"tool_name": "write_file", "tool_input": {"path": "a.py"}},
        )
        assert result == "write_file: a.py"

    def test_event_var(self):
        result = substitute_variables(
            "$EVENT",
            {"event": "PreToolUse"},
        )
        assert result == "PreToolUse"

    def test_session_id(self):
        result = substitute_variables(
            "sid=$SESSION_ID",
            {"session_id": "abc123"},
        )
        assert result == "sid=abc123"

    def test_no_var_unchanged(self):
        result = substitute_variables("echo hello", {})
        assert result == "echo hello"

    def test_missing_variable_empty(self):
        result = substitute_variables("$NOT_EXIST", {})
        assert result == ""
