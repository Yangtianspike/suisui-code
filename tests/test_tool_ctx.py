"""tool ctx 单测：with_cwd / cwd_from_ctx / resolve_path。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from suisuicode.tool.ctx import cwd_from_ctx, resolve_path, with_cwd


class TestResolvePath:
    """resolve_path 行为测试。"""

    def test_absolute_path(self) -> None:
        """绝对路径不被 ctx cwd 修改。"""
        result = resolve_path("/tmp/foo")
        # 返回的路径应该是绝对的
        assert Path(result).is_absolute()

    def test_relative_no_ctx(self) -> None:
        """无 ctx cwd 时相对路径以进程 cwd 为基准。"""
        result = resolve_path("foo/bar.txt")
        expected = str(Path(os.getcwd()) / "foo/bar.txt")
        assert result == expected

    def test_relative_with_ctx(self, tmp_path: Path) -> None:
        """有 ctx cwd 时相对路径以 ctx cwd 为基准。"""
        with with_cwd(str(tmp_path)):
            result = resolve_path("hello.txt")
            assert result == str(tmp_path / "hello.txt")

    def test_empty_string_no_ctx(self) -> None:
        """空字符串返回进程 cwd。"""
        result = resolve_path("")
        assert result == str(Path.cwd())

    def test_empty_string_with_ctx(self, tmp_path: Path) -> None:
        """空字符串 + ctx 返回 ctx cwd。"""
        with with_cwd(str(tmp_path)):
            result = resolve_path("")
            assert result == str(tmp_path)

    def test_cwd_from_ctx_default(self) -> None:
        """默认（未设置）返回 None。"""
        assert cwd_from_ctx() is None

    def test_cwd_from_ctx_set(self, tmp_path: Path) -> None:
        """with_cwd 设置后 cwd_from_ctx 返回该值。"""
        with with_cwd(str(tmp_path)):
            assert cwd_from_ctx() == str(tmp_path)

    def test_cwd_from_ctx_reset(self, tmp_path: Path) -> None:
        """with_cwd 退出后 cwd_from_ctx 恢复 None。"""
        with with_cwd(str(tmp_path)):
            pass
        assert cwd_from_ctx() is None

    def test_with_cwd_empty_string(self) -> None:
        """空字符串不设置 ctx。"""
        with with_cwd(""):
            assert cwd_from_ctx() is None

    def test_nested_with_cwd(self, tmp_path: Path) -> None:
        """嵌套 with_cwd——内层覆盖外层，退出恢复。"""
        outer = tmp_path / "outer"
        inner = tmp_path / "inner"
        outer.mkdir(exist_ok=True)
        inner.mkdir(exist_ok=True)

        with with_cwd(str(outer)):
            assert cwd_from_ctx() == str(outer)
            with with_cwd(str(inner)):
                assert cwd_from_ctx() == str(inner)
            assert cwd_from_ctx() == str(outer)
        assert cwd_from_ctx() is None
