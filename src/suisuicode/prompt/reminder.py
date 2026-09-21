from __future__ import annotations

# ── 规划模式提醒常量 ──────────────────────────────────

_PLAN_REMINDER_FULL = (
    "当前处于计划模式（PLAN MODE）。你只能使用只读工具 "
    "（read_file、glob、grep）来调研代码库。不得写入文件、"
    "编辑文件或执行 shell 命令。请产出一个清晰、分步的计划，"
    "然后停止，等待用户用 /do 批准后再执行任何操作。"
)

_PLAN_REMINDER_CONCISE = (
    "提醒：当前为计划模式（PLAN MODE）。仅可用只读工具 "
    "（read_file、glob、grep）。不要修改任何文件或执行 shell 命令。"
)

# ── /do 注入 ──────────────────────────────────────────

EXECUTE_DIRECTIVE = "请按上面的计划开始执行。"


# ── 标签包裹 ──────────────────────────────────────────


def system_reminder(body: str) -> str:
    """用 <system-reminder>…</system-reminder> 包裹 body。"""
    return f"<system-reminder>\n{body}\n</system-reminder>"


def plan_reminder(full: bool) -> str:
    """返回包好标签的规划模式提醒。

    full=True → 完整版（首轮/间隔轮）；full=False → 精简版。
    """
    body = _PLAN_REMINDER_FULL if full else _PLAN_REMINDER_CONCISE
    return system_reminder(body)
