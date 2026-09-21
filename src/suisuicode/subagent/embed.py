"""通过 importlib.resources 读取内置 Agent 定义文件。"""

from __future__ import annotations

from importlib.resources import files

from suisuicode.subagent.definition import Definition, Source
from suisuicode.subagent.parser import parse_definition


def builtin_definitions() -> list[Definition]:
    """读取随包发布的内置 Agent 定义，按 name 升序返回。

    内置文件位于 ``suisuicode/subagent/builtin/*.md``。
    解析失败直接 raise（代码 bug，启动期失败即灾难）。
    """
    pkg = files("suisuicode.subagent.builtin")
    defs: list[Definition] = []
    for entry in sorted(pkg.iterdir(), key=lambda e: e.name):
        if not entry.name.endswith(".md"):
            continue
        data = (pkg / entry.name).read_bytes()
        d = parse_definition(data, f"builtin:{entry.name}", Source.BUILTIN)
        defs.append(d)
    return defs
