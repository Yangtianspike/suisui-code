"""Task notification 注入与辅助函数（ch13 SubAgent）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from suisuicode.task.manager import BackgroundTask


def build_task_notification(bt: "BackgroundTask") -> str:
    """构造 <task-notification> 块（spec F19）。"""
    status_str = str(bt.status)
    name_part = f' name="{bt.name}"' if bt.name else ""
    result_part = bt.result if bt.result else ""
    if bt.err is not None and not result_part:
        result_part = str(bt.err)

    return (
        f"<task-notification>\n"
        f"Task {bt.id}{name_part}: {status_str}\n"
        f"Result: {result_part}\n"
        f"</task-notification>"
    )
