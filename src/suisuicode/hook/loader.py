"""Hook YAML 配置加载器：双层文件扫描、字段校验、Matcher 编译、合并去重。"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import yaml

from suisuicode.hook.engine import Engine
from suisuicode.hook.event import parse_event, is_blocking
from suisuicode.hook.rule import (
    Action,
    ActionType,
    AtomCondition,
    CombineMode,
    Condition,
    HttpAction,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)
from suisuicode.permission.matcher import compile_matcher


def load(project_root: str | Path) -> Engine:
    """扫描两层 hooks.yaml 配置，编译为 Engine。

    两层路径：
    - 项目级：``<project_root>/.suisuicode/hooks.yaml``
    - 用户级：``~/.suisuicode/hooks.yaml``

    两层叠加合并，项目级优先；name 冲突时跳过后到者。
    所有加载错误打 stderr 后继续，不抛异常、不阻断进程。
    """
    root = Path(project_root).resolve()
    candidates = [
        root / ".suisuicode" / "hooks.yaml",  # 项目级
        Path.home() / ".suisuicode" / "hooks.yaml",  # 用户级
    ]

    all_rules: list[Rule] = []
    sources: list[str] = []
    seen_names: dict[str, str] = {}  # name → source path

    for src_path in candidates:
        if not src_path.is_file():
            continue
        sources.append(str(src_path))
        try:
            raw = yaml.safe_load(src_path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            print(
                f"hooks: failed to parse {src_path}: {e}",
                file=sys.stderr,
            )
            continue

        if not isinstance(raw, dict):
            print(
                f"hooks: {src_path}: expected mapping, got "
                f"{type(raw).__name__}, skipped",
                file=sys.stderr,
            )
            continue

        hooks_raw = raw.get("hooks")
        if not isinstance(hooks_raw, list):
            print(
                f"hooks: {src_path}: 'hooks' must be a list, skipped",
                file=sys.stderr,
            )
            continue

        for idx, item in enumerate(hooks_raw):
            if not isinstance(item, dict):
                print(
                    f"hooks: {src_path}[{idx}]: not a mapping, skipped",
                    file=sys.stderr,
                )
                continue

            rule = _compile_rule(str(src_path), idx, item)
            if rule is None:
                continue

            # name 冲突检测
            if rule.name in seen_names:
                prev_src = seen_names[rule.name]
                print(
                    f'hook "{rule.name}": name conflict, '
                    f"already defined in {prev_src}, skipped",
                    file=sys.stderr,
                )
                continue

            seen_names[rule.name] = str(src_path)
            all_rules.append(rule)

    return Engine(rules=all_rules, sources=sources)


# ── internal: compile single rule ─────────────────────


def _compile_rule(source: str, idx: int, raw: dict[str, Any]) -> Rule | None:
    """从 YAML dict 编译单条 Rule；失败返回 None 并打 stderr。

    支持两种风格：
    - suisuicode 结构化：name/event/if(对象)/action.type
    - Claude Code 风格：id/event/if(字符串)/once/reject/command action
    """
    # id（Claude Code 风格）或 name（suisuicode 风格）
    name = raw.get("id") or raw.get("name")
    if not isinstance(name, str) or not name.strip():
        print(
            f"hooks: {source}[{idx}]: 'id' (or 'name') is required, skipped",
            file=sys.stderr,
        )
        return None
    name = name.strip()

    # event（支持 PascalCase 和 snake_case）
    event_s = raw.get("event")
    if not isinstance(event_s, str):
        print(
            f'hook "{name}": "event" is required, skipped',
            file=sys.stderr,
        )
        return None
    event = parse_event(event_s)
    if event is None:
        print(
            f'hook "{name}": unknown event "{event_s}", skipped',
            file=sys.stderr,
        )
        return None

    # action
    action = _compile_action(name, source, raw.get("action"))

    if action is None:
        return None

    # async + blocking validation
    asyncio_mode = bool(raw.get("async", False))
    if asyncio_mode and is_blocking(event):
        print(
            f'hook "{name}": async not allowed for blocking events, '
            f"skipped",
            file=sys.stderr,
        )
        return None

    # reject（Claude Code 风格）：仅 blocking events 可用
    reject = bool(raw.get("reject", False))
    if reject and not is_blocking(event):
        print(
            f'hook "{name}": reject only allowed on pre_tool_use / '
            f"user_prompt_submit, skipped",
            file=sys.stderr,
        )
        return None

    # condition（if 键存在且编译失败 → 跳过整条 hook）
    if_spec = raw.get("if")
    if if_spec is not None:
        condition = _compile_condition(name, source, if_spec)
        if condition is None:
            return None
    else:
        condition = None

    # only_once（支持 once 别名）
    only_once = bool(raw.get("once", False) or raw.get("only_once", False))

    # timeout
    timeout_s = _parse_duration(raw.get("timeout", "30s"), name)
    if timeout_s is None:
        return None

    return Rule(
        name=name,
        event=event,
        action=action,
        condition=condition,
        only_once=only_once,
        asyncio_mode=asyncio_mode,
        reject=reject,
        timeout_s=timeout_s,
        source=source,
    )


# ── internal: compile action ──────────────────────────


def _compile_action(
    name: str, source: str, raw: object
) -> Action | None:
    """编译动作对象。"""
    if not isinstance(raw, dict):
        print(
            f'hook "{name}": "action" must be a mapping, skipped',
            file=sys.stderr,
        )
        return None

    action_type_s = raw.get("type")
    if not isinstance(action_type_s, str):
        print(
            f'hook "{name}": action.type is required, skipped',
            file=sys.stderr,
        )
        return None

    # Claude Code 风格：type: command → 内部 shell
    if action_type_s == "command":
        action_type = ActionType.SHELL
    else:
        try:
            action_type = ActionType(action_type_s)
        except ValueError:
            print(
                f'hook "{name}": unknown action type "{action_type_s}", '
                f"skipped",
                file=sys.stderr,
            )
            return None

    if action_type is ActionType.SHELL:
        command = raw.get("command")
        if not isinstance(command, str) or not command.strip():
            print(
                f'hook "{name}": shell/command action requires '
                f'"command" field, skipped',
                file=sys.stderr,
            )
            return None
        return Action(type=action_type, shell=ShellAction(command=command))

    if action_type is ActionType.PROMPT:
        # Claude Code 风格：message → 内部 text
        text = raw.get("message") or raw.get("text")
        if not isinstance(text, str):
            print(
                f'hook "{name}": prompt action requires '
                f'"message" (or "text") field, skipped',
                file=sys.stderr,
            )
            return None
        return Action(type=action_type, prompt=PromptAction(text=text))

    if action_type is ActionType.HTTP:
        url = raw.get("url")
        if not isinstance(url, str) or not url.strip():
            print(
                f'hook "{name}": http action requires "url" field, '
                f"skipped",
                file=sys.stderr,
            )
            return None
        return Action(
            type=action_type,
            http=HttpAction(
                url=url,
                method=str(raw.get("method", "POST")).upper(),
                headers=_parse_string_map(raw.get("headers")),
                body=raw.get("body") if isinstance(raw.get("body"), str)
                else None,
            ),
        )

    if action_type is ActionType.SUBAGENT:
        agent_name = raw.get("agent_name")
        if not isinstance(agent_name, str) or not agent_name.strip():
            print(
                f'hook "{name}": subagent action requires '
                f'"agent_name" field, skipped',
                file=sys.stderr,
            )
            return None
        prompt = raw.get("prompt")
        if not isinstance(prompt, str):
            print(
                f'hook "{name}": subagent action requires '
                f'"prompt" field, skipped',
                file=sys.stderr,
            )
            return None
        return Action(
            type=action_type,
            subagent=SubagentAction(agent_name=agent_name, prompt=prompt),
        )

    return None


# ── internal: compile condition ───────────────────────


def _compile_condition(
    name: str, source: str, raw: object
) -> Condition | None:
    """编译条件表达式。raw 为 None（缺省 if:）→ 无条件触发。

    支持两种格式：
    - 结构化（suisuicode 风格）：``if: {all_of: [{field, match}, ...]}``
    - 紧凑字符串（Claude Code 风格）：``if: 'tool == "Bash" && args.command ~= /rm/'``
    """
    if raw is None:
        return None

    # Claude Code 风格：紧凑字符串
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        from suisuicode.hook.dsl import parse_if_expression

        cond, err = parse_if_expression(s)
        if err is not None:
            print(
                f'hook "{name}": invalid if expression: {err}, skipped',
                file=sys.stderr,
            )
            return None
        return cond

    # suisuicode 风格：结构化对象
    if not isinstance(raw, dict):
        print(
            f'hook "{name}": "if" must be a mapping or string, skipped',
            file=sys.stderr,
        )
        return None

    has_all = "all_of" in raw
    has_any = "any_of" in raw

    if has_all and has_any:
        print(
            f'hook "{name}": "if" cannot contain both all_of and '
            f"any_of, skipped",
            file=sys.stderr,
        )
        return None

    if not has_all and not has_any:
        print(
            f'hook "{name}": "if" must contain all_of or any_of, skipped',
            file=sys.stderr,
        )
        return None

    mode = CombineMode.ALL_OF if has_all else CombineMode.ANY_OF
    atoms_raw = raw["all_of" if has_all else "any_of"]

    if not isinstance(atoms_raw, list) or not atoms_raw:
        print(
            f'hook "{name}": "if.{mode.value}" must be a non-empty list, '
            f"skipped",
            file=sys.stderr,
        )
        return None

    atoms: list[AtomCondition] = []
    for i, atom_raw in enumerate(atoms_raw):
        if not isinstance(atom_raw, dict):
            print(
                f'hook "{name}": "if.{mode.value}[{i}]" not a mapping, '
                f"skipping atom",
                file=sys.stderr,
            )
            continue

        field = atom_raw.get("field")
        if not isinstance(field, str) or not field.strip():
            print(
                f'hook "{name}": "if.{mode.value}[{i}]" missing '
                f'"field", skipping atom',
                file=sys.stderr,
            )
            continue

        match_r = atom_raw.get("match")
        if not isinstance(match_r, dict):
            print(
                f'hook "{name}": "if.{mode.value}[{i}]" missing '
                f'"match", skipping atom',
                file=sys.stderr,
            )
            continue

        matcher = _compile_match(f'{name}:if.{mode.value}[{i}]', match_r)
        if matcher is None:
            # 编译失败 → 整条 hook 不可用
            return None

        atoms.append(AtomCondition(field=field.strip(), matcher=matcher))

    if not atoms:
        return None  # 全部 atom 编译失败 → hook 不可用

    return Condition(mode=mode, atoms=atoms)


def _compile_match(label: str, raw: dict[str, Any]):
    """编译单个 match 对象。"""
    mtype = raw.get("type")
    if not isinstance(mtype, str):
        print(
            f"hook {label}: match.type is required, skipping hook",
            file=sys.stderr,
        )
        return None

    if mtype == "not":
        inner_raw = raw.get("inner")
        if not isinstance(inner_raw, dict):
            print(
                f"hook {label}: match.type=not requires inner match, "
                f"skipping hook",
                file=sys.stderr,
            )
            return None
        inner = _compile_match(label, inner_raw)
        if inner is None:
            return None
        from suisuicode.permission.matcher import NotMatcher

        return NotMatcher(inner)

    value = raw.get("value")
    if not isinstance(value, str):
        print(
            f"hook {label}: match.type={mtype} requires 'value' string, "
            f"skipping hook",
            file=sys.stderr,
        )
        return None

    prefix_map = {"exact": "=", "regex": "~", "glob": ""}
    prefix = prefix_map.get(mtype)
    if prefix is None:
        print(
            f"hook {label}: unknown match type '{mtype}', skipping hook",
            file=sys.stderr,
        )
        return None

    try:
        return compile_matcher(f"{prefix}{value}", is_command=False)
    except ValueError as e:
        print(
            f"hook {label}: invalid match: {e}, skipping hook",
            file=sys.stderr,
        )
        return None


# ── helpers ────────────────────────────────────────────


_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smh]?)$")


def _parse_duration(raw: object, name: str) -> float | None:
    """解析时长字符串，支持 30s / 5m / 1h / 10.5。返回秒数。"""
    if raw is None:
        return 30.0
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        print(
            f'hook "{name}": timeout must be a duration string, '
            f"skipped",
            file=sys.stderr,
        )
        return None
    s = raw.strip()
    m = _DURATION_RE.match(s)
    if not m:
        print(
            f'hook "{name}": invalid timeout "{s}", skipped',
            file=sys.stderr,
        )
        return None
    value = float(m.group(1))
    unit = m.group(2)
    if unit == "m":
        value *= 60
    elif unit == "h":
        value *= 3600
    return value


def _parse_string_map(raw: object) -> dict[str, str]:
    """安全解析键值对。"""
    if not isinstance(raw, dict):
        return {}
    result: dict[str, str] = {}
    for k, v in raw.items():
        result[str(k)] = str(v)
    return result
