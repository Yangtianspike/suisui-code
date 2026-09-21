from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal, Protocol

if TYPE_CHECKING:
    from suisuicode.config import ProviderConfig

# ── 哨兵异常 ──────────────────────────────────


class PromptTooLongError(Exception):
    """Provider 上报上下文超出窗口时统一抛出的哨兵异常。"""


# ── 角色常量 ──────────────────────────────────
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"


# ── Token 用量（协议无关）─────────────────────
@dataclass
class Usage:
    """一轮请求的 token 用量（协议无关）。"""

    input_tokens: int = 0  # 本轮输入（含完整历史）token 数
    output_tokens: int = 0  # 本轮响应输出 token 数
    cache_write: int = 0  # Anthropic: cache_creation_input_tokens；OpenAI: 恒 0
    cache_read: int = 0  # Anthropic: cache_read_input_tokens；OpenAI: cached_tokens


# ── 协议无关工具类型 ──────────────────────────
@dataclass
class ToolCall:
    """协议无关地承载模型发起的一次工具调用（流式拼接完成后）。"""

    id: str  # provider 侧调用 id；回灌结果时配对
    name: str  # 工具名（注册中心按名查找）
    input: str  # 拼接完成的 JSON 参数字符串（raw JSON）


@dataclass
class ToolResult:
    """协议无关地承载一次工具执行结果。"""

    tool_call_id: str  # 对应 ToolCall.id
    content: str  # 执行产出（成功内容或结构化错误文本）
    is_error: bool = False  # True 表示错误结果


@dataclass
class ToolDefinition:
    """注册中心导出的协议无关工具定义。"""

    name: str
    description: str
    input_schema: dict[str, Any]


# ── Message / StreamEvent / System / Request ──
@dataclass
class Message:
    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)  # 仅 assistant
    tool_results: list[ToolResult] = field(default_factory=list)  # 仅 ROLE_TOOL


@dataclass
class System:
    """系统提示——稳定模块与环境信息分属两段。"""

    stable: str = ""  # 可缓存：装配好的稳定系统提示
    environment: str = ""  # 不缓存：环境信息段


@dataclass
class Request:
    """承载一次 LLM 请求的全部入参。"""

    messages: list[Message] = field(default_factory=list)  # 持久对话历史
    tools: list[ToolDefinition] = field(default_factory=list)  # 本轮工具集
    system: System = field(default_factory=System)  # 稳定系统提示 + 环境段
    reminder: str = ""  # 本轮 system-reminder（空=不注入）


@dataclass
class StreamEvent:
    """Provider 流式事件。text 为文本增量；tool_calls 为完整工具调用列表（流结束一次性收集）；
    usage 为本轮 token 用量（done 之前一次性发出）；done 表示流已正常结束；err 表示流出错。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage | None = None  # 非空：本轮 token 用量（done 之前一次性发出）
    done: bool = False
    err: Exception | None = None


class Provider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def stream(self, req: Request) -> AsyncIterator[StreamEvent]: ...


def new_provider(cfg: ProviderConfig) -> Provider:
    if cfg.protocol == "anthropic":
        from suisuicode.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider(cfg)
    elif cfg.protocol in ("openai", "deepseek"):
        from suisuicode.llm.openai_provider import OpenAIProvider

        return OpenAIProvider(cfg)
    else:
        raise ValueError(f"未知 protocol: {cfg.protocol}")
