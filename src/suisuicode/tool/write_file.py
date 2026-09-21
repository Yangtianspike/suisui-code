from __future__ import annotations

import json
import pathlib

from suisuicode.tool import Result, Tool
from suisuicode.tool.ctx import resolve_path


class WriteFileTool(Tool):
    def name(self) -> str:
        return "write_file"

    def description(self) -> str:
        return "创建或覆盖写入文件，自动创建中间目录。写文件、保存内容请优先使用本工具，不要用 bash 命令拼凑。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "文件路径",
                },
                "content": {
                    "type": "string",
                    "description": "写入内容",
                },
            },
            "required": ["path", "content"],
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
        content = data.get("content")
        if not path or content is None:
            return Result(is_error=True, content="缺少必填参数: path 和 content")

        abs_path = resolve_path(path)
        p = pathlib.Path(abs_path).expanduser()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return Result(content=f"已写入 {path}（{len(content.encode())} 字节）")
        except OSError as e:
            return Result(is_error=True, content=f"写入失败: {e}")
