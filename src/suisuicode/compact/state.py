from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


# ── 会话上下文 ─────────────────────────────────────────


def _new_session_id() -> str:
    """生成进程内唯一的会话 id，格式为 YYYYMMDD-HHMMSS-xxxx。"""
    now_str = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        hex_str = secrets.token_hex(2)  # 4 字符随机十六进制
    except Exception:
        import random

        logger.warning("secrets.token_hex 失败，降级为 random")
        hex_str = random.Random(time.time()).randbytes(2).hex()
    return f"{now_str}-{hex_str}"


def parse_session_time(session_id: str) -> datetime:
    """从 session ID 前 15 位解析 YYYYMMDD-HHMMSS 时间戳。

    格式为 YYYYMMDD-HHMMSS-xxxx，前 15 位固定为时间。
    旧格式（unix_ts-hex）解析失败。
    """
    time_part = session_id[:15]  # "YYYYMMDD-HHMMSS"
    return datetime.strptime(time_part, "%Y%m%d-%H%M%S")


@dataclass
class SessionContext:
    """会话生命周期信息。"""

    session_id: str
    session_dir: str  # 会话根目录，形如 .suisuicode/sessions/<id>/
    spill_dir: str  # 落盘根目录，形如 .suisuicode/sessions/<id>/tool-results/


def new_session_context(workspace: str) -> SessionContext:
    """创建新的会话上下文并建立落盘目录。"""
    sid = _new_session_id()
    session_dir = str(Path(workspace) / ".suisuicode" / "sessions" / sid)
    spill_dir = str(Path(session_dir) / "tool-results")
    Path(spill_dir).mkdir(parents=True, exist_ok=True)
    return SessionContext(session_id=sid, session_dir=session_dir, spill_dir=spill_dir)


def open_session_context(workspace: str, session_id: str) -> SessionContext:
    """打开已有会话目录（恢复场景），不创建目录。"""
    session_dir = str(Path(workspace) / ".suisuicode" / "sessions" / session_id)
    spill_dir = str(Path(session_dir) / "tool-results")
    return SessionContext(session_id=session_id, session_dir=session_dir, spill_dir=spill_dir)


# ── 替换决策账本 ───────────────────────────────────────


class ContentReplacementState:
    """会话级工具结果替换决策账本。

    _seen_ids 记录已决策过的 tool_use_id（无论 kept 还是 replaced）。
    _replacements 只保存"决定替换"那一支的预览字符串，键为 tool_use_id。
    同一个 id 一旦进入 _seen_ids 就永不翻转，保证 prompt cache 稳定。

    并发安全：Python asyncio 单线程事件循环保证串行访问。
    """

    def __init__(self) -> None:
        self._seen_ids: set[str] = set()
        self._replacements: dict[str, str] = {}

    def decide_once(
        self,
        tool_use_id: str,
        original: str,
        decide: Callable[[], tuple[str, str]],
    ) -> str:
        """原子完成"查账本 → 决策 → 写账本"。

        decide 回调返回 (decision, preview)：
          - "kept"   → 写 _seen_ids，不写 _replacements，返回 original。
          - "replaced" → 写 _seen_ids + _replacements，返回 preview。
          - "skip"   → 不写两本，返回 original（下一轮重试）。
        若 id 已 Seen，直接返回账本存量结果，不调 decide。
        """
        # 已决策：直接返回存量结果
        if tool_use_id in self._seen_ids:
            return self._replacements.get(tool_use_id, original)

        # 未决策：在临界区内完成决策 + 写入
        decision, preview = decide()

        if decision == "kept":
            self._seen_ids.add(tool_use_id)
            return original
        elif decision == "replaced":
            self._seen_ids.add(tool_use_id)
            self._replacements[tool_use_id] = preview
            return preview
        else:  # "skip"
            return original


# ── 熔断器 ─────────────────────────────────────────────


class CompactCircuitBreaker:
    """跟踪自动摘要连续失败次数，用于熔断。

    手动 / 紧急压缩路径不读这个字段。
    并发安全：Python asyncio 单线程事件循环保证串行访问。
    """

    def __init__(self) -> None:
        self._consecutive_failures = 0

    def record_success(self) -> None:
        """成功一次即清零连续失败计数。"""
        self._consecutive_failures = 0

    def record_failure(self) -> None:
        """记录一次失败，累加计数。"""
        self._consecutive_failures += 1

    def tripped(self) -> bool:
        """熔断是否已触发（连续失败达到阈值）。"""
        from suisuicode.compact.const import MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES

        return self._consecutive_failures >= MAX_CONSECUTIVE_AUTO_COMPACT_FAILURES


# ── 文件追踪 ───────────────────────────────────────────


@dataclass
class FileReadRecord:
    """一次成功文件读取的记录。"""

    path: str  # 绝对路径
    content: str  # 不带行号前缀的纯净字节（UTF-8 解码）
    timestamp: datetime  # 最后一次成功读取的时间


class RecoveryState:
    """Agent 主循环写、compact 摘要时读的文件追踪状态。

    键为文件绝对路径，避免相对路径在不同 cwd 下错乱。
    并发安全：Python asyncio 单线程事件循环保证串行访问。
    """

    def __init__(self) -> None:
        self._files: dict[str, FileReadRecord] = {}

    def record_file(self, path: str, content: str) -> None:
        """记录一次成功的文件读取。

        若 path 不是绝对路径则 resolve 一次再存。
        """
        p = Path(path)
        if not p.is_absolute():
            p = p.resolve()
        abs_path = str(p)
        self._files[abs_path] = FileReadRecord(
            path=abs_path,
            content=content,
            timestamp=datetime.now(timezone.utc),
        )

    def snapshot(self) -> list[FileReadRecord]:
        """返回按 timestamp 倒序排序的快照拷贝列表。"""
        records = list(self._files.values())
        records.sort(key=lambda r: r.timestamp, reverse=True)
        return records
