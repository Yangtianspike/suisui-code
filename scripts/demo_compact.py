"""演示 ch08 上下文管理 TUI 反馈 —— 不需要真实 API key。

模拟场景：
  1. layer1 spill —— 工具返回超大结果，被落盘
  2. layer2 auto compact —— 长对话触发自动摘要
  3. 紧急压缩 —— provider 返回 PromptTooLongError

运行: python scripts/demo_compact.py
"""

import asyncio
import sys
from collections.abc import AsyncIterator
from pathlib import Path

# 确保 suisuicode 可 import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from suisuicode.agent import Agent, Event, Phase, SessionRuntime
from suisuicode.agent.event import CompactPhase
from suisuicode.compact import (
    CompactCircuitBreaker,
    ContentReplacementState,
    RecoveryState,
    new_session_context,
)
from suisuicode.conversation import Conversation
from suisuicode.llm import (
    PromptTooLongError,
    Request,
    StreamEvent,
    ToolCall,
    Usage,
)
from suisuicode.permission import Mode
from suisuicode.permission.engine import new_engine
from suisuicode.tool import Registry, Result, Tool


# ── Fake Provider（脚本驱动）───────────────────────────

class ScriptProvider:
    def __init__(self, scripts: list[list[StreamEvent]]):
        self._scripts = scripts
        self._call = 0
        self.summarize_calls = 0

    @property
    def name(self) -> str:
        return "demo-provider"

    @property
    def model(self) -> str:
        return "demo-model"

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        idx = min(self._call, len(self._scripts) - 1)
        self._call += 1
        if req.tools is None:
            self.summarize_calls += 1
        for ev in self._scripts[idx]:
            yield ev


# ── 渲染辅助 ──────────────────────────────────────────

def render_event(ev: Event) -> str | None:
    """把 Event 转成 TUI 会显示的文本。"""
    if ev.compact is not None:
        from suisuicode.tui.commands import format_compact_notice
        return f"[COMPACT] {format_compact_notice(ev.compact)}"
    if ev.notice:
        return f"[NOTICE] {ev.notice}"
    if ev.text:
        return f"[TEXT] {ev.text[:80]}{'...' if len(ev.text) > 80 else ''}"
    if ev.tool is not None:
        if ev.tool.phase == Phase.START:
            return f"[TOOL] ▶ {ev.tool.name}({ev.tool.args})"
        else:
            preview = ev.tool.result[:60] if ev.tool.result else ""
            return f"[TOOL] ◀ {ev.tool.name} → {preview}..."
    if ev.err is not None:
        return f"[ERROR] {ev.err}"
    if ev.done:
        return "[DONE] ✓"
    if ev.iter > 0:
        return f"─── iteration {ev.iter} ───"
    return None


# ── 场景 1：layer1 spill ──────────────────────────────

async def demo_spill():
    print("\n" + "=" * 60)
    print("场景 1: Layer 1 — 超大工具结果触发 spill")
    print("=" * 60)

    conv = Conversation()
    conv.add_user("read a big file")

    # 让 LLM 请求 read_file（用相对路径避免沙箱拦截）
    provider = ScriptProvider([
        [
            StreamEvent(tool_calls=[
                ToolCall(id="tc-1", name="read_file", input='{"path":"README.md"}'),
            ]),
            StreamEvent(done=True),
        ],
        # 第二轮：简单回复
        [
            StreamEvent(text="I've read the file. It was spilled to disk because it was too large."),
            StreamEvent(usage=Usage(input_tokens=50, output_tokens=20)),
            StreamEvent(done=True),
        ],
    ])

    # 注册一个返回超大结果的 read_file 工具
    class BigReadTool(Tool):
        def name(self) -> str: return "read_file"
        def description(self) -> str: return "Read a file"
        def parameters(self) -> dict: return {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
        @property
        def read_only(self) -> bool: return True
        async def execute(self, args_str: str) -> Result:
            import json
            args = json.loads(args_str)
            # 返回 80KB 内容，远超过 SINGLE_RESULT_LIMIT(50000)
            return Result(content="x" * 80000, is_error=False)

    engine, _ = new_engine(".")
    reg = Registry()
    reg.register(BigReadTool())

    tmp = Path("demo_sessions")
    tmp.mkdir(exist_ok=True)
    session = new_session_context(str(tmp))
    runtime = SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=session,
        context_window=200000,
    )
    agent = Agent(provider, reg, engine, "demo", runtime=runtime)

    print("\n事件流：")
    async for ev in agent.run(conv, mode=Mode.DEFAULT):
        line = render_event(ev)
        if line:
            print(f"  {line}")

    # 检查落盘文件
    spill_files = list((tmp / session.session_id / "tool-results").glob("*"))
    print(f"\n落盘文件: {len(spill_files)} 个")
    for f in spill_files:
        print(f"  {f.name}: {f.stat().st_size} bytes")


