"""Team feature flag 读取。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from suisuicode.config.config import Config


def fork_teammate_enabled(cfg: Config) -> bool:
    """FORK_TEAMMATE feature flag —— 默认关闭。

    开启后，Agent(team_name=..., subagent_type="" 留空) 走 Fork 路径
    （继承 Lead 完整对话历史），否则回退到 general-purpose 定义。
    """
    try:
        features = getattr(cfg, "features", None)
        if features is None:
            return False
        return bool(getattr(features, "fork_teammate", False))
    except Exception:
        return False
