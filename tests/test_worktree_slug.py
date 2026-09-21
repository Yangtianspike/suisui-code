"""Worktree slug 校验单测。"""

from __future__ import annotations

import pytest

from suisuicode.worktree.slug import flat_slug, validate_slug


class TestValidateSlug:
    """合法 slug 测试。"""

    @pytest.mark.parametrize("name", [
        "alice",
        "team/alice",
        "v1.0",
        "a_b",
        "feature-branch",
        "team.x/v2.0_beta",
        "a" * 64,  # 刚好上限
    ])
    def test_valid_slugs(self, name: str) -> None:
        """合法 slug 不抛异常。"""
        validate_slug(name)

    @pytest.mark.parametrize("name,expected_reason", [
        ("", "不能为空"),
        ("a" * 65, "长度不能超过"),
        ("..", "不能为 '.' 或 '..'"),
        ("./x", "不能为 '.' 或 '..'"),
        ("a//b", "不能包含连续的"),
        ("/x", "不能以 '/' 开头"),
        ("a/", "不能以 '/' 结尾"),
        ("a b", "非法字符"),
        ("a;b", "非法字符"),
        ("../etc", "不能为 '.' 或 '..'"),
    ])
    def test_invalid_slugs(self, name: str, expected_reason: str) -> None:
        """非法 slug 抛 ValueError 且原因匹配。"""
        with pytest.raises(ValueError, match=expected_reason):
            validate_slug(name)


class TestFlatSlug:
    """flat_slug 测试。"""

    def test_simple(self) -> None:
        assert flat_slug("alice") == "alice"

    def test_nested(self) -> None:
        assert flat_slug("team/alice") == "team+alice"

    def test_noop(self) -> None:
        assert flat_slug("a-b_c") == "a-b_c"
