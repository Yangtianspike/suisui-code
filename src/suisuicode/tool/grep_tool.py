from __future__ import annotations

import asyncio
import json
import pathlib
import re

from suisuicode.tool import Result, Tool
from suisuicode.tool.ctx import resolve_path

_MAX_GREP_HITS = 100


class GrepTool(Tool):
    def name(self) -> str:
        return "grep"

    def description(self) -> str:
        return "按正则表达式搜索文件内容，返回 file:line:content（最多 100 条）"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Python 正则表达式",
                },
                "path": {
                    "type": "string",
                    "description": "搜索根路径（默认当前目录）",
                },
                "glob": {
                    "type": "string",
                    "description": "文件过滤 glob，如 *.py（可选）",
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
        glob_filter = data.get("glob")

        if not pattern:
            return Result(is_error=True, content="缺少必填参数: pattern")

        try:
            rx = re.compile(pattern)
        except re.error as e:
            return Result(is_error=True, content=f"正则非法: {e}")

        abs_root = resolve_path(path_str)
        root = pathlib.Path(abs_root).expanduser().resolve()
        if not root.is_dir():
            return Result(is_error=True, content=f"路径不存在或非目录: {path_str}")

        if glob_filter:
            walker = root.rglob(glob_filter)
        else:
            walker = root.rglob("*")

        hits: list[str] = []
        truncated = False
        file_count = 0

        try:
            for fp in walker:
                if not fp.is_file():
                    continue
                file_count += 1
                if file_count % 50 == 0:
                    await asyncio.sleep(0)

                try:
                    with open(fp, encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if len(line) > 1024 * 1024:
                                hits.append(
                                    f"{fp.resolve()}:{lineno}:<该行过长，未完整搜索>"
                                )
                                continue
                            if rx.search(line):
                                hits.append(f"{fp.resolve()}:{lineno}:{line.rstrip()}")
                                if len(hits) >= _MAX_GREP_HITS:
                                    truncated = True
                                    break
                    if truncated:
                        break
                except (PermissionError, OSError, UnicodeDecodeError):
                    continue

        except Exception as e:
            return Result(is_error=True, content=f"搜索异常: {e}")

        if not hits:
            return Result(content="无命中")

        output = "\n".join(hits)
        if truncated:
            output += f"\n...（结果超过 {_MAX_GREP_HITS} 条，已截断）"

        return Result(content=output)
