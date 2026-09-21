"""Hook 规则数据结构：Rule / Condition / Action / Payload。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from suisuicode.hook.event import Event
from suisuicode.permission.matcher import Matcher


# ── Payload ───────────────────────────────────────────────

# 事件分派时携带的上下文数据；条件求值与动作输入都用它。
# 序列化为 JSON 时保证 key 字典序: json.dumps(payload, sort_keys=True)
Payload = dict[str, Any]


# ── CombineMode ───────────────────────────────────────────


class CombineMode(str, enum.Enum):
    ALL_OF = "all_of"
    ANY_OF = "any_of"


# ── Condition / AtomCondition ────────────────────────────


@dataclass
class AtomCondition:
    """原子条件：payload 的某个字段路径是否匹配 matcher。"""

    field: str  # 形如 "tool_name" / "tool_input.path" / "tool_input.command"
    matcher: Matcher  # 复用 permission.Matcher


@dataclass
class Condition:
    """钩子触发条件。mode 为 ALL_OF 或 ANY_OF，atoms 为原子条件列表。

    ``Condition`` 为 None 表示无条件触发。
    """

    mode: CombineMode
    atoms: list[AtomCondition]


# ── Action types ─────────────────────────────────────────


class ActionType(str, enum.Enum):
    SHELL = "shell"
    PROMPT = "prompt"
    HTTP = "http"
    SUBAGENT = "subagent"


@dataclass
class ShellAction:
    command: str  # 由 sh -c 解释执行


@dataclass
class PromptAction:
    text: str  # 注入到 reminder 区的文本


@dataclass
class HttpAction:
    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None  # 模板字符串，None 表示用 payload JSON


@dataclass
class SubagentAction:
    agent_name: str
    prompt: str  # 模板字符串


@dataclass
class Action:
    """Hook 动作对象。"""

    type: ActionType
    shell: ShellAction | None = None
    prompt: PromptAction | None = None
    http: HttpAction | None = None
    subagent: SubagentAction | None = None


# ── Rule ─────────────────────────────────────────────────


@dataclass
class Rule:
    """单条 Hook 规则。

    对应 hooks.yaml 中的一条 hook 条目。
    """

    name: str  # 标识，用于日志、only_once 跟踪、冲突检测
    event: Event  # 11 个生命周期事件之一
    action: Action  # 动作对象
    condition: Condition | None = None  # None 表示无条件触发
    only_once: bool = False  # 同一会话内只跑一次
    asyncio_mode: bool = False  # 对应 YAML 的 async（避免与 Python 关键字冲突）
    reject: bool = False  # True → 该 hook 表达拦截（仅 blocking events），输出是拒绝原因
    timeout_s: float = 30.0  # 命令/HTTP 最大执行时长（秒）
    source: str = ""  # 来源文件路径，供 /hooks 显示
