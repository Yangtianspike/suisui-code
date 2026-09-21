from __future__ import annotations

import os
import tempfile

from suisuicode.prompt import (
    Environment,
    build_system_prompt,
    optional_sections,
    plan_reminder,
    system_reminder,
)
from suisuicode.prompt.environment import gather_environment


# ── 装配顺序 / 跳空槽 / N1 确定性 ────────────────────


def test_assemble_system_priority_order():
    """AC1: 模块按 priority 升序排列，身份段在工具使用段之前。"""
    result = build_system_prompt()
    identity_pos = result.index("你是 SuisuiCode")
    tool_pos = result.index("ReadFile")
    assert identity_pos < tool_pos, "身份段应出现在工具使用段之前"


def test_assemble_system_blank_lines():
    """AC1: 模块间以空行分隔。"""
    result = build_system_prompt()
    # Should contain double newlines between modules
    assert "\n\n" in result, "模块间应有空行分隔"


def test_assemble_empty_slot_skipped():
    """AC2: 空 content 模块被跳过，不留多余空行。"""
    # Test via build_system_prompt — optional slots are all content=""
    result = build_system_prompt()
    # Optional slots should not appear
    assert "# 自定义指令" not in result
    assert "# 已激活 Skill" not in result
    assert "# 长期记忆" not in result
    # But fixed sections should appear
    assert "# Identity" in result
    assert "# 系统" in result


def test_optional_modules_empty_by_default():
    """AC2: 可选模块 content 均为空。"""
    for sec in optional_sections():
        assert sec.content == "", f"{sec.name} 应初始为空"


def test_deterministic_across_calls():
    """AC5/N1: 连续两次 build_system_prompt() 逐字节相等。"""
    a = build_system_prompt()
    b = build_system_prompt()
    assert a == b, "两次装配结果应逐字节相等"


def test_stable_block_unchanged_by_environment():
    """AC5/N1: 稳定系统提示不包含环境相关信息。"""
    prompt = build_system_prompt()
    # Should not contain date-like strings
    import datetime

    today = datetime.date.today().isoformat()
    assert today not in prompt, "稳定块不应含当前日期"
    # Should not contain working directory
    assert "Working directory" not in prompt


# ── F5 双重强化 ──────────────────────────────────────


def test_f5_double_emphasis_in_system_prompt():
    """AC7/F5: 系统提示模块中含双重强化关键词。"""
    prompt = build_system_prompt()
    # 编辑前先读
    assert "ReadFile" in prompt, "系统提示应提到 ReadFile"
    assert "编辑" in prompt, "系统提示应提到编辑"
    # 优先用专用工具
    assert "优先" in prompt, "系统提示应强调优先用专用工具"


# ── 环境信息 ──────────────────────────────────────────


def test_environment_render_contains_fields():
    """AC3: Environment.render() 包含各字段。"""
    env = Environment(
        working_dir="/home/test",
        platform="linux",
        date="2026-07-06",
        git_status="clean",
        version="0.1.0",
        model="claude-opus-4-8",
    )
    text = env.render()
    assert "环境信息：" in text
    assert "/home/test" in text
    assert "linux" in text
    assert "2026-07-06" in text
    assert "clean" in text
    assert "0.1.0" in text
    assert "claude-opus-4-8" in text


def test_environment_render_skips_empty():
    """空字段在 render 中应被省略。"""
    env = Environment(
        working_dir="", platform="linux", date="", git_status="", version="", model=""
    )
    text = env.render()
    assert "环境信息：" in text
    assert "linux" in text
    # Empty fields should be absent
    assert "工作目录" not in text
    assert "当前日期" not in text
    assert "Git 状态" not in text
    assert "应用版本" not in text
    assert "当前模型" not in text


def test_environment_gather_non_git_dir():
    """AC13: 非 git 目录 git_status 降级为空。"""
    with tempfile.TemporaryDirectory() as tmp:
        orig = os.getcwd()
        try:
            os.chdir(tmp)
            env = gather_environment("0.1.0", "test-model")
            assert env.git_status == "", f"非 git 目录应返回空, got {env.git_status!r}"
            assert env.working_dir != ""
            assert env.platform != ""
            assert env.date != ""
            assert env.version == "0.1.0"
            assert env.model == "test-model"
        finally:
            os.chdir(orig)


# ── 补充消息与规划提醒 ───────────────────────────────


def test_system_reminder_wraps():
    """AC8: system_reminder 以 <system-reminder> 包裹。"""
    result = system_reminder("Hello")
    assert "<system-reminder>" in result
    assert "</system-reminder>" in result
    assert "Hello" in result


def test_plan_reminder_full():
    """AC9: plan_reminder(True) 含标签与完整文案。"""
    result = plan_reminder(full=True)
    assert "<system-reminder>" in result
    assert "计划模式" in result
    assert "只读" in result
    assert "/do" in result


def test_plan_reminder_concise():
    """F7: plan_reminder(False) 用精简文案。"""
    full = plan_reminder(full=True)
    concise = plan_reminder(full=False)
    assert len(concise) < len(full), "精简版应比完整版短"
    assert "<system-reminder>" in concise
    assert "计划模式" in concise
