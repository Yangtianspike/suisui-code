"""跨进程文件锁机制。

用于 mailbox 与 tasks 的并发安全写操作。
os.open(O_CREAT|O_EXCL) 抢占 + 随机抖动重试 + stale 检测。
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

# ── 常量 ─────────────────────────────────────────────────

LOCK_MAX_RETRIES: int = 10
LOCK_STALE_AFTER: float = 10.0  # 秒
LOCK_BACKOFF_MIN: float = 0.005  # 5ms
LOCK_BACKOFF_MAX: float = 0.100  # 100ms


# ── acquire ──────────────────────────────────────────────


@asynccontextmanager
async def acquire(lock_path: str | Path) -> AsyncIterator[None]:
    """抢文件锁并返回异步上下文管理器，退出时自动释放。

    抢占失败时随机抖动重试（最多 10 次）。
    持锁超过 10 秒视为 stale，直接清掉旧锁重试。
    """
    p = Path(lock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lp = str(p)

    acquired = False
    last_error: Exception | None = None

    for i in range(LOCK_MAX_RETRIES):
        try:
            fd = os.open(lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            # 检查 stale
            try:
                st = p.stat()
                if time.time() - st.st_mtime > LOCK_STALE_AFTER:
                    # stale lock，清掉重试
                    os.unlink(lp)
                    try:
                        fd = os.open(
                            lp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
                        )
                        os.close(fd)
                        acquired = True
                        break
                    except FileExistsError:
                        pass  # 被别人抢了，继续抖动
            except FileNotFoundError:
                pass  # lock 在 stat 前已被释放，下一轮重试
        except OSError as e:
            last_error = e

        if i < LOCK_MAX_RETRIES - 1:
            await asyncio.sleep(
                random.uniform(LOCK_BACKOFF_MIN, LOCK_BACKOFF_MAX)
            )

    if not acquired:
        err_msg = (
            f"无法获取文件锁 {lp}: "
            f"已重试 {LOCK_MAX_RETRIES} 次"
        )
        if last_error:
            err_msg += f"（最后错误: {last_error}）"
        raise RuntimeError(err_msg)

    try:
        yield
    finally:
        try:
            os.unlink(lp)
        except FileNotFoundError:
            pass  # 已被清理
        except OSError:
            pass
