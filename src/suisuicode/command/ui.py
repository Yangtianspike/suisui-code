"""UI Protocol：handler 操作 TUI 的唯一通道 + NopUI 测试桩。

方法名统一使用 ui_ 前缀以避免与 App 现有属性（mode, usage_in 等）冲突。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from suisuicode.permission import Mode


@dataclass
class WorktreeSummary:
    """Worktree 摘要信息（供 slash 命令列表展示）。"""

    name: str
    path: str
    branch: str
    active: bool
    manual: bool


class WorktreeAccessor(Protocol):
    """Worktree 操作的轻量协议，隔离 worktree 包反向依赖。"""

    async def create(self, name: str) -> tuple[str, str]: ...  # (path, branch)
    def list(self) -> list[WorktreeSummary]: ...
    async def enter(self, name: str) -> None: ...
    async def exit(self, action: str, discard: bool) -> bool: ...  # removed
    async def remove(self, name: str, discard: bool) -> None: ...


class UI(Protocol):
    """命令 handler 通过此协议操作 TUI，不持有具体 TUI 类型。"""

    # 输出
    def ui_println(self, msg: str) -> None: ...
    def ui_error(self, msg: str) -> None: ...

    # 模式
    def ui_mode(self) -> Mode: ...
    def ui_set_mode(self, m: Mode) -> None: ...

    # 对话注入（KindPrompt）
    def ui_inject_and_send(self, display_label: str, preset_prompt: str) -> None: ...

    # /status 与 /memory 只读查询
    def ui_usage_in(self) -> int: ...
    def ui_usage_out(self) -> int: ...
    def ui_model_name(self) -> str: ...
    def ui_cwd(self) -> str: ...
    def ui_tool_count(self) -> int: ...
    def ui_memory_files(self) -> list[str]: ...
    def ui_session_path(self) -> str: ...
    def ui_session_id(self) -> str: ...

    # 影响界面动作
    def ui_quit(self) -> None: ...
    def ui_force_compact(self) -> None: ...
    def ui_open_resume_menu(self) -> None: ...
    def ui_clear_and_new_session(self) -> None: ...

    # 状态机查询
    def ui_idle(self) -> bool: ...

    # Skill 相关
    def append_assistant_message(self, text: str) -> None: ...
    def list_catalog_skills(self) -> list: ...
    def list_active_skills(self) -> list[str]: ...
    def clear_active_skills(self) -> None: ...

    # ch12 Hook 相关
    def hook_sources(self) -> list[str]: ...
    def hook_rules(self) -> list: ...

    # ch14 Worktree 相关
    def worktree_accessor(self) -> "WorktreeAccessor | None": ...


class NopUI:
    """UI 协议的测试桩：所有写入 no-op，所有查询返回零值。"""

    def ui_println(self, msg: str) -> None:
        pass

    def ui_error(self, msg: str) -> None:
        pass

    def ui_mode(self) -> Mode:
        return Mode.DEFAULT

    def ui_set_mode(self, m: Mode) -> None:
        pass

    def ui_inject_and_send(self, display_label: str, preset_prompt: str) -> None:
        pass

    def ui_usage_in(self) -> int:
        return 0

    def ui_usage_out(self) -> int:
        return 0

    def ui_model_name(self) -> str:
        return ""

    def ui_cwd(self) -> str:
        return "/"

    def ui_tool_count(self) -> int:
        return 0

    def ui_memory_files(self) -> list[str]:
        return []

    def ui_session_path(self) -> str:
        return ""

    def ui_session_id(self) -> str:
        return ""

    def ui_quit(self) -> None:
        pass

    def ui_force_compact(self) -> None:
        pass

    def ui_open_resume_menu(self) -> None:
        pass

    def ui_clear_and_new_session(self) -> None:
        pass

    def ui_idle(self) -> bool:
        return True

    def append_assistant_message(self, text: str) -> None:
        pass

    def list_catalog_skills(self) -> list:
        return []

    def list_active_skills(self) -> list[str]:
        return []

    def clear_active_skills(self) -> None:
        pass

    def hook_sources(self) -> list[str]:
        return []

    def hook_rules(self) -> list:
        return []

    def worktree_accessor(self) -> WorktreeAccessor | None:
        return None
