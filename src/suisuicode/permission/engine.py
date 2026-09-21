"""权限引擎：前四层判定流水线 + 配置加载与合并（F6）。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from suisuicode.llm import ToolCall
from suisuicode.permission import Decision, Mode, Category, parse_mode
from suisuicode.permission.blacklist import hits_blacklist
from suisuicode.permission.rule import RuleSet
from suisuicode.permission.sandbox import resolve_root, sandbox_ok
from suisuicode.permission.settings import (
    SettingsError,
    load_settings,
    to_rule_set,
    friendly_name,
    categorize,
    extract_target,
)

logger = logging.getLogger(__name__)


@dataclass
class Engine:
    """权限引擎：持有项目根、黑名单、三级规则集、本地层路径、启动默认模式。"""

    root: str  # 项目根（绝对、已解析符号链接）
    user: RuleSet  # 用户级规则
    project: RuleSet  # 项目级规则
    local: RuleSet  # 本地级规则
    local_path: str  # 本地层配置文件路径
    start_mode: Mode  # 启动默认模式


def new_engine(root: str) -> tuple[Engine, Exception | None]:
    """构造权限引擎：解析项目根、加载三层配置、确定启动模式。

    配置文件格式错误绝不致引擎构造失败，只降级该文件为空。
    唯一可能返回非 None err 的是 resolve_root 失败（此时 root 退化为传入值）。

    Returns:
        (engine, err): engine 必非 None。
    """
    # ── 解析项目根 ──────────────────────────────────
    err: Exception | None = None
    try:
        resolved_root = resolve_root(root)
    except Exception as e:
        resolved_root = root  # 退化
        err = e

    # ── 加载三层配置 ─────────────────────────────────
    home = str(Path.home())
    user_path = str(Path(home) / ".suisuicode" / "settings.yaml")
    project_path = str(Path(resolved_root) / ".suisuicode" / "settings.yaml")
    local_path = str(Path(resolved_root) / ".suisuicode" / "settings.local.yaml")

    user_rs = _load_rule_set(user_path)
    project_rs = _load_rule_set(project_path)
    local_rs = _load_rule_set(local_path)

    # ── 确定启动默认模式 ─────────────────────────────
    start_mode = Mode.DEFAULT
    for settings_path in (local_path, project_path, user_path):
        try:
            s = load_settings(settings_path)
        except SettingsError:
            continue
        if s.default_mode:
            m, ok = parse_mode(s.default_mode)
            if ok:
                start_mode = m
                break  # local > project > user，第一个命中即生效

    engine = Engine(
        root=resolved_root,
        user=user_rs,
        project=project_rs,
        local=local_rs,
        local_path=local_path,
        start_mode=start_mode,
    )
    return engine, err


def _load_rule_set(path: str) -> RuleSet:
    """加载单个配置文件的规则集；文件缺失/格式错误降级为空（N5）。"""
    try:
        s = load_settings(path)
        return to_rule_set(s)
    except SettingsError:
        return RuleSet()
    except Exception:
        logger.debug("加载权限配置失败: %s", path, exc_info=True)
        return RuleSet()


def mode_fallback(mode: Mode, cat: Category) -> Decision:
    """模式兜底矩阵（F5）：只产 Allow/Ask，绝不产 Deny。

    | 模式              | READ | WRITE | EXEC |
    |-------------------|------|-------|------|
    | DEFAULT           | Allow| Ask   | Ask  |
    | ACCEPT_EDITS      | Allow| Allow | Ask  |
    | PLAN              | Allow| Ask   | Ask  |
    | BYPASS            | Allow| Allow | Allow|
    """
    if cat == Category.READ:
        return Decision.ALLOW
    if mode == Mode.BYPASS:
        return Decision.ALLOW
    if mode == Mode.ACCEPT_EDITS and cat == Category.WRITE:
        return Decision.ALLOW
    # DEFAULT / PLAN 的 WRITE/EXEC，及 ACCEPT_EDITS 的 EXEC
    return Decision.ASK


def check(
    engine: Engine, mode: Mode, call: ToolCall, read_only: bool
) -> tuple[Decision, str]:
    """前四层判定流水线。

    ① 黑名单（仅 Exec 类）→ ② 沙箱（仅文件类）→ ③ 规则引擎（三级）→ ④ 模式兜底。
    任一层给出 Allow/Deny 即短路返回。

    Returns:
        (Decision, reason): reason 在 Allow 时为空串。
    """
    cat = categorize(call.name, read_only)
    friendly = friendly_name(call.name)
    target, is_file, ok = extract_target(call)

    # ── ① 黑名单（仅命令执行类） ──────────────────
    if cat == Category.EXEC and target != "" and hits_blacklist(target):
        return Decision.DENY, f"命中危险命令黑名单：{_truncate_reason(target)}"

    # ── ② 沙箱（仅文件类） ────────────────────────
    if is_file:
        if not ok:
            return Decision.DENY, "无法解析文件路径参数，安全拒绝"
        if not sandbox_ok(engine.root, target):
            return Decision.DENY, f"路径在项目目录之外：{_truncate_reason(target)}"

    # ── ③ 规则引擎（local → project → user） ─────
    for layer_name, rule_set in [
        ("本地", engine.local),
        ("项目", engine.project),
        ("用户", engine.user),
    ]:
        d, hit = rule_set.match(friendly, target)
        if hit:
            if d == Decision.DENY:
                return (
                    Decision.DENY,
                    f"匹配 {layer_name} deny 规则：{friendly}({target})",
                )
            # Allow 规则命中 → 直接放行
            return Decision.ALLOW, ""

    # ── ④ 模式兜底 ────────────────────────────────
    fb = mode_fallback(mode, cat)
    if fb == Decision.ASK:
        cat_name = {
            Category.READ: "只读",
            Category.WRITE: "文件写入",
            Category.EXEC: "命令执行",
        }.get(cat, str(cat))
        return Decision.ASK, f"{mode} 模式下 {cat_name} 类操作需确认"
    return Decision.ALLOW, ""


def _truncate_reason(s: str, max_len: int = 120) -> str:
    """截断过长的原因文本。"""
    if len(s) <= max_len:
        return s
    return s[:max_len] + "…"
