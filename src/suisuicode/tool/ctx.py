"""工具 ctx cwd 传递机制。

通过 ``contextvars.ContextVar`` 实现显式 cwd 传递，
不改变进程级 ``os.getcwd()``。

工具调用时用 ``resolve_path`` 解析路径：
- 绝对路径原样返回
- 相对路径以 ctx cwd（优先）或进程 cwd 为基准拼接
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from pathlib import Path

_ctx_cwd: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "cwd", default=None
)


@contextmanager
def with_cwd(directory: str):
    """上下文管理器：在当前协程/线程内设置 ctx cwd。

    ``directory`` 为空字符串时直接 yield，不设 ContextVar。
    """
    if not directory:
        yield
        return
    token = _ctx_cwd.set(directory)
    try:
        yield
    finally:
        _ctx_cwd.reset(token)


def cwd_from_ctx() -> str | None:
    """返回当前 ctx 中的 cwd（None 表示未设置）。"""
    return _ctx_cwd.get()


def resolve_path(p: str) -> str:
    """解析路径：绝对路径直接返回，相对路径以 ctx cwd 或进程 cwd 为基准。

    ``p`` 为空字符串时返回基准 cwd 本身。
    """
    base = _ctx_cwd.get() or str(Path.cwd())
    if not p:
        return base
    pp = Path(p)
    if pp.is_absolute():
        return str(pp)
    return str(Path(base) / pp)
