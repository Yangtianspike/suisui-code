"""注册中心：register / lookup / visible / prefix_match + 冲突检测。"""

from __future__ import annotations

from suisuicode.command.command import Command


class Registry:
    """内置命令注册中心。

    维护 _by_name 字典（主名 + 别名 → Command）和 _visible 排序列表。
    """

    def __init__(self) -> None:
        self._by_name: dict[str, Command] = {}
        self._visible: list[Command] = []

    def register(self, cmd: Command) -> None:
        """注册一条命令。

        对 cmd.name 与 cmd.aliases 做冲突检测 ——
        任一 key 已存在于 _by_name 则 raise RuntimeError，含具体冲突键。
        通过后把每个 key 指向同一 cmd，visible 列表按 name 字典序重排。
        """
        # 校验
        if not cmd.name or cmd.name != cmd.name.lower():
            raise RuntimeError(f"命令名必须为非空全小写: {cmd.name!r}")
        for alias in cmd.aliases:
            if not alias or alias != alias.lower():
                raise RuntimeError(f"别名必须为非空全小写: {alias!r}")

        keys = [cmd.name, *cmd.aliases]
        for key in keys:
            if key in self._by_name:
                raise RuntimeError(f"command conflict: {key}")

        for key in keys:
            self._by_name[key] = cmd

        if not cmd.hidden:
            self._visible.append(cmd)
            self._visible.sort(key=lambda c: c.name)

    def lookup(self, name: str) -> Command | None:
        """按名查找（大小写不敏感）。"""
        return self._by_name.get(name.lower())

    def visible(self) -> list[Command]:
        """返回已排序的可见命令副本。"""
        return list(self._visible)

    def prefix_match(self, prefix: str) -> list[Command]:
        """按命令名前缀匹配（仅匹配 name，不匹配别名/描述）。

        prefix 可含前导 "/"，内部 strip 并小写。
        空 prefix 返回全部 visible。
        """
        p = prefix.lstrip("/").lower()
        if not p:
            return list(self._visible)
        return [c for c in self._visible if c.name.startswith(p)]
