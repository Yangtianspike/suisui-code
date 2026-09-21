"""人在回路「永久放行」规则持久化（F8）。"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from suisuicode.llm import ToolCall
from suisuicode.permission.matcher import GlobMatcher
from suisuicode.permission.rule import Rule
from suisuicode.permission.settings import (
    load_settings,
    friendly_name,
    extract_target,
)

logger = logging.getLogger(__name__)


def rule_for(call: ToolCall, root: str) -> tuple[Rule, str, bool]:
    """从一次工具调用生成精确 allow 规则（无通配）。

    Args:
        call: 工具调用。
        root: 项目根绝对路径（用于计算相对路径）。

    Returns:
        (rule, yaml_str, ok): ok=False 表示生成失败。
    """
    friendly = friendly_name(call.name)
    target, is_file, ok = extract_target(call)

    if not ok:
        return Rule(), "", False

    if is_file:
        # 文件类 → 计算项目相对路径
        try:
            from pathlib import Path as _Path

            abs_target = str(
                _Path(target).resolve()
                if _Path(target).is_absolute()
                else (_Path(root) / target).resolve()
            )
            rel = str(_Path(abs_target).relative_to(root))
        except (ValueError, OSError):
            # 目标在 root 外，用原 target
            rel = target.lstrip("/")
        yaml_str = f"{friendly}({_escape_glob(rel)})"
        r = Rule(tool=friendly, matcher=GlobMatcher(_escape_glob(rel), is_command=False), raw=yaml_str, allow=True)
        return r, yaml_str, True
    else:
        # 命令执行类
        escaped = _escape_glob(target)
        yaml_str = f"{friendly}({escaped})"
        r = Rule(tool=friendly, matcher=GlobMatcher(escaped, is_command=True), raw=yaml_str, allow=True)
        return r, yaml_str, True


def _escape_glob(s: str) -> str:
    """转义 glob 元字符防止规则被意外泛化。"""
    for ch in ["*", "?", "[", "]"]:
        s = s.replace(ch, "\\" + ch)
    return s


def persist_local_allow(engine, call: ToolCall) -> None:
    """将精确 allow 规则写入本地层配置文件并同步内存。

    Args:
        engine: 权限引擎。
        call: 工具调用。

    Raises:
        OSError: 文件写入失败（调用方捕获后记日志）。
    """
    r, yaml_str, ok = rule_for(call, engine.root)
    if not ok:
        logger.warning("无法生成永久规则: %s(%s)", call.name, call.input)
        return

    local_path = Path(engine.local_path)

    # 加载现有配置
    settings = load_settings(str(local_path))

    # 去重：如果已有相同规则串则跳过
    existing = set(settings.permissions.allow)
    if yaml_str in existing:
        return

    # 追加
    settings.permissions.allow.append(yaml_str)

    # 确保目录存在
    local_path.parent.mkdir(parents=True, exist_ok=True)

    # 写回
    data: dict = {}
    if settings.default_mode:
        data["default_mode"] = settings.default_mode
    data["permissions"] = {
        "allow": settings.permissions.allow,
        "deny": settings.permissions.deny,
    }

    local_path.write_text(
        yaml.safe_dump(data, default_flow_style=False, allow_unicode=True),
        encoding="utf-8",
    )

    # 同步内存
    engine.local.allow.append(r)

    logger.info("永久放行已写入: %s → %s", yaml_str, local_path)
