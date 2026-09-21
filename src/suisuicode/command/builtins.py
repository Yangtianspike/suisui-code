"""register_builtins(reg) — 一次性注册 12 条内置命令。"""

from __future__ import annotations

from suisuicode.command.builtin_local import (
    handle_memory,
    handle_permission,
    handle_session,
    handle_status,
    make_help_handler,
)
from suisuicode.command.builtin_prompt import (
    handle_do,
    handle_review,
)
from suisuicode.command.builtin_skill import handle_skill
from suisuicode.command.builtin_ui import (
    handle_clear,
    handle_compact,
    handle_exit,
    handle_plan,
    handle_resume,
)
from suisuicode.command.command import Command, Kind
from suisuicode.command.registry import Registry


def register_builtins(reg: Registry) -> None:
    """按字典序向 reg 注册全部 12 条内置命令。"""

    # /clear (Kind.UI)
    reg.register(
        Command(
            name="clear",
            description="清空当前会话，开启新会话",
            kind=Kind.UI,
            handler=handle_clear,
        )
    )

    # /compact (Kind.UI)
    reg.register(
        Command(
            name="compact",
            description="手动触发上下文压缩",
            kind=Kind.UI,
            handler=handle_compact,
        )
    )

    # /do (Kind.PROMPT)
    reg.register(
        Command(
            name="do",
            description="切回全工具模式并按计划执行",
            kind=Kind.PROMPT,
            handler=handle_do,
        )
    )

    # /exit (Kind.UI)
    reg.register(
        Command(
            name="exit",
            description="关闭 SuisuiCode",
            kind=Kind.UI,
            handler=handle_exit,
        )
    )

    # /help (Kind.LOCAL) — handler 通过工厂捕获 reg
    reg.register(
        Command(
            name="help",
            description="列出所有可用命令",
            kind=Kind.LOCAL,
            handler=make_help_handler(reg),
        )
    )

    # /memory (Kind.LOCAL)
    reg.register(
        Command(
            name="memory",
            description="列出已加载的记忆文件名",
            kind=Kind.LOCAL,
            handler=handle_memory,
        )
    )

    # /permission (Kind.LOCAL)
    reg.register(
        Command(
            name="permission",
            description="显示当前权限模式",
            kind=Kind.LOCAL,
            handler=handle_permission,
        )
    )

    # /plan (Kind.UI)
    reg.register(
        Command(
            name="plan",
            description="切换到计划模式（仅只读工具）",
            kind=Kind.UI,
            handler=handle_plan,
        )
    )

    # /resume (Kind.UI)
    reg.register(
        Command(
            name="resume",
            description="恢复历史会话",
            kind=Kind.UI,
            handler=handle_resume,
        )
    )

    # /review (Kind.PROMPT)
    reg.register(
        Command(
            name="review",
            description="请求 AI 审查当前代码变更",
            kind=Kind.PROMPT,
            handler=handle_review,
        )
    )

    # /session (Kind.LOCAL)
    reg.register(
        Command(
            name="session",
            description="显示当前会话信息",
            kind=Kind.LOCAL,
            handler=handle_session,
        )
    )

    # /hooks (Kind.LOCAL) — ch12: Hook 列表
    from suisuicode.tui.hooks import handle_hooks

    reg.register(
        Command(
            name="hooks",
            description="列出已加载的 hook 列表",
            kind=Kind.LOCAL,
            handler=handle_hooks,
        )
    )

    # /skill (Kind.LOCAL) — ch11: Skill 管理
    reg.register(
        Command(
            name="skill",
            description="Skill 管理：list / info <name> / reload",
            kind=Kind.LOCAL,
            handler=handle_skill,
        )
    )

    # /worktree (Kind.LOCAL) — ch14: Worktree 管理
    from suisuicode.command.builtin_worktree import handle_worktree

    reg.register(
        Command(
            name="worktree",
            description="Worktree 管理：create / list / enter / exit / remove",
            kind=Kind.LOCAL,
            handler=handle_worktree,
        )
    )

    # /team (Kind.LOCAL) — ch15: Team 管理
    from suisuicode.command.builtin_team import _build_team_handler
    # team_mgr 通过闭包注入（由 cli.py 在注册时提供）
    # 此处注册占位 handler（实际 handler 在 cli 中通过 team_mgr 构造后覆盖）
    # 使用工厂模式：如果 team_mgr 可用则注册，否则注册占位

    # /status (Kind.LOCAL)
    reg.register(
        Command(
            name="status",
            description="显示当前状态信息",
            kind=Kind.LOCAL,
            handler=handle_status,
        )
    )
