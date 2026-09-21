from __future__ import annotations

import json
import pathlib

from suisuicode.tool import Result, Tool
from suisuicode.tool.ctx import resolve_path


class EditFileTool(Tool):
    def name(self) -> str:
        return "edit_file"

    def description(self) -> str:
        return "通过精确匹配原文来修改文件内容，匹配必须唯一，否则返回可区分的错误（含匹配数）。编辑前请先用 read_file 读取目标文件，确认 old_string 唯一。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径",
                },
                "old_string": {
                    "type": "string",
                    "description": "待替换的原文（必须唯一匹配）",
                },
                "new_string": {
                    "type": "string",
                    "description": "替换后的新内容",
                },
            },
            "required": ["path", "old_string", "new_string"],
        }

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, args: str) -> Result:
        if not args:
            args = "{}"
        try:
            data = json.loads(args)
        except json.JSONDecodeError as e:
            return Result(is_error=True, content=f"参数解析失败: {e}")

        path = data.get("path")
        old_string = data.get("old_string")
        new_string = data.get("new_string")
        if not path or old_string is None or new_string is None:
            return Result(is_error=True, content="缺少必填参数")

        abs_path = resolve_path(path)
        p = pathlib.Path(abs_path).expanduser()
        if not p.exists():
            return Result(is_error=True, content=f"文件不存在: {path}")

        try:
            content = p.read_text(encoding="utf-8")
        except Exception as e:
            return Result(is_error=True, content=f"读取失败: {e}")

        n = content.count(old_string)
        if n == 0:
            return Result(is_error=True, content="未找到匹配的内容")
        if n > 1:
            return Result(
                is_error=True,
                content=f"匹配到 {n} 处，old_string 不唯一，请提供更长上下文使其唯一",
            )

        new_content = content.replace(old_string, new_string, 1)
        try:
            p.write_text(new_content, encoding="utf-8")
            return Result(content=f"已更新文件 {path}")
        except Exception as e:
            return Result(is_error=True, content=f"写入失败: {e}")