# ── 场景 2：layer2 auto compact ────────────────────────

async def demo_auto_compact():
    print("\n" + "=" * 60)
    print("场景 2: Layer 2 — 长对话触发自动摘要")
    print("=" * 60)

    conv = Conversation()
    # 用 70K 窗口 + 130K 字符的内容强制触发自动 compact
    # threshold = 70000 - 20000 - 13000 = 37000
    # 130000 字符 / 3.5 ≈ 37143 tokens > 37000
    big = "y" * 130000
    conv.add_user(f"help me with: {big}")
    conv.add_assistant("let me think...")

    summary = (
        "<analysis>draft</analysis>\n"
        "<summary>## 1 主要请求和意图\nhelp request\n"
        "## 2 关键技术概念\nnone\n## 3 文件和代码段\nnone\n"
        "## 4 错误和修复\nnone\n## 5 问题解决过程\nnone\n"
        "## 6 所有用户消息原文\nhelp me\n"
        "## 7 待办任务\nnone\n## 8 当前工作\nanalyzing\n"
        "## 9 可能的下一步\nrespond</summary>"
    )

    provider = ScriptProvider([
        # 摘要请求
        [
            StreamEvent(text=summary),
            StreamEvent(usage=Usage(input_tokens=100, output_tokens=50)),
            StreamEvent(done=True),
        ],
        # 压缩后的主请求
        [
            StreamEvent(text="Based on the summary, I can help you with that."),
            StreamEvent(usage=Usage(input_tokens=30, output_tokens=15)),
            StreamEvent(done=True),
        ],
    ])

    engine, _ = new_engine(".")
    reg = Registry()

    tmp = Path("demo_sessions")
    session = new_session_context(str(tmp))
    runtime = SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=session,
        context_window=70000,  # 低窗口确保触发
    )
    agent = Agent(provider, reg, engine, "demo", runtime=runtime)

    print("\n事件流：")
    async for ev in agent.run(conv, mode=Mode.DEFAULT):
        line = render_event(ev)
        if line:
            print(f"  {line}")


# ── 场景 3：紧急压缩 ──────────────────────────────────

async def demo_emergency():
    print("\n" + "=" * 60)
    print("场景 3: 紧急压缩 — provider 返回 PromptTooLongError")
    print("=" * 60)

    conv = Conversation()
    conv.add_user("hello")
    conv.add_assistant("hi there")

    summary = (
        "<analysis>draft</analysis>\n"
        "<summary>## 1 主要请求和意图\ngreeting\n"
        "## 2 关键技术概念\nnone\n## 3 文件和代码段\nnone\n"
        "## 4 错误和修复\nnone\n## 5 问题解决过程\nnone\n"
        "## 6 所有用户消息原文\nhello\n"
        "## 7 待办任务\nnone\n## 8 当前工作\nchatting\n"
        "## 9 可能的下一步\nreply</summary>"
    )

    provider = ScriptProvider([
        # 第 1 次主请求 → PTL 撞墙
        [StreamEvent(err=PromptTooLongError("prompt too long"))],
        # 摘要请求（紧急路径）
        [
            StreamEvent(text=summary),
            StreamEvent(usage=Usage(input_tokens=10, output_tokens=5)),
            StreamEvent(done=True),
        ],
        # 重试主请求 → 成功
        [
            StreamEvent(text="Hello! How can I help you today?"),
            StreamEvent(usage=Usage(input_tokens=20, output_tokens=10)),
            StreamEvent(done=True),
        ],
    ])

    engine, _ = new_engine(".")
    reg = Registry()

    tmp = Path("demo_sessions")
    session = new_session_context(str(tmp))
    runtime = SessionRuntime(
        replacement=ContentReplacementState(),
        recovery=RecoveryState(),
        auto_tracking=CompactCircuitBreaker(),
        session=session,
        context_window=200000,
    )
    agent = Agent(provider, reg, engine, "demo", runtime=runtime)

    print("\n事件流：")
    async for ev in agent.run(conv, mode=Mode.DEFAULT):
        line = render_event(ev)
        if line:
            print(f"  {line}")


# ── main ──────────────────────────────────────────────

async def main():
    print("ch08 上下文管理 TUI 反馈演示")
    print("(所有场景使用 fake provider，无需 API key)\n")

    await demo_spill()
    await demo_auto_compact()
    await demo_emergency()

    print("\n" + "=" * 60)
    print("演示完成。以上输出模拟了 TUI RichLog 中实际会显示的内容。")


if __name__ == "__main__":
    asyncio.run(main())
