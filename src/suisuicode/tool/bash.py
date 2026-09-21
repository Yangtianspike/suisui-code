from __future__ import annotations

import asyncio
import json
import sys

from suisuicode.tool import Result, _truncate, Tool
from suisuicode.tool.ctx import resolve_path


class BashTool(Tool):
    def name(self) -> str:
        return "bash"

    def description(self) -> str:
        return "在终端执行 shell 命令，返回 stdout/stderr 和退出码。运行于 Windows PowerShell，使用 PowerShell 语法。读文件、找文件、搜内容请优先用 read_file/glob/grep，写文件请用 write_file，编辑文件请用 edit_file，不要用 bash 拼凑文件操作。"

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的 PowerShell 命令（运行于 Windows，使用 PowerShell 语法，如 ls, cat, echo, Get-ChildItem, Select-String 等）",
                },
            },
            "required": ["command"],
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

        cmd = data.get("command")
        if not cmd:
            return Result(is_error=True, content="缺少必填参数: command")

        cwd = resolve_path("")

        try:
            if sys.platform == "win32":
                proc = await asyncio.create_subprocess_exec(
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                )
            stdout_b, stderr_b = await proc.communicate()
            stdout = stdout_b.decode("utf-8", errors="replace")
            stderr = stderr_b.decode("utf-8", errors="replace")
            code = proc.returncode or 0

            output = f"exit_code: {code}\nstdout:\n{stdout}"
            if stderr:
                output += f"\nstderr:\n{stderr}"

            output = _truncate(output, max_lines=10000, max_chars=30000)
            return Result(content=output)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            return Result(is_error=True, content=f"执行异常: {e}")
