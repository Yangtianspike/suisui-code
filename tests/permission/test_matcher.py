"""T2: Matcher 四种 type × 边界条件单元测试。"""

from __future__ import annotations

import re

import pytest

from suisuicode.permission.matcher import (
    ExactMatcher,
    GlobMatcher,
    NotMatcher,
    RegexMatcher,
    compile_matcher,
)


# ── ExactMatcher ──────────────────────────────────────────


class TestExactMatcher:
    def test_hit(self):
        m = ExactMatcher("git status")
        assert m.match("git status") is True

    def test_miss(self):
        m = ExactMatcher("git status")
        assert m.match("git status -s") is False

    def test_empty_value(self):
        m = ExactMatcher("")
        assert m.match("") is True
        assert m.match("x") is False

    def test_str(self):
        assert str(ExactMatcher("foo")) == "=foo"


# ── GlobMatcher (command) ─────────────────────────────────


class TestGlobMatcherCommand:
    def test_wildcard_hit(self):
        m = GlobMatcher("git *", is_command=True)
        assert m.match("git status") is True

    def test_wildcard_miss(self):
        m = GlobMatcher("git *", is_command=True)
        assert m.match("npm test") is False

    def test_deep_wildcard(self):
        m = GlobMatcher("git *", is_command=True)
        # * 匹配含空格的所有字符
        assert m.match("git push origin main") is True

    def test_exact_no_wildcard(self):
        m = GlobMatcher("ls", is_command=True)
        assert m.match("ls") is True
        assert m.match("ls -l") is False

    def test_str(self):
        assert str(GlobMatcher("git *", is_command=True)) == "git *"


# ── GlobMatcher (path) ────────────────────────────────────


class TestGlobMatcherPath:
    def test_star_segment(self):
        m = GlobMatcher("**/*.py", is_command=False)
        assert m.match("src/main.py") is True
        assert m.match("tests/test_foo.py") is True

    def test_star_segment_miss(self):
        m = GlobMatcher("**/*.py", is_command=False)
        assert m.match("README.md") is False

    def test_exact_filename(self):
        m = GlobMatcher("requirements.txt", is_command=False)
        assert m.match("requirements.txt") is True
        assert m.match("src/requirements.txt") is False

    def test_single_segment_star(self):
        m = GlobMatcher("src/*.py", is_command=False)
        assert m.match("src/main.py") is True
        assert m.match("src/sub/main.py") is False  # * 不跨 /

    def test_str(self):
        assert str(GlobMatcher("**/*.py", is_command=False)) == "**/*.py"


# ── RegexMatcher ──────────────────────────────────────────


class TestRegexMatcher:
    def test_hit(self):
        m = RegexMatcher(r"^npm (install|test)$", re.compile(r"^npm (install|test)$"))
        assert m.match("npm install") is True
        assert m.match("npm test") is True

    def test_miss(self):
        m = RegexMatcher(r"^npm (install|test)$", re.compile(r"^npm (install|test)$"))
        assert m.match("npm run dev") is False

    def test_search_not_fullmatch(self):
        """regex 用 search 而非 fullmatch，部分匹配即命中。"""
        m = RegexMatcher("delete", re.compile("delete"))
        assert m.match("please delete that file") is True

    def test_str(self):
        m = RegexMatcher(r"^npm", re.compile(r"^npm"))
        assert str(m) == "~^npm"


# ── NotMatcher ────────────────────────────────────────────


class TestNotMatcher:
    def test_not_exact_hit(self):
        """!=foo 不命中 foo"""
        m = NotMatcher(ExactMatcher("foo"))
        assert m.match("foo") is False

    def test_not_exact_miss(self):
        """!=foo 命中 bar"""
        m = NotMatcher(ExactMatcher("foo"))
        assert m.match("bar") is True

    def test_not_regex_hit(self):
        """!~^rm 命中不以 rm 开头的"""
        m = NotMatcher(RegexMatcher(r"^rm", re.compile(r"^rm")))
        assert m.match("ls -lh") is True

    def test_not_regex_miss(self):
        """!~^rm 不命中以 rm 开头的"""
        m = NotMatcher(RegexMatcher(r"^rm", re.compile(r"^rm")))
        assert m.match("rm -rf .") is False

    def test_not_glob_hit(self):
        """!git * 命中 npm install"""
        m = NotMatcher(GlobMatcher("git *", is_command=True))
        assert m.match("npm install") is True

    def test_not_glob_miss(self):
        """!git * 不命中 git status"""
        m = NotMatcher(GlobMatcher("git *", is_command=True))
        assert m.match("git status") is False

    def test_str(self):
        m = NotMatcher(ExactMatcher("foo"))
        assert str(m) == "!=foo"


# ── compile_matcher factory ───────────────────────────────


class TestCompileMatcher:
    @pytest.mark.parametrize(
        "pattern,is_command,expected_type,expected_str",
        [
            ("=git status", False, ExactMatcher, "=git status"),
            ("=git status", True, ExactMatcher, "=git status"),
            ("~^npm (install|test)$", False, RegexMatcher, "~^npm (install|test)$"),
            ("!=foo", False, NotMatcher, "!=foo"),
            ("!=foo", True, NotMatcher, "!=foo"),
            ("!~^rm", True, NotMatcher, "!~^rm"),
            ("git *", True, GlobMatcher, "git *"),
            ("git *", False, GlobMatcher, "git *"),
            ("**/*.py", False, GlobMatcher, "**/*.py"),
        ],
        ids=[
            "exact_cmd",
            "exact_path",
            "regex",
            "not_exact_cmd",
            "not_exact_path",
            "not_regex",
            "glob_cmd",
            "glob_path",
            "glob_path_starstar",
        ],
    )
    def test_factory(self, pattern, is_command, expected_type, expected_str):
        m = compile_matcher(pattern, is_command=is_command)
        assert isinstance(m, expected_type)
        assert str(m) == expected_str

    def test_empty_raises(self):
        with pytest.raises(ValueError, match="empty matcher pattern"):
            compile_matcher("", is_command=False)

    def test_invalid_regex_raises(self):
        with pytest.raises(ValueError, match="invalid regex"):
            compile_matcher("~[invalid", is_command=False)

    def test_nested_not(self):
        """!!=foo 双重取反等于 =foo"""
        m = compile_matcher("!!=foo", is_command=False)
        assert isinstance(m, NotMatcher)
        assert isinstance(m.inner, NotMatcher)
        assert isinstance(m.inner.inner, ExactMatcher)

    def test_is_command_flows_to_glob(self):
        """is_command=True 仅在 glob 类型生效。"""
        m = compile_matcher("ls *", is_command=True)
        assert isinstance(m, GlobMatcher)
        assert m.is_command is True

    def test_is_command_false_in_non_glob(self):
        """exact 类型忽略 is_command。"""
        m = compile_matcher("=ls *", is_command=True)
        assert isinstance(m, ExactMatcher)

    def test_glob_command_with_space(self):
        """命令 glob 中 * 匹配空格。"""
        m = compile_matcher("git *", is_command=True)
        assert m.match("git push origin main") is True

    def test_glob_path_no_cross_slash(self):
        """文件 glob 中 * 不跨 /。"""
        m = compile_matcher("src/*.py", is_command=False)
        assert m.match("src/main.py") is True
        assert m.match("src/sub/main.py") is False
