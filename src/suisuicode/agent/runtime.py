"""SessionRuntime：跨 Agent.run 调用持有的长生命周期状态容器。"""

from __future__ import annotations

from dataclasses import dataclass, field

from suisuicode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    SessionContext,
)
from suisuicode.skills.active import ActiveSkills


@dataclass
class SessionRuntime:
    """TUI Model 跨 run 持有的长生命周期状态。

    compact 是逻辑层，对状态零持有、可重入。
    asyncio 单线程事件循环保证串行访问，无需额外锁。
    """

    replacement: ContentReplacementState
    recovery: RecoveryState
    auto_tracking: CompactCircuitBreaker
    session: SessionContext
    context_window: int = 200000
    usage_anchor: int = 0  # 上一次主对话路径 stream 真实 usage 之和；摘要请求不更新
    anchor_msg_len: int = 0  # anchor 当时 Conversation.length()
    turn_count: int = 0  # ch09：Agent.run 完成轮数（不含工具调用中间轮）
    active_skills: ActiveSkills = field(default_factory=ActiveSkills)
    pending_reminders: list[str] = field(default_factory=list)  # ch12：hook prompt 注入队列
    hook_engine: object | None = None  # ch12：Hook Engine 引用（避免循环导入）

    def reset_for_new_session(self, ses_ctx: "SessionContext") -> None:
        """原子重置 compact 子状态与会话跟踪字段，指向新 SessionContext。"""
        from suisuicode.compact import (
            CompactCircuitBreaker,
            ContentReplacementState,
            RecoveryState,
        )

        self.replacement = ContentReplacementState()
        self.recovery = RecoveryState()
        self.auto_tracking = CompactCircuitBreaker()
        self.session = ses_ctx
        self.usage_anchor = 0
        self.anchor_msg_len = 0
        self.turn_count = 0
        self.active_skills.clear()
        self.pending_reminders = []

    def append_reminders(self, prompts: list[str]) -> None:
        """追加 hook 注入的 prompt 文本到待发送队列。"""
        self.pending_reminders.extend(prompts)

    def take_reminders(self) -> list[str]:
        """取出并清空待发送的 reminder 队列。"""
        taken = self.pending_reminders
        self.pending_reminders = []
        return taken


def new_session_runtime() -> SessionRuntime:
    """构造一个全新的 SessionRuntime（fork 子 Agent 用）。"""
    from suisuicode.compact import new_session_context

    return SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=new_session_context("."),
    )
