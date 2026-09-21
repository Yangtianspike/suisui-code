"""Catalog：Agent 角色定义的多来源加载与查询。

三层加载顺序：builtin → user → project（plugin 占位恒空）。
同名定义高优先级覆盖，加载失败打 stderr 跳过。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from suisuicode.permission import Mode as PermissionMode
from suisuicode.subagent.definition import Definition, Source
from suisuicode.subagent.embed import builtin_definitions
from suisuicode.subagent.parser import AgentParseError, parse_file


class Catalog:
    """Agent 角色定义编目，支持按 name 查询、列表、按来源筛选。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._defs: dict[str, Definition] = {}  # name -> 最高优先级定义
        self._by_source: dict[Source, list[Definition]] = {
            Source.BUILTIN: [],
            Source.USER: [],
            Source.PROJECT: [],
            Source.PLUGIN: [],
        }

    # ── 查询 ──────────────────────────────────────────

    def resolve(self, name: str) -> Definition | None:
        """按 name 查找定义（大小写敏感，与文件 name 字段一致）。"""
        with self._lock:
            return self._defs.get(name)

    def list(self) -> list[Definition]:
        """返回所有定义，按 name 升序。"""
        with self._lock:
            return sorted(self._defs.values(), key=lambda d: d.name)

    def list_by_source(self, src: Source) -> list[Definition]:
        """返回指定来源的全部定义（含被覆盖的）。"""
        with self._lock:
            return list(self._by_source.get(src, []))

    def fork_definition(self) -> Definition:
        """返回 Fork 路径用的临时 Definition。

        name="__fork__"，tools/disallowed_tools 留空（工具集继承父），
        max_turns=25，permission_mode=DEFAULT。
        """
        return Definition(
            name="__fork__",
            description="Fork-based subagent",
            model="inherit",
            max_turns=25,
            permission_mode=PermissionMode.DEFAULT,
            source=Source.BUILTIN,
        )

    # ── 内部加载 ──────────────────────────────────────

    def _add_all(self, defs: list[Definition], source: Source) -> None:
        """注册一批定义；同名时后来的覆盖先前的（优先级更高）。"""
        with self._lock:
            for d in defs:
                self._defs[d.name] = d
            self._by_source[source].extend(defs)


# ── 公开加载函数 ──────────────────────────────────────────


def load_catalog(root: str) -> Catalog:
    """加载全部 Agent 定义。

    顺序：builtin → user → project。同名高优先级覆盖。
    加载错误的单文件打 stderr 警告并跳过，不阻断整体加载。
    """
    c = Catalog()

    # 1. 内置
    try:
        c._add_all(builtin_definitions(), Source.BUILTIN)
    except Exception as e:
        print(f"subagent: builtin definitions load failed: {e}", file=sys.stderr)
        # 内置失败是严重问题，但不阻断启动（至少给一个空 Catalog）

    # 2. 用户级
    user_dir = Path.home() / ".suisuicode" / "agents"
    c._add_all(_load_from_dir(user_dir, Source.USER), Source.USER)

    # 3. 项目级
    project_dir = Path(root) / ".suisuicode" / "agents"
    c._add_all(_load_from_dir(project_dir, Source.PROJECT), Source.PROJECT)

    return c


def _load_from_dir(directory: Path, source: Source) -> list[Definition]:
    """从目录加载所有 .md 文件，失败打 stderr 跳过。"""
    if not directory.is_dir():
        return []

    defs: list[Definition] = []
    for p in sorted(directory.glob("*.md")):
        try:
            d = parse_file(str(p), source)
            defs.append(d)
        except AgentParseError as e:
            print(f"subagent: {e}", file=sys.stderr)
        except Exception as e:
            print(f"subagent: failed to load {p}: {e}", file=sys.stderr)
    return defs
