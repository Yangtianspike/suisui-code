import json
import os
import tempfile

import pytest

from suisuicode.tool import Registry, new_default_registry


class TestRegistry:
    def test_definitions_count(self):
        reg = new_default_registry()
        defs = reg.definitions()
        assert len(defs) == 6
        names = [d.name for d in defs]
        assert names == ["read_file", "write_file", "edit_file", "bash", "glob", "grep"]

    def test_get_hit(self):
        reg = new_default_registry()
        assert reg.get("read_file") is not None
        assert reg.get("unknown") is None

    def test_duplicate_register_raises(self):
        from suisuicode.tool.read_file import ReadFileTool

        reg = Registry()
        reg.register(ReadFileTool())
        with pytest.raises(ValueError, match="已注册"):
            reg.register(ReadFileTool())


class TestReadFile:
    @pytest.mark.asyncio
    async def test_read_existing(self):
        from suisuicode.tool.read_file import ReadFileTool

        t = ReadFileTool()
        r = await t.execute(json.dumps({"path": "pyproject.toml"}))
        assert not r.is_error
        assert "\t" in r.content  # has line numbers

    @pytest.mark.asyncio
    async def test_read_nonexistent(self):
        from suisuicode.tool.read_file import ReadFileTool

        t = ReadFileTool()
        r = await t.execute(json.dumps({"path": "/tmp/nonexistent_xyz_file"}))
        assert r.is_error
        assert "不存在" in r.content

    @pytest.mark.asyncio
    async def test_read_is_dir(self):
        from suisuicode.tool.read_file import ReadFileTool

        t = ReadFileTool()
        r = await t.execute(json.dumps({"path": "src"}))
        assert r.is_error
        assert "目录" in r.content


class TestWriteFile:
    @pytest.mark.asyncio
    async def test_write_new(self):
        from suisuicode.tool.write_file import WriteFileTool

        t = WriteFileTool()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "test.txt")
            r = await t.execute(json.dumps({"path": p, "content": "hello"}))
            assert not r.is_error
            assert os.path.exists(p)
            assert open(p).read() == "hello"

    @pytest.mark.asyncio
    async def test_write_nested_dir(self):
        from suisuicode.tool.write_file import WriteFileTool

        t = WriteFileTool()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a", "b", "c.txt")
            r = await t.execute(json.dumps({"path": p, "content": "nested"}))
            assert not r.is_error
            assert os.path.exists(p)
            assert open(p).read() == "nested"


class TestEditFile:
    @pytest.mark.asyncio
    async def test_edit_zero_matches(self):
        from suisuicode.tool.edit_file import EditFileTool

        t = EditFileTool()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.txt")
            open(p, "w").write("hello\nworld")
            r = await t.execute(
                json.dumps({"path": p, "old_string": "xyz", "new_string": "abc"})
            )
            assert r.is_error
            assert "未找到匹配" in r.content

    @pytest.mark.asyncio
    async def test_edit_one_match(self):
        from suisuicode.tool.edit_file import EditFileTool

        t = EditFileTool()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.txt")
            open(p, "w").write("hello\nworld")
            r = await t.execute(
                json.dumps({"path": p, "old_string": "hello", "new_string": "hi"})
            )
            assert not r.is_error
            assert open(p).read() == "hi\nworld"

    @pytest.mark.asyncio
    async def test_edit_multi_matches(self):
        from suisuicode.tool.edit_file import EditFileTool

        t = EditFileTool()
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.txt")
            open(p, "w").write("hello\nhello")
            r = await t.execute(
                json.dumps({"path": p, "old_string": "hello", "new_string": "hi"})
            )
            assert r.is_error
            assert "匹配到" in r.content


class TestBash:
    @pytest.mark.asyncio
    async def test_echo(self):
        from suisuicode.tool.bash import BashTool

        t = BashTool()
        r = await t.execute(json.dumps({"command": "echo hello"}))
        assert not r.is_error
        assert "hello" in r.content

    @pytest.mark.asyncio
    async def test_exit_code(self):
        from suisuicode.tool.bash import BashTool

        t = BashTool()
        r = await t.execute(json.dumps({"command": "exit 42"}))
        assert not r.is_error
        assert "42" in r.content


class TestGlob:
    @pytest.mark.asyncio
    async def test_glob_py(self):
        from suisuicode.tool.glob_tool import GlobTool

        t = GlobTool()
        r = await t.execute(json.dumps({"pattern": "**/*.py", "path": "src/suisuicode"}))
        assert not r.is_error
        assert len(r.content.splitlines()) > 0

    @pytest.mark.asyncio
    async def test_glob_no_match(self):
        from suisuicode.tool.glob_tool import GlobTool

        t = GlobTool()
        r = await t.execute(json.dumps({"pattern": "**/*.nonexistent_xyz"}))
        assert not r.is_error
        assert r.content == "无匹配" or "匹配" not in r.content


class TestGrep:
    @pytest.mark.asyncio
    async def test_grep_keyword(self):
        from suisuicode.tool.grep_tool import GrepTool

        t = GrepTool()
        r = await t.execute(
            json.dumps({"pattern": "class.*Tool", "path": "src/suisuicode/tool"})
        )
        assert not r.is_error
        assert len(r.content.splitlines()) > 0

    @pytest.mark.asyncio
    async def test_grep_no_match(self):
        from suisuicode.tool.grep_tool import GrepTool

        t = GrepTool()
        r = await t.execute(json.dumps({"pattern": "XYZZZ_NONEXISTENT_12345"}))
        assert not r.is_error
        assert "无命中" in r.content
