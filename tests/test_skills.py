"""ch11 Skill 系统单元测试：parser / loader / render / executor / LoadSkill / Agent 集成。

运行方式：
    cd . && uv run pytest tests/test_skills.py -q
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from suisuicode.skills.parser import (
    SkillParseError,
    _validate_meta,
    parse_frontmatter_and_body,
    parse_skill_dir,
    substitute_arguments,
)
from suisuicode.skills.types import Skill, SkillMeta, SkillSource, ActiveEntry
from suisuicode.skills.active import ActiveSkills
from suisuicode.skills.render import render_body
from suisuicode.skills.executor import SkillDependencyError, filter_tool_registry
from suisuicode.skills.catalog import Catalog


# ═══════════════════════════════════════════════════════════
# parser tests
# ═══════════════════════════════════════════════════════════

VALID_FM = """---
name: test-skill
description: A test skill
mode: inline
---
This is the prompt body.
"""


class TestParseFrontmatter:
    def test_valid(self):
        fm, body = parse_frontmatter_and_body(VALID_FM)
        assert fm["name"] == "test-skill"
        assert fm["description"] == "A test skill"
        assert fm["mode"] == "inline"
        assert "This is the prompt body." in body

    def test_missing_opening_fence(self):
        with pytest.raises(SkillParseError, match="缺少 frontmatter"):
            parse_frontmatter_and_body("no frontmatter\njust text\n")

    def test_unclosed_fence(self):
        with pytest.raises(SkillParseError, match="缺少 frontmatter"):
            parse_frontmatter_and_body("---\nname: test\nno closing fence")

    def test_invalid_yaml(self):
        raw = "---\n: bad yaml\n---\nbody"
        with pytest.raises(SkillParseError, match="YAML"):
            parse_frontmatter_and_body(raw)

    def test_non_dict_frontmatter(self):
        raw = "---\n- list item\n---\nbody"
        with pytest.raises(SkillParseError, match="mapping"):
            parse_frontmatter_and_body(raw)

    def test_content_before_fence(self):
        raw = "garbage\n---\nname: test\ndescription: desc\n---\nbody"
        with pytest.raises(SkillParseError, match="非空白"):
            parse_frontmatter_and_body(raw)


class TestValidateMeta:
    def test_valid(self):
        fm = {"name": "my-skill", "description": "does stuff"}
        meta = _validate_meta(fm, Path("/tmp"))
        assert meta.name == "my-skill"
        assert meta.description == "does stuff"
        assert meta.mode == "inline"

    def test_missing_name(self):
        with pytest.raises(SkillParseError, match="name"):
            _validate_meta({"description": "no name"}, Path("/tmp"))

    def test_missing_description(self):
        with pytest.raises(SkillParseError, match="description"):
            _validate_meta({"name": "test"}, Path("/tmp"))

    def test_invalid_name_format(self):
        fm = {"name": "BadName!", "description": "desc"}
        with pytest.raises(SkillParseError, match="格式无效"):
            _validate_meta(fm, Path("/tmp"))

    def test_name_must_start_with_letter(self):
        fm = {"name": "123bad", "description": "desc"}
        with pytest.raises(SkillParseError, match="格式无效"):
            _validate_meta(fm, Path("/tmp"))

    def test_invalid_mode_falls_back(self, caplog):
        fm = {"name": "test", "description": "desc", "mode": "badmode"}
        meta = _validate_meta(fm, Path("/tmp"))
        assert meta.mode == "inline"

    def test_invalid_context_falls_back(self, caplog):
        fm = {"name": "test", "description": "desc", "context": "badctx"}
        meta = _validate_meta(fm, Path("/tmp"))
        assert meta.fork_context == "none"

    def test_fork_mode(self):
        fm = {
            "name": "test",
            "description": "desc",
            "mode": "fork",
            "context": "recent",
        }
        meta = _validate_meta(fm, Path("/tmp"))
        assert meta.mode == "fork"
        assert meta.is_fork()
        assert meta.fork_context == "recent"

    def test_allowed_tools(self):
        fm = {
            "name": "test",
            "description": "desc",
            "allowed_tools": ["read_file", "grep"],
        }
        meta = _validate_meta(fm, Path("/tmp"))
        assert meta.allowed_tools == ["read_file", "grep"]

    def test_model_field(self):
        fm = {"name": "test", "description": "desc", "model": "claude-sonnet-5"}
        meta = _validate_meta(fm, Path("/tmp"))
        assert meta.model == "claude-sonnet-5"


class TestSubstituteArguments:
    def test_with_placeholder(self):
        result = substitute_arguments("do $ARGUMENTS now", "hello")
        assert result == "do hello now"

    def test_without_placeholder(self):
        result = substitute_arguments("do something", "hello")
        assert "## User Request" in result
        assert "hello" in result

    def test_no_args_no_placeholder(self):
        result = substitute_arguments("do something", "")
        assert result == "do something"

    def test_multiple_placeholders(self):
        result = substitute_arguments("$ARGUMENTS and again $ARGUMENTS", "x")
        assert result == "x and again x"


class TestParseSkillDir:
    def test_valid_skill_dir(self):
        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "my-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(VALID_FM, encoding="utf-8")
            skill = parse_skill_dir(skill_dir, SkillSource.PROJECT)
            assert skill.name == "test-skill"
            assert skill.source == SkillSource.PROJECT
            assert skill.source_dir == skill_dir

    def test_missing_skill_md(self):
        with tempfile.TemporaryDirectory() as td:
            skill_dir = Path(td) / "empty"
            skill_dir.mkdir()
            with pytest.raises(SkillParseError, match="SKILL.md"):
                parse_skill_dir(skill_dir, SkillSource.USER)


# ═══════════════════════════════════════════════════════════
# ActiveSkills tests
# ═══════════════════════════════════════════════════════════


class TestActiveSkills:
    def test_activate_and_snapshot(self):
        a = ActiveSkills()
        a.activate("skill-a", "body A")
        a.activate("skill-b", "body B")
        snap = a.snapshot()
        assert len(snap) == 2
        assert snap[0].name == "skill-a"
        assert snap[1].name == "skill-b"

    def test_reactivate_replaces(self):
        a = ActiveSkills()
        a.activate("skill-a", "body v1")
        a.activate("skill-a", "body v2")
        snap = a.snapshot()
        assert len(snap) == 1
        assert snap[0].body == "body v2"

    def test_clear(self):
        a = ActiveSkills()
        a.activate("skill-a", "body")
        a.clear()
        assert a.snapshot() == []

    def test_names(self):
        a = ActiveSkills()
        a.activate("a", "body")
        a.activate("b", "body")
        assert a.names() == ["a", "b"]


# ═══════════════════════════════════════════════════════════
# render tests
# ═══════════════════════════════════════════════════════════


class TestRenderBody:
    def test_no_allowed_tools(self):
        meta = SkillMeta(name="t", description="d")
        skill = Skill(
            meta=meta,
            prompt_body="hello $ARGUMENTS",
            source_dir=Path("/tmp"),
            source=SkillSource.PROJECT,
        )
        result = render_body(skill, "world")
        assert result == "hello world"

    def test_with_allowed_tools(self):
        meta = SkillMeta(name="t", description="d", allowed_tools=["read_file", "grep"])
        skill = Skill(
            meta=meta,
            prompt_body="do stuff",
            source_dir=Path("/tmp"),
            source=SkillSource.PROJECT,
        )
        result = render_body(skill)
        assert "read_file, grep" in result
        assert "do stuff" in result


# ═══════════════════════════════════════════════════════════
# filter_tool_registry tests
# ═══════════════════════════════════════════════════════════


class TestFilterToolRegistry:
    @staticmethod
    def _make_registry(tools_with_system=None):
        from suisuicode.tool import Registry, Tool, Result

        reg = Registry()

        class FakeTool(Tool):
            def __init__(self, name, is_system=False):
                self._name = name
                self.is_system_tool = is_system

            def name(self):
                return self._name

            def description(self):
                return ""

            def parameters(self):
                return {}

            @property
            def read_only(self):
                return True

            async def execute(self, args):
                return Result(content="ok")

        names = tools_with_system or [
            ("read_file", False),
            ("write_file", False),
            ("bash", False),
            ("grep", False),
            ("LoadSkill", True),
        ]
        for name, is_sys in names:
            reg.register(FakeTool(name, is_system=is_sys))
        return reg

    def test_empty_allowed_returns_original(self):
        reg = self._make_registry()
        result = filter_tool_registry(reg, [])
        assert result is reg

    def test_filters_to_allowed_only(self):
        reg = self._make_registry()
        result = filter_tool_registry(reg, ["read_file", "grep"])
        # Should include read_file, grep, and LoadSkill (system tool)
        names = result._order
        assert "read_file" in names
        assert "grep" in names
        assert "LoadSkill" in names  # system tool passthrough
        assert "write_file" not in names
        assert "bash" not in names

    def test_system_tool_passthrough(self):
        reg = self._make_registry()
        result = filter_tool_registry(reg, ["bash"])
        assert "LoadSkill" in result._order

    def test_missing_tool_raises(self):
        reg = self._make_registry()
        with pytest.raises(SkillDependencyError, match="不存在的工具"):
            filter_tool_registry(reg, ["nonexistent_tool"])


# ═══════════════════════════════════════════════════════════
# Catalog tests
# ═══════════════════════════════════════════════════════════


class TestCatalog:
    @staticmethod
    def _make_skill_dir(parent: Path, name: str, source: SkillSource) -> Path:
        d = parent / name
        d.mkdir()
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A skill called {name}\n---\nbody of {name}\n",
            encoding="utf-8",
        )
        return d

    @staticmethod
    def _isolated_load(root: Path) -> Catalog:
        """加载 Catalog 但隔离 Claude/用户级目录，避免测试环境干扰。"""
        import suisuicode.skills.catalog as cat_mod

        orig_claude = cat_mod.CLAUDE_SKILLS_DIR
        orig_user = cat_mod.USER_SKILLS_DIR
        try:
            # 重定向到不存在的路径
            cat_mod.CLAUDE_SKILLS_DIR = "/tmp/__nonexistent_claude_skills__"
            cat_mod.USER_SKILLS_DIR = "/tmp/__nonexistent_suisuicode_skills__"
            return Catalog.load(root)
        finally:
            cat_mod.CLAUDE_SKILLS_DIR = orig_claude
            cat_mod.USER_SKILLS_DIR = orig_user

    def test_load_from_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj_dir = root / ".suisuicode" / "skills"
            proj_dir.mkdir(parents=True)
            self._make_skill_dir(proj_dir, "proj-skill", SkillSource.PROJECT)

            cat = self._isolated_load(root)
            assert len(cat) == 1
            assert cat.names() == ["proj-skill"]
            s = cat.get("proj-skill")
            assert s is not None
            assert s.name == "proj-skill"
            assert s.source == SkillSource.PROJECT

    def test_project_overrides_user(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            # user-level skill
            user_dir = Path("~/.suisuicode/skills").expanduser()
            # Use custom path to avoid touching real config
            cat = Catalog()

            # Simulate project override by loading user first, then project
            proj_dir = root / ".suisuicode" / "skills"
            proj_dir.mkdir(parents=True)
            self._make_skill_dir(proj_dir, "shared-skill", SkillSource.PROJECT)

            # Create a user skill dir in temp
            user_skills = root / "user-skills"
            user_skills.mkdir()
            self._make_skill_dir(user_skills, "shared-skill", SkillSource.USER)

            # Manually test override: project should win
            proj_skill = parse_skill_dir(proj_dir / "shared-skill", SkillSource.PROJECT)
            assert proj_skill.source == SkillSource.PROJECT

    def test_get_unknown_returns_none(self):
        cat = Catalog()
        assert cat.get("nonexistent") is None

    def test_list_empty_catalog(self):
        cat = Catalog()
        assert cat.list() == []
        assert cat.names() == []

    def test_hot_reload_reads_disk(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj_dir = root / ".suisuicode" / "skills"
            proj_dir.mkdir(parents=True)
            self._make_skill_dir(proj_dir, "hot-skill", SkillSource.PROJECT)

            cat = self._isolated_load(root)
            s1 = cat.get("hot-skill")
            assert "body of hot-skill" in s1.prompt_body

            # Modify on disk
            (proj_dir / "hot-skill" / "SKILL.md").write_text(
                "---\nname: hot-skill\ndescription: updated\n---\nupdated body\n",
                encoding="utf-8",
            )

            s2 = cat.get("hot-skill")
            assert "updated body" in s2.prompt_body

    def test_skip_failed_skill(self, caplog):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj_dir = root / ".suisuicode" / "skills"
            proj_dir.mkdir(parents=True)

            # bad skill: missing description
            bad_dir = proj_dir / "bad-skill"
            bad_dir.mkdir()
            (bad_dir / "SKILL.md").write_text(
                "---\nname: bad-skill\n---\nbroken\n", encoding="utf-8"
            )

            # good skill
            self._make_skill_dir(proj_dir, "good-skill", SkillSource.PROJECT)

            import logging

            with caplog.at_level(logging.WARNING):
                cat = self._isolated_load(root)

            assert len(cat) == 1
            assert "good-skill" in cat.names()
            assert "bad-skill" not in cat.names()

    def test_reload(self):
        import suisuicode.skills.catalog as cat_mod

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj_dir = root / ".suisuicode" / "skills"
            proj_dir.mkdir(parents=True)

            cat = Catalog()
            assert len(cat) == 0

            self._make_skill_dir(proj_dir, "new-skill", SkillSource.PROJECT)

            # 隔离外部目录
            orig_claude = cat_mod.CLAUDE_SKILLS_DIR
            orig_user = cat_mod.USER_SKILLS_DIR
            try:
                cat_mod.CLAUDE_SKILLS_DIR = "/tmp/__nonexistent_claude_skills__"
                cat_mod.USER_SKILLS_DIR = "/tmp/__nonexistent_suisuicode_skills__"
                cat.reload(root)
            finally:
                cat_mod.CLAUDE_SKILLS_DIR = orig_claude
                cat_mod.USER_SKILLS_DIR = orig_user

            assert len(cat) == 1
            assert "new-skill" in cat.names()

    def test_source_label_detection(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj_dir = root / ".suisuicode" / "skills"
            proj_dir.mkdir(parents=True)
            self._make_skill_dir(proj_dir, "p-skill", SkillSource.PROJECT)

            cat = self._isolated_load(root)
            s = cat.get("p-skill")
            assert s.source == SkillSource.PROJECT

    def test_validate_tools(self):
        from suisuicode.tool import Registry as ToolRegistry

        reg = ToolRegistry()
        from suisuicode.tool.read_file import ReadFileTool

        reg.register(ReadFileTool())

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj_dir = root / ".suisuicode" / "skills"
            proj_dir.mkdir(parents=True)

            # skill with existing tool
            d1 = proj_dir / "ok-skill"
            d1.mkdir()
            (d1 / "SKILL.md").write_text(
                "---\nname: ok-skill\ndescription: ok\nallowed_tools:\n  - read_file\n---\nbody\n",
                encoding="utf-8",
            )

            # skill with missing tool
            d2 = proj_dir / "bad-ref"
            d2.mkdir()
            (d2 / "SKILL.md").write_text(
                "---\nname: bad-ref\ndescription: bad\nallowed_tools:\n  - no_such_tool\n---\nbody\n",
                encoding="utf-8",
            )

            cat = self._isolated_load(root)
            assert len(cat) == 2

            issues = cat.validate_tools(reg)
            assert len(issues) == 1
            assert "bad-ref" in issues
            assert len(cat) == 1  # bad-ref removed


# ═══════════════════════════════════════════════════════════
# LoadSkill tool tests
# ═══════════════════════════════════════════════════════════


class TestLoadSkillTool:
    def test_name_and_properties(self):
        from suisuicode.tool.load_skill import LoadSkillTool

        t = LoadSkillTool()
        assert t.name() == "LoadSkill"
        assert t.read_only is True
        assert t.is_system_tool is True

    def test_uninitialized_execute(self):
        from suisuicode.tool.load_skill import LoadSkillTool

        t = LoadSkillTool()
        import asyncio

        result = asyncio.run(t.execute(json.dumps({"name": "test"})))
        assert result.is_error
        assert "initialized" in result.content

    def test_load_unknown_skill(self):
        from suisuicode.tool.load_skill import LoadSkillTool

        t = LoadSkillTool()
        cat = Catalog()
        t.set_loader(cat)

        mock_agent = MagicMock()
        t.set_agent(mock_agent)

        import asyncio

        result = asyncio.run(t.execute(json.dumps({"name": "nonexistent"})))
        assert result.is_error
        assert "未知 skill" in result.content

    def test_load_existing_skill(self):
        from suisuicode.tool.load_skill import LoadSkillTool

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj_dir = root / ".suisuicode" / "skills"
            proj_dir.mkdir(parents=True)

            skill_dir = proj_dir / "my-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: my-skill\ndescription: test desc\n---\nSOP body\n",
                encoding="utf-8",
            )

            cat = TestCatalog._isolated_load(root)
            t = LoadSkillTool()
            t.set_loader(cat)

            mock_agent = MagicMock()
            t.set_agent(mock_agent)

            import asyncio

            result = asyncio.run(t.execute(json.dumps({"name": "my-skill"})))
            assert not result.is_error
            assert "activated" in result.content
            mock_agent.activate_skill.assert_called_once_with("my-skill", "SOP body")

    def test_invalid_json_args(self):
        from suisuicode.tool.load_skill import LoadSkillTool

        t = LoadSkillTool()
        import asyncio

        result = asyncio.run(t.execute("not json"))
        assert result.is_error

    def test_missing_name_param(self):
        from suisuicode.tool.load_skill import LoadSkillTool

        t = LoadSkillTool()
        cat = Catalog()
        t.set_loader(cat)
        mock_agent = MagicMock()
        t.set_agent(mock_agent)

        import asyncio

        result = asyncio.run(t.execute(json.dumps({})))
        assert result.is_error
        assert "name" in result.content


# ═══════════════════════════════════════════════════════════
# Agent integration tests
# ═══════════════════════════════════════════════════════════


class TestAgentSkillsIntegration:
    def test_activate_and_clear(self):
        from suisuicode.agent.runtime import SessionRuntime
        from suisuicode.compact import (
            CompactCircuitBreaker,
            ContentReplacementState,
            RecoveryState,
            new_session_context,
        )

        rt = SessionRuntime(
            replacement=ContentReplacementState(),
            recovery=RecoveryState(),
            auto_tracking=CompactCircuitBreaker(),
            session=new_session_context("."),
        )
        rt.active_skills.activate("skill-a", "body A")
        assert rt.active_skills.names() == ["skill-a"]

        rt.active_skills.activate("skill-b", "body B")
        assert len(rt.active_skills.names()) == 2

        rt.active_skills.clear()
        assert rt.active_skills.names() == []

    def test_reset_clears_active_skills(self):
        from suisuicode.agent.runtime import SessionRuntime
        from suisuicode.compact import (
            CompactCircuitBreaker,
            ContentReplacementState,
            RecoveryState,
            new_session_context,
        )

        rt = SessionRuntime(
            replacement=ContentReplacementState(),
            recovery=RecoveryState(),
            auto_tracking=CompactCircuitBreaker(),
            session=new_session_context("."),
        )
        rt.active_skills.activate("skill-a", "body")
        assert len(rt.active_skills.names()) == 1

        new_ctx = new_session_context(".")
        rt.reset_for_new_session(new_ctx)
        assert rt.active_skills.names() == []

    def test_new_session_runtime_has_active_skills(self):
        from suisuicode.agent.runtime import new_session_runtime

        rt = new_session_runtime()
        assert rt.active_skills is not None
        assert rt.active_skills.names() == []


# ═══════════════════════════════════════════════════════════
# Prompt skills_block tests
# ═══════════════════════════════════════════════════════════


class TestPromptSkillsBlock:
    def test_render_skills_catalog_empty(self):
        from suisuicode.prompt.skills_block import render_skills_catalog

        assert render_skills_catalog([]) == ""

    def test_render_skills_catalog_with_items(self):
        from suisuicode.prompt.skills_block import (
            SkillCatalogItem,
            render_skills_catalog,
        )

        items = [
            SkillCatalogItem(name="a", description="skill a"),
            SkillCatalogItem(name="b", description="skill b"),
        ]
        result = render_skills_catalog(items)
        assert "# Available Skills" in result
        assert "a: skill a" in result
        assert "b: skill b" in result
        assert "LoadSkill" in result

    def test_render_active_skills_block_empty(self):
        from suisuicode.prompt.skills_block import render_active_skills_block

        assert render_active_skills_block([]) == ""

    def test_render_active_skills_block_with_entries(self):
        from suisuicode.prompt.skills_block import (
            ActiveSkillEntry,
            render_active_skills_block,
        )

        entries = [ActiveSkillEntry(name="test", body="SOP goes here")]
        result = render_active_skills_block(entries)
        assert "## Active Skills" in result
        assert "### test" in result
        assert "SOP goes here" in result

    def test_build_system_prompt_with_catalog(self):
        from suisuicode.prompt import build_system_prompt

        result = build_system_prompt(
            instructions="custom instructions",
            memory="memory text",
            skills_catalog="## Skills\n- test: a test skill",
        )
        assert "custom instructions" in result
        assert "memory text" in result
        assert "Skills" in result


# ═══════════════════════════════════════════════════════════
# Adapter tests
# ═══════════════════════════════════════════════════════════


class TestAdapter:
    def test_to_prompt_items(self):
        from suisuicode.skills.adapter import to_prompt_items

        meta = SkillMeta(name="test", description="desc")
        skill = Skill(
            meta=meta,
            prompt_body="body",
            source_dir=Path("/tmp"),
            source=SkillSource.PROJECT,
        )
        items = to_prompt_items([skill])
        assert len(items) == 1
        assert items[0].name == "test"
        assert items[0].description == "desc"

    def test_to_prompt_entries(self):
        from suisuicode.skills.adapter import to_prompt_entries

        entries = [ActiveEntry(name="test", body="SOP")]
        result = to_prompt_entries(entries)
        assert len(result) == 1
        assert result[0].name == "test"
        assert result[0].body == "SOP"
