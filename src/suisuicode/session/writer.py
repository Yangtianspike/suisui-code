"""会话 JSONL 写入器：追加写、fsync 刷盘、线程安全。"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass

from suisuicode import llm

logger = __import__("logging").getLogger(__name__)


@dataclass
class Entry:
    """JSONL 中一行的 dataclass 表示。"""

    role: str = ""
    content: str = ""
    tool_calls: list[dict] | None = None
    tool_results: list[dict] | None = None
    ts: int = 0
    model: str | None = None
    type: str | None = None  # "compact" 或省略


class Writer:
    """向 conversation.jsonl 追加写入的会话写入器。

    线程安全：所有写操作持 threading.Lock。
    每次 append 后 file.flush() + os.fsync() 刷盘。
    """

    def __init__(self, session_dir: str) -> None:
        os.makedirs(session_dir, exist_ok=True)
        jsonl_path = os.path.join(session_dir, "conversation.jsonl")
        self._file = open(jsonl_path, "ab")
        self._path = jsonl_path
        self._lock = threading.Lock()

    @classmethod
    def open_existing(cls, session_dir: str) -> "Writer":
        """打开已有会话目录的 JSONL（追加模式），不创建目录。"""
        jsonl_path = os.path.join(session_dir, "conversation.jsonl")
        writer = object.__new__(cls)
        writer._path = jsonl_path
        writer._file = open(jsonl_path, "ab")
        writer._lock = threading.Lock()
        return writer

    @property
    def path(self) -> str:
        """JSONL 文件的绝对路径。"""
        return self._path

    # ── 写入 ──────────────────────────────────────────

    def append(self, msg: llm.Message, model: str = "", is_first: bool = False) -> None:
        """追加一条消息到 JSONL。

        Args:
            msg: llm.Message 实例
            model: 模型名（仅首条消息携带）
            is_first: 是否为首条消息（携带 model 字段）
        """
        entry = Entry(
            role=msg.role,
            content=msg.content,
            tool_calls=(
                [asdict(tc) for tc in msg.tool_calls] if msg.tool_calls else None
            ),
            tool_results=(
                [asdict(tr) for tr in msg.tool_results] if msg.tool_results else None
            ),
            ts=int(time.time()),
            model=model if is_first else None,
        )
        self._write_line(_entry_to_dict(entry))

    def write_compact_marker(self) -> None:
        """写入压缩标记行。"""
        marker = {"type": "compact", "ts": int(time.time())}
        self._write_line(marker)

    def append_all(self, msgs: list[llm.Message]) -> None:
        """逐条追加消息列表（compact 后的新消息）。"""
        for msg in msgs:
            self.append(msg, model="", is_first=False)

    # ── 生命周期 ──────────────────────────────────────

    def close(self) -> None:
        """关闭文件句柄。"""
        with self._lock:
            if not self._file.closed:
                self._file.close()

    def __enter__(self) -> "Writer":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
        return None

    # ── 内部 ──────────────────────────────────────────

    def _write_line(self, obj: dict) -> None:
        """加锁、序列化、写入一行 JSON、fsync。"""
        line = json.dumps(obj, ensure_ascii=False) + "\n"
        data = line.encode("utf-8")
        with self._lock:
            self._file.write(data)
            self._file.flush()
            os.fsync(self._file.fileno())


def _entry_to_dict(entry: Entry) -> dict:
    """将 Entry 转为 dict，去除值为 None 的字段。"""
    d = {
        "role": entry.role,
        "content": entry.content,
        "ts": entry.ts,
    }
    if entry.tool_calls is not None:
        d["tool_calls"] = entry.tool_calls
    if entry.tool_results is not None:
        d["tool_results"] = entry.tool_results
    if entry.model is not None:
        d["model"] = entry.model
    if entry.type is not None:
        d["type"] = entry.type
    return d
