"""/do /review — 2 条提示词命令。"""

from __future__ import annotations

from suisuicode.permission import Mode
from suisuicode.prompt import EXECUTE_DIRECTIVE

REVIEW_DIRECTIVE = (
    "请审查当前上下文中的代码变更/已读取的文件，指出潜在 bug、可读性问题和可简化处。"
)


async def handle_do(ui, args="") -> None:
    """切回 DEFAULT 模式 + 注入执行指令 + 触发 LLM 回合。"""
    ui.ui_set_mode(Mode.DEFAULT)
    ui.ui_inject_and_send("/do", EXECUTE_DIRECTIVE)


async def handle_review(ui, args="") -> None:
    """注入代码审查请求 + 触发 LLM 回合。"""
    ui.ui_inject_and_send("/review", REVIEW_DIRECTIVE)
