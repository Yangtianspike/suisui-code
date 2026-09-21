from __future__ import annotations

import asyncio
import json
import pathlib

from suisuicode.tool import Result, Tool
from suisuicode.tool.ctx import resolve_path


class GlobTool(Tool):
    def name(self) -> str:
        return "glob"

    def description(self) -> str:
        return "按 glob 模式搜索文件路径（如 **/*.py），最多返回 100 条"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "glob 模式，如 **/*.py",
                },
                "path": {
                    "type": "string",
                    "description": "搜索根路径（默认当前目录）",
                },
            },
            "required": ["pattern"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, args: str) -> Result:
        if not args:
            args = "{}"
        try:
            data = json.loads(args)
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")

        pattern = data.get("pattern")
        path_str = data.get("path", ".")
        if not pattern:
            return Result(is_error=True, content="缺少必填参数: pattern")

        abs_root = resolve_path(path_str)
        root = pathlib.Path(abs_root).expanduser().resolve()
        if not root.is_dir():
            return Result(is_error=True, content=f"路径不存在或非目录: {path_str}")

        try:
            matches = sorted(root.glob(pattern))
            count = 0
            lines: list[str] = []
            for m in matches:
                if m.is_file():
                    lines.append(str(m.resolve()))
                    count += 1
                    if count >= 100:
                        break
                if count % 100 == 0:
                    await asyncio.sleep(0)

            if not lines:
                return Result(content="无匹配")

            return Result(content="\n".join(lines))
        except Exception as e:
            return Result(is_error=True, content=f"搜索失败: {e}")
