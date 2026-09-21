"""会话过期清理：删除超过 max_age 的会话目录。"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from suisuicode.compact.state import parse_session_time

logger = logging.getLogger(__name__)


def clean_expired(sessions_dir: str, max_age: timedelta) -> None:
    """扫描 sessions_dir，删除 session ID 时间戳超过 max_age 的目录。

    只处理能解析出新格式时间戳的目录，旧格式跳过。
    单个目录删除失败跳过不影响其他目录。
    """
    root = Path(sessions_dir)
    if not root.exists():
        return

    now = datetime.now()

    for entry in root.iterdir():
        if not entry.is_dir():
            continue

        # 尝试解析时间戳 → 旧格式跳过
        try:
            session_time = parse_session_time(entry.name)
        except ValueError:
            continue

        # 判断是否过期
        if now - session_time > max_age:
            try:
                shutil.rmtree(str(entry), ignore_errors=False)
            except Exception:
                logger.warning("清理过期会话失败: %s", entry.name, exc_info=True)
