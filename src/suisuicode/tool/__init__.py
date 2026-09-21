from __future__ import annotations

import asyncio
from dataclasses import dataclass

from suisuicode.llm import ToolDefinition

DEFAULT_TIMEOUT: float = 30.0


@dataclass
class Result:
    """工具执行结果——永远以值类型返回，从不抛 Python 异常给上层。"""

    content: str
    is_error: bool = False


def _truncate(s: str, max_lines: int, max_chars: int) -> str:
    """截断长输出，尾部追加 [truncated] 标注。"""
    if len(s) <= max_chars:
        lines = s.splitlines()
        if len(lines) <= max_lines:
            return s
        return "\n".join(lines[:max_lines]) + "\n[truncated]"
    # 按字符截断
    truncated = s[:max_chars]
    lines = truncated.splitlines()
    if len(lines) > max_lines:
        truncated = "\n".join(lines[:max_lines])
    return truncated + "\n[truncated]"


class Tool:
    """统一工具抽象（Protocol）。"""

    is_system_tool: bool = False  # 系统工具：fork 模式自动透传且不受权限拦截

    def name(self) -> str: ...
    def description(self) -> str: ...
    def parameters(self) -> dict: ...
    @property
    def read_only(self) -> bool: ...  # True=只读（可并发执行 & Plan Mode 放行）
    async def execute(self, args: str) -> Result: ...


class Registry:
    """集中登记、按名查找、导出定义、按名执行。"""

    def __init__(self) -> None:
        self._order: list[str] = []
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> None:
        name = t.name()
        if name in self._tools:
            raise ValueError(f"工具已注册: {name}")
        self._order.append(name)
        self._tools[name] = t

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def definitions(self) -> list[ToolDefinition]:
        return [
            ToolDefinition(
                name=name,
                description=self._tools[name].description(),
                input_schema=self._tools[name].parameters(),
            )
            for name in self._order
        ]

    def read_only_definitions(self) -> list[ToolDefinition]:
        """Plan Mode：只导出 read_only==True 的工具定义，保留注册顺序。"""
        return [
            ToolDefinition(
                name=name,
                description=self._tools[name].description(),
                input_schema=self._tools[name].parameters(),
            )
            for name in self._order
            if self._tools[name].read_only
        ]

    def count(self) -> int:
        """返回当前已注册工具数量（O(1)）。"""
        return len(self._tools)

    def system_definitions(self) -> list[ToolDefinition]:
        """仅返回系统工具定义（is_system_tool == True）。"""
        return [
            ToolDefinition(
                name=name,
                description=self._tools[name].description(),
                input_schema=self._tools[name].parameters(),
            )
            for name in self._order
            if getattr(self._tools[name], "is_system_tool", False)
        ]

    def definitions_filtered(self, allowed: list[str]) -> list[ToolDefinition]:
        """按白名单 + 系统工具豁免导出工具定义。"""
        return [
            ToolDefinition(
                name=name,
                description=self._tools[name].description(),
                input_schema=self._tools[name].parameters(),
            )
            for name in self._order
            if name in allowed or getattr(self._tools[name], "is_system_tool", False)
        ]

    def remove_all(self, predicate) -> int:
        """按 predicate(name) 移除工具，返回移除数量。"""
        removed = 0
        for name in list(self._order):
            if predicate(name):
                del self._tools[name]
                self._order.remove(name)
                removed += 1
        return removed

    def __iter__(self):
        return iter(self._order)

    def is_read_only(self, name: str) -> bool:
        """分批判定；未知工具返回 False（按串行处理）。"""
        t = self.get(name)
        return t is not None and t.read_only

    async def execute(
        self, name: str, args: str, timeout: float = DEFAULT_TIMEOUT
    ) -> Result:
        tool = self.get(name)
        if tool is None:
            return Result(is_error=True, content=f"未知工具: {name}")
        try:
            return await asyncio.wait_for(tool.execute(args), timeout=timeout)
        except asyncio.TimeoutError:
            return Result(is_error=True, content=f"工具 {name} 执行超时（{timeout}s）")
        except Exception as e:
            return Result(is_error=True, content=f"工具 {name} 异常: {e}")


def new_default_registry() -> Registry:
    """构造并注册 6 个工具。"""
    from suisuicode.tool.read_file import ReadFileTool
    from suisuicode.tool.write_file import WriteFileTool
    from suisuicode.tool.edit_file import EditFileTool
    from suisuicode.tool.bash import BashTool
    from suisuicode.tool.glob_tool import GlobTool
    from suisuicode.tool.grep_tool import GrepTool

    reg = Registry()
    reg.register(ReadFileTool())
    reg.register(WriteFileTool())
    reg.register(EditFileTool())
    reg.register(BashTool())
    reg.register(GlobTool())
    reg.register(GrepTool())
    return reg
