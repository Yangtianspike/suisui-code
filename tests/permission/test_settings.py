"""T5: to_rule_set stderr 报错与跳过逻辑 单元测试。"""

from __future__ import annotations

from suisuicode.permission import Decision
from suisuicode.permission.settings import Settings, PermissionsBlock, to_rule_set


class TestToRuleSet:
    def test_valid_rules(self):
        s = Settings(
            permissions=PermissionsBlock(
                allow=["Bash(git *)", "Read(**/*.py)"],
                deny=["Bash(rm *)"],
            )
        )
        rs = to_rule_set(s)
        assert len(rs.allow) == 2
        assert len(rs.deny) == 1

    def test_invalid_rule_skipped_with_stderr(self, capsys):
        """非法 rule 被跳过且 stderr 含 parse failed。"""
        s = Settings(
            permissions=PermissionsBlock(
                allow=["Bash(~[bad)", "Bash(git *)"],  # 第一条非法
            )
        )
        rs = to_rule_set(s)
        captured = capsys.readouterr()
        # 只有第二条合法 rule 进入 allow
        assert len(rs.allow) == 1
        # stderr 含 parse failed
        assert "parse failed" in captured.err
        assert "Bash(~[bad)" in captured.err

    def test_valid_rules_unchanged_by_invalid(self):
        """合法 rule 不受非法 rule 影响。"""
        s = Settings(
            permissions=PermissionsBlock(
                allow=["Bash(git *)"],
                deny=["Bash("],  # 非法
            )
        )
        rs = to_rule_set(s)
        assert len(rs.allow) == 1
        assert len(rs.deny) == 0  # 非法被跳过

    def test_empty_settings(self):
        s = Settings()
        rs = to_rule_set(s)
        assert len(rs.allow) == 0
        assert len(rs.deny) == 0

    def test_exact_rule(self):
        s = Settings(
            permissions=PermissionsBlock(allow=["Bash(=git status)"])
        )
        rs = to_rule_set(s)
        assert len(rs.allow) == 1

        d, hit = rs.match("Bash", "git status")
        assert hit is True
        assert d == Decision.ALLOW

    def test_regex_rule(self):
        s = Settings(
            permissions=PermissionsBlock(allow=["Bash(~^npm (install|test)$)"])
        )
        rs = to_rule_set(s)
        assert len(rs.allow) == 1

        d, hit = rs.match("Bash", "npm install")
        assert hit is True

    def test_not_regex_rule(self):
        s = Settings(
            permissions=PermissionsBlock(deny=["Bash(!~^rm)"])
        )
        rs = to_rule_set(s)
        assert len(rs.deny) == 1
        # 不命中 rm -rf .
        _, hit = rs.match("Bash", "rm -rf .")
        assert hit is False
        # 命中 ls -lh
        _, hit2 = rs.match("Bash", "ls -lh")
        assert hit2 is True
