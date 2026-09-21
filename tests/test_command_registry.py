"""T2: Registry 注册、冲突、前缀匹配、visible 排序测试。"""

import pytest

from suisuicode.command.command import Command, Kind
from suisuicode.command.registry import Registry


def _cmd(name: str, **kw) -> Command:
    defaults = {"description": f"{name} 命令", "kind": Kind.LOCAL}
    defaults.update(kw)
    if "handler" not in defaults:
        async def _noop(ui):
            pass
        defaults["handler"] = _noop
    return Command(name=name, **defaults)


def test_register_ok():
    reg = Registry()
    reg.register(_cmd("help"))
    reg.register(_cmd("status"))
    assert reg.lookup("help") is not None
    assert reg.lookup("status") is not None
    assert reg.lookup("HELP") is not None  # 大小写不敏感


def test_register_duplicate_name_raises():
    reg = Registry()
    reg.register(_cmd("help"))
    with pytest.raises(RuntimeError, match="command conflict"):
        reg.register(_cmd("help"))


def test_register_duplicate_alias_raises():
    reg = Registry()
    reg.register(_cmd("help", aliases=["h"]))
    with pytest.raises(RuntimeError, match="command conflict"):
        reg.register(_cmd("hello", aliases=["h"]))


def test_visible_sorted():
    reg = Registry()
    reg.register(_cmd("compact"))
    reg.register(_cmd("clear"))
    reg.register(_cmd("help"))
    names = [c.name for c in reg.visible()]
    assert names == ["clear", "compact", "help"]


def test_prefix_match():
    reg = Registry()
    reg.register(_cmd("help"))
    reg.register(_cmd("status"))
    reg.register(_cmd("session"))
    reg.register(_cmd("exit"))

    # prefix_match 只匹配 name
    assert len(reg.prefix_match("/s")) == 2
    names = [c.name for c in reg.prefix_match("/s")]
    assert "session" in names
    assert "status" in names

    # 空 prefix 返回全部
    assert len(reg.prefix_match("")) == 4
    assert len(reg.prefix_match("/")) == 4

    # 不匹配
    assert len(reg.prefix_match("/z")) == 0


def test_register_hidden_not_in_visible():
    reg = Registry()
    async def _noop(ui):
        pass
    reg.register(Command(name="secret", description="hidden", kind=Kind.LOCAL, handler=_noop, hidden=True))
    reg.register(_cmd("help"))
    assert len(reg.visible()) == 1
    assert reg.visible()[0].name == "help"
    # dispatcher 仍可命中
    assert reg.lookup("secret") is not None
    # prefix_match 也不含 hidden
    assert len(reg.prefix_match("s")) == 0
