"""SubAgent + Worktree 协同执行模块。

提供 ``build_worktree_notice``（向子 Agent 说明隔离上下文）
和 ``_execute_with_worktree``（完整 worktree 启动链路）。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from suisuicode.tool.ctx import with_cwd

if TYPE_CHECKING:
    from suisuicode.subagent import Definition as AgentDefinition
    from suisuicode.worktree import Manager as WorktreeManager


def build_worktree_notice(parent_cwd: str, wt_path: str) -> str:
    """构造 Worktree 上下文说明文本（注入到子 Agent 的 task 前）。"""
    return (
        "<worktree-context>\n"
        f"你当前在一个独立的 Git Worktree 副本中工作,与父 Agent 隔离。\n"
        f"- 父目录: {parent_cwd}\n"
        f"- 你的工作目录: {wt_path}\n"
        "- 父 Agent 提到的绝对路径基于父目录,你需要翻译成本地路径(替换前缀)再读写\n"
        "- 编辑文件前,必须先在本地 Worktree 重新 read_file 一次,避免使用过时内容\n"
        "</worktree-context>"
    )


async def _execute_with_worktree(
    manager: "WorktreeManager",
    definition: "AgentDefinition",
    sub_agent,
    sub_conv,
    prompt: str,
    events,
) -> str:
    """SubAgent isolation:worktree 完整执行链路。

    1. 创建临时 Worktree
    2. 注入 worktree notice 到 task 前
    3. 用 with_cwd 包住 run_to_completion
    4. auto_cleanup
    5. 有变更时追加保留信息到 final_text
    """
    from suisuicode.worktree import random_agent_name

    name = random_agent_name()
    wt = await manager.create(name, "HEAD", manual=False)
    cwd = str(Path.cwd())
    notice = build_worktree_notice(cwd, wt.path)
    task_text = notice + "\n\n" + prompt

    with with_cwd(wt.path):
        final_text = await sub_agent.run_to_completion(sub_conv, task_text, events)

    report = await manager.auto_cleanup(name)
    if report.kept:
        final_text += (
            f"\n\n[Worktree 保留: {report.path}, 分支 {report.branch}]"
        )

    return final_text
