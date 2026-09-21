"""T5: parse_rule 新语法 + match_pattern 向后兼容 单元测试。"""

from __future__ import annotations

from suisuicode.permission import Decision
from suisuicode.permission.matcher import ExactMatcher, GlobMatcher, NotMatcher, RegexMatcher
from suisuicode.permission.rule import RuleSet, match_pattern, parse_rule


# ── parse_rule 基础 ──────────────────────────────────────


class TestParseRuleBasic:
    def test_full_match_no_paren(self):
        """无括号 → 全匹配"""
        r, err = parse_rule("Bash")
        assert err is None
        assert r is not None
        assert r.tool == "Bash"
        assert r.matcher is None
        assert r.allow is True

    def test_glob_command(self):
        """Bash(git *) → glob 命令匹配"""
        r, err = parse_rule("Bash(git *)")
        assert err is None
        assert r is not None
        assert r.tool == "Bash"
        assert isinstance(r.matcher, GlobMatcher)
        assert r.matcher.is_command is True
        assert r.matcher.match("git status") is True

    def test_glob_path(self):
        """Write(**/*.py) → glob 路径匹配"""
        r, err = parse_rule("Write(**/*.py)")
        assert err is None
        assert r is not None
        assert r.tool == "Write"
        assert isinstance(r.matcher, GlobMatcher)
        assert r.matcher.is_command is False
        assert r.matcher.match("src/main.py") is True

    def test_empty_paren(self):
        """Tool() → 全匹配"""
        r, err = parse_rule("Bash()")
        assert err is None
        assert r is not None
        assert r.matcher is None


class TestParseRuleExact:
    def test_exact(self):
        """Bash(=git status) → ExactMatcher"""
        r, err = parse_rule("Bash(=git status)")
        assert err is None
        assert r is not None
        assert isinstance(r.matcher, ExactMatcher)
        assert r.matcher.match("git status") is True
        assert r.matcher.match("git status -s") is False

    def test_exact_empty(self):
        """Bash(=) → ExactMatcher("")"""
        r, err = parse_rule("Bash(=)")
        assert err is None
        assert r is not None
        assert isinstance(r.matcher, ExactMatcher)
        assert r.matcher.value == ""


class TestParseRuleRegex:
    def test_regex(self):
        """Bash(~^npm.*) → RegexMatcher"""
        r, err = parse_rule("Bash(~^npm.*)")
        assert err is None
        assert r is not None
        assert isinstance(r.matcher, RegexMatcher)
        assert r.matcher.match("npm install") is True
        assert r.matcher.match("yarn install") is False

    def test_regex_compile_error(self):
        """~[invalid → 编译失败"""
        r, err = parse_rule("Bash(~[invalid)")
        assert r is None
        assert err is not None
        assert "invalid regex" in err


class TestParseRuleNot:
    def test_not_exact(self):
        """Bash(!=foo) → NotMatcher(ExactMatcher("foo"))"""
        r, err = parse_rule("Bash(!=foo)")
        assert err is None
        assert r is not None
        assert isinstance(r.matcher, NotMatcher)
        assert isinstance(r.matcher.inner, ExactMatcher)

    def test_not_regex(self):
        """Bash(!~^rm) → NotMatcher(RegexMatcher(...))"""
        r, err = parse_rule("Bash(!~^rm)")
        assert err is None
        assert r is not None
        assert isinstance(r.matcher, NotMatcher)
        assert isinstance(r.matcher.inner, RegexMatcher)
        assert r.matcher.match("ls -lh") is True
        assert r.matcher.match("rm -rf .") is False

    def test_not_glob(self):
        """Bash(!git *) → NotMatcher(GlobMatcher(...))"""
        r, err = parse_rule("Bash(!git *)")
        assert err is None
        assert r is not None
        assert isinstance(r.matcher, NotMatcher)
        assert isinstance(r.matcher.inner, GlobMatcher)


class TestParseRuleErrors:
    def test_empty_string(self):
        r, err = parse_rule("")
        assert r is None
        assert err == "empty rule string"

    def test_unmatched_paren(self):
        r, err = parse_rule("Bash(git")
        assert r is None
        assert err == "unmatched parenthesis"

    def test_empty_tool(self):
        r, err = parse_rule("(git *)")
        assert r is None
        assert err == "empty tool name"


# ── match_pattern 向后兼容 ────────────────────────────────


class TestMatchPatternCompat:
    def test_empty_pattern(self):
        assert match_pattern("", "anything") is True

    def test_command_glob(self):
        assert match_pattern("git *", "git status", is_command=True) is True
        assert match_pattern("git *", "npm test", is_command=True) is False

    def test_path_glob(self):
        assert match_pattern("**/*.py", "src/main.py", is_command=False) is True
        assert match_pattern("**/*.py", "README.md", is_command=False) is False

    def test_exact_via_compile(self):
        """match_pattern 内部用 compile_matcher, 支持精确匹配"""
        assert match_pattern("=git status", "git status", is_command=True) is True
        assert match_pattern("=git status", "git status -s", is_command=True) is False


# ── RuleSet 集成 ──────────────────────────────────────────


class TestRuleSet:
    def test_deny_first(self):
        """先 deny 再 allow; deny 命中则短路"""
        deny_rule, _ = parse_rule("Bash(git *)")
        deny_rule.allow = False
        allow_rule, _ = parse_rule("Bash(git status)")
        allow_rule.allow = True

        rs = RuleSet(deny=[deny_rule], allow=[allow_rule])

        d, hit = rs.match("Bash", "git status")
        assert hit is True
        assert d == Decision.DENY

    def test_allow_hit(self):
        allow_rule, _ = parse_rule("Bash(=git status)")
        rs = RuleSet(allow=[allow_rule])

        d, hit = rs.match("Bash", "git status")
        assert hit is True
        assert d == Decision.ALLOW

    def test_no_match(self):
        rs = RuleSet()

        d, hit = rs.match("Bash", "anything")
        assert hit is False
        assert d == Decision.ALLOW

    def test_full_match_matcher_none(self):
        """matcher=None → 该工具全部命中"""
        rule, _ = parse_rule("Bash")
        rs = RuleSet(deny=[rule])

        rs.deny[0].allow = False
        d, hit = rs.match("Bash", "rm -rf /")
        assert hit is True
        assert d == Decision.DENY

    def test_exact_matcher(self):
        """精确匹配：Eq(=git status) 命中 git status 不命中 git status -s"""
        rule, _ = parse_rule("Bash(=git status)")
        rs = RuleSet(allow=[rule])

        d, hit = rs.match("Bash", "git status")
        assert hit is True
        assert d == Decision.ALLOW

        _, hit2 = rs.match("Bash", "git status -s")
        assert hit2 is False

    def test_regex_matcher(self):
        """Regex：Bash(~^npm (install|test)$) 命中 npm install"""
        rule, _ = parse_rule("Bash(~^npm (install|test)$)")
        rs = RuleSet(allow=[rule])

        d, hit = rs.match("Bash", "npm install")
        assert hit is True
        assert d == Decision.ALLOW

    def test_not_regex_matcher(self):
        """Not Regex：Bash(!~^rm) 不命中 rm -rf ."""
        rule, _ = parse_rule("Bash(!~^rm)")
        rs = RuleSet(deny=[rule])

        rs.deny[0].allow = False
        _, hit = rs.match("Bash", "rm -rf .")
        assert hit is False

        _, hit2 = rs.match("Bash", "ls -lh")
        assert hit2 is True
