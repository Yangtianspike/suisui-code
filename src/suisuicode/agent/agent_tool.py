"""Agent 工具：主 Agent 通过此工具启动子 Agent（spec F1-F3）。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from suisuicode.agent.agent import Agent
from suisuicode.agent.fork import build_forked_messages, is_fork_context
from suisuicode.conversation import Conversation
from suisuicode.llm import Provider
from suisuicode.tool import Result, Tool
from suisuicode.tool.filter import FilterParams, apply_agent_tool_filter

if TYPE_CHECKING:
    from suisuicode.subagent import Definition as AgentDefinition
    from suisuicode.task.manager import PartialState

# 前台默认超时秒数（过期自动切后台）
AUTO_BACKGROUND_SECONDS: float = 120.0

# 支持切换的模型名 → provider 前缀
_VALID_MODELS = {"haiku", "sonnet", "opus", "inherit"}


# ── Protocol（解耦，避免循环依赖）────────────────────────


class AgentCatalog(Protocol):
    def resolve(self, name: str) -> "AgentDefinition | None": ...
    def fork_definition(self) -> "AgentDefinition": ...
    def list(self) -> list: ...


class TaskManagerProto(Protocol):
    async def launch(self, ag: Agent, conv: Conversation,
                     name: str, task: str) -> str: ...
    async def adopt_running(self, ag: Agent, conv: Conversation,
                            name: str, events: asyncio.Queue,
                            handle: asyncio.Task, partial: "PartialState") -> str: ...
    async def upgrade_approval(self, req) -> tuple: ...


# ── AgentArgs ─────────────────────────────────────────────


@dataclass
class AgentArgs:
    prompt: str
    description: str
    subagent_type: str = ""
    model: str = ""
    run_in_background: bool = False
    name: str = ""
    team_name: str = ""  # ch15: 非空时走 Team spawn 分支
    plan_mode_required: bool = False  # ch15: 队员是否从 plan 模式起步


# ── AgentTool ────────────────────────────────────────────


class AgentTool(Tool):
    """注册到 tool.Registry 的统一 Agent 工具。

    主 Agent 可通过此工具启动定义式或 Fork 式子 Agent。
    """

    is_system_tool = True

    def __init__(
        self,
        catalog: AgentCatalog,
        task_mgr: TaskManagerProto,
        parent: Agent | None = None,
        bg_enabled: bool = True,
        worktree_mgr=None,  # ch14: worktree.Manager | None
        team_hook=None,  # ch15: TeamHook | None
    ) -> None:
        self._catalog = catalog
        self._task_mgr = task_mgr
        self._parent = parent
        self._bg_enabled = bg_enabled
        self._worktree_mgr = worktree_mgr
        self._team_hook = team_hook  # ch15
        # 父对话消息引用（Fork 消息克隆用），由 TUI 在每轮开始前更新
        self._parent_conv: Conversation | None = None

    def name(self) -> str:
        return "Agent"

    def description(self) -> str:
        base = (
            "启动一个子 Agent 执行任务。"
            "指定 subagent_type 选择预定义角色，留空走 Fork 路径（克隆当前对话）。"
        )
        try:
            names = [d.name for d in self._catalog.list()]
            if names:
                base += " subagent_type 可选值: " + ", ".join(names)
        except Exception:
            pass
        return base

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "交给子 Agent 的任务指令",
                },
                "description": {
                    "type": "string",
                    "description": "一句话描述任务，供 UI 展示",
                },
                "subagent_type": {
                    "type": "string",
                    "description": "预定义角色名（如 Explore / Plan），留空走 Fork 路径",
                },
                "model": {
                    "type": "string",
                    "description": "模型覆盖：haiku / sonnet / opus / inherit",
                },
                "run_in_background": {
                    "type": "boolean",
                    "description": "true 时强制后台启动",
                },
                "name": {
                    "type": "string",
                    "description": "给子 Agent 命名，供 SendMessage 用",
                },
                "team_name": {
                    "type": "string",
                    "description": "可选，非空时走 Team spawn 分支，队员加入指定团队",
                },
                "plan_mode_required": {
                    "type": "boolean",
                    "description": "可选，true 时队员以 plan 模式起步，需 Lead 审批",
                },
            },
            "required": ["prompt", "description"],
        }

    @property
    def read_only(self) -> bool:
        return False  # 子 Agent 可能做任何事

    def set_parent(self, ag: Agent) -> None:
        """cli 在 SuisuiCodeApp 构造后回填 parent 引用。"""
        self._parent = ag

    def set_parent_conv(self, conv: Conversation) -> None:
        """TUI 在每轮开始前更新父对话引用（供 Fork 消息克隆）。"""
        self._parent_conv = conv

    async def execute(self, args: str) -> Result:
        # 解析参数
        try:
            data = json.loads(args) if isinstance(args, str) else args
        except (json.JSONDecodeError, TypeError):
            return Result(is_error=True, content="invalid args: expected JSON")
        if not isinstance(data, dict):
            return Result(is_error=True, content="invalid args: expected JSON object")

        a = AgentArgs(
            prompt=str(data.get("prompt", "")),
            description=str(data.get("description", "")),
            subagent_type=str(data.get("subagent_type", "")),
            model=str(data.get("model", "")),
            run_in_background=bool(data.get("run_in_background", False)),
            name=str(data.get("name", "")),
            team_name=str(data.get("team_name", "")),
            plan_mode_required=bool(data.get("plan_mode_required", False)),
        )

        if not a.prompt:
            return Result(is_error=True, content="prompt is required")
        if not a.description:
            return Result(is_error=True, content="description is required")

        # ── ch15: Team spawn 分支 ──
        if a.team_name:
            if self._team_hook is None:
                return Result(
                    is_error=True,
                    content="Team Hook 未配置，无法走 Team spawn",
                )
            try:
                from suisuicode.agent.team_hook import TeamSpawnRequest

                result_text = await self._team_hook.spawn_teammate(
                    TeamSpawnRequest(
                        team_name=a.team_name,
                        member_name=a.name,
                        prompt=a.prompt,
                        description=a.description,
                        subagent_type=a.subagent_type,
                        model=a.model,
                        plan_mode_required=a.plan_mode_required,
                    )
                )
                return Result(content=result_text)
            except Exception as e:
                return Result(is_error=True, content=f"Team spawn 失败: {e}")

        if self._parent is None:
            return Result(is_error=True, content="Agent tool: parent agent not set")

        # ── Step 2: 防嵌套（spec F24/F25）───
        # 检查 1: 父 Agent 自身是否为子 Agent（dont_ask 标记）
        if self._parent.dont_ask:
            return Result(
                is_error=True,
                content="子 Agent 不能再启动 Agent（检测到嵌套调用）",
            )

        # 检查 2: Fork boilerplate 兜底检测
        if self._parent_conv is not None:
            if is_fork_context(self._parent_conv.messages()):
                return Result(
                    is_error=True,
                    content="Fork 子 Agent 不能再启动 Agent（检测到 fork boilerplate）",
                )

        # ── Step 3: resolve 定义 ──
        if a.subagent_type:
            defi = self._catalog.resolve(a.subagent_type)
            if defi is None:
                return Result(
                    is_error=True,
                    content=f"unknown subagent_type: {a.subagent_type}",
                )
        else:
            defi = self._catalog.fork_definition()

        # ── Step 4: 决定后台 ──
        is_fork = defi.name == "__fork__"
        background = defi.background or a.run_in_background or is_fork
        if background and not self._bg_enabled:
            if is_fork:
                return Result(
                    is_error=True,
                    content="background mode is disabled by config; cannot Fork",
                )
            return Result(
                is_error=True,
                content="background mode is disabled by config",
            )

        # ── Step 5: 工具过滤（spec F30）───
        try:
            all_names = [d.name for d in self._parent._registry.definitions()]
        except Exception:
            all_names = []

        allowed = apply_agent_tool_filter(FilterParams(
            all=all_names,
            source=int(defi.source),
            background=background,
            allowed=list(defi.tools),
            disallowed=list(defi.disallowed_tools),
        ))

        # ── Step 6: 选 provider（model 切换）───
        sub_provider: Provider = self._parent._provider
        effective_model = a.model or defi.model
        if effective_model and effective_model != "inherit":
            sub_provider = _switch_model(self._parent._provider, effective_model)

        # ── Step 7: 构造子 Agent ──
        from suisuicode.agent.runtime import new_session_runtime

        sub_runtime = new_session_runtime()
        sub_runtime.context_window = getattr(
            self._parent.runtime, "context_window", 200000
        )

        sub_agent = Agent(
            provider=sub_provider,
            registry=self._parent._registry,
            engine=self._parent._engine,
            version=getattr(self._parent, "_version", ""),
            runtime=sub_runtime,
            allowed_tools=allowed,
            system_prompt=defi.system_prompt if defi.system_prompt else None,
            max_turns=defi.max_turns if defi.max_turns > 0 else 0,
            permission_mode=defi.permission_mode if not defi.dont_ask else None,
            dont_ask=defi.dont_ask,
            approval_upgrader=self._task_mgr.upgrade_approval,
            hook_engine=self._parent._hook_engine,
        )

        # ── Step 7b: 子 conv ──
        sub_conv: Conversation
        if is_fork and self._parent_conv is not None:
            # Fork 路径：克隆父对话 + Boilerplate + task
            parent_msgs = self._parent_conv.messages()
            forked = build_forked_messages(parent_msgs, a.prompt)
            sub_conv = Conversation.from_messages(forked)
        else:
            sub_conv = Conversation()

        # ── Step 7c: isolation:worktree 分支 ──
        if defi.isolation == "worktree":
            if self._worktree_mgr is None:
                return Result(
                    is_error=True,
                    content="worktree manager not configured",
                )
            # isolation:worktree 强制前台同步
            from suisuicode.agent.agent_worktree import _execute_with_worktree

            events: asyncio.Queue = asyncio.Queue(maxsize=32)
            try:
                final_text = await _execute_with_worktree(
                    self._worktree_mgr, defi, sub_agent, sub_conv, a.prompt, events,
                )
            except Exception as e:
                return Result(is_error=True, content=f"worktree subagent error: {e}")
            return Result(content=final_text)

        # ── Step 9: 后台路径 ──
        if background:
            task_id = await self._task_mgr.launch(
                sub_agent, sub_conv, a.name, a.prompt,
            )
            return Result(content=json.dumps({
                "task_id": task_id,
                "status": "async_launched",
            }))

        # ── Step 8: 前台路径 ──
        events: asyncio.Queue = asyncio.Queue(maxsize=32)
        from suisuicode.task.manager import PartialState

        partial = PartialState()

        async def aggregate_partial() -> None:
            while True:
                ev = await events.get()
                if ev is None:
                    break
                if hasattr(ev, "tool") and ev.tool is not None:
                    partial.tool_count += 1
                    partial.last_activity = ev.tool.name
                if hasattr(ev, "usage") and ev.usage is not None:
                    partial.usage.input += getattr(ev.usage, "input", 0)
                    partial.usage.output += getattr(ev.usage, "output", 0)

        agg_task = asyncio.create_task(aggregate_partial())

        try:
            final_text = await asyncio.wait_for(
                sub_agent.run_to_completion(sub_conv, a.prompt, events),
                timeout=AUTO_BACKGROUND_SECONDS,
            )
            return Result(content=final_text)
        except asyncio.TimeoutError:
            # 超时 → 切后台（spec F17）
            running = asyncio.create_task(
                sub_agent.run_to_completion(sub_conv, "", events)
            )
            task_id = await self._task_mgr.adopt_running(
                sub_agent, sub_conv, a.name, events, running, partial,
            )
            return Result(content=json.dumps({
                "task_id": task_id,
                "status": "timed_out_to_background",
            }))
        except asyncio.CancelledError:
            return Result(is_error=True, content="subagent cancelled")
        except Exception as e:
            return Result(is_error=True, content=f"subagent error: {e}")
        finally:
            agg_task.cancel()
            try:
                await events.put(None)
            except Exception:
                pass


# ── Provider 切换辅助 ─────────────────────────────────────


def _switch_model(base_provider: Provider, model: str) -> Provider:
    """根据 model 名返回切换后的 Provider。

    本期保守实现：如果 base_provider 支持 clone_with_model，
    则调用之；否则直接返回 base_provider（不切换）。
    """
    if hasattr(base_provider, "clone_with_model"):
        return base_provider.clone_with_model(model)  # type: ignore[union-attr]
    # 兜底：返回原 provider（子 Agent 用同一模型）
    return base_provider
