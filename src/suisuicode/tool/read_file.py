from __future__ import annotations

import json
import pathlib

from suisuicode.tool import Result, _truncate, Tool
from suisuicode.tool.ctx import resolve_path


class ReadFileTool(Tool):
    def name(self) -> str:
        return "read_file"

    def description(self) -> str:
        return "读取指定文件内容（带行号），支持按行范围截取；超出 2000 行时截断并标注"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要读取的文件路径",
                },
                "start_line": {
                    "type": "integer",
                    "description": "起始行号（1-based，可选）",
                },
                "end_line": {
                    "type": "integer",
                    "description": "结束行号（可选）",
                },
            },
            "required": ["path"],
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

        path = data.get("path")
        if not path:
            return Result(is_error=True, content="缺少必填参数: path")

        abs_path = resolve_path(path)
        p = pathlib.Path(abs_path).expanduser()
        if not p.exists():
            return Result(is_error=True, content=f"文件不存在: {path}")
        if p.is_dir():
            return Result(is_error=True, content=f"是目录，非文件: {path}")

        try:
            text = p.read_text(encoding="utf-8")
        except PermissionError:
            return Result(is_error=True, content=f"无权限读取: {path}")
        except Exception as e:
            return Result(is_error=True, content=f"读取失败: {e}")

        lines = text.splitlines()
        total = len(lines)

        start_idx = max(0, (data.get("start_line") or 1) - 1)
        end_idx = min(data.get("end_line") or total, total)

        selected = lines[start_idx:end_idx]

        line_width = len(str(start_idx + len(selected)))
        numbered = [
            f"{i:>{line_width}}\t{line}"
            for i, line in enumerate(selected, start_idx + 1)
        ]
        result = "\n".join(numbered)

        result = _truncate(result, max_lines=2000, max_chars=256 * 1024)
        return Result(content=result)
