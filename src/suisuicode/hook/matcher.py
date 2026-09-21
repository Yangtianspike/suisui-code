"""Hook 条件求值：字段路径取值 + 条件组合（复用 permission.Matcher）。"""

from __future__ import annotations

import json

from suisuicode.hook.rule import CombineMode, Condition, Payload


def get_by_path(payload: Payload, path: str) -> str:
    """按 ``.`` 分隔的字段路径从 payload 中递归取值，返回字符串。

    - 中途遇 ``None`` 或非 dict → 返回空串
    - ``bool`` / ``int`` / ``float`` → ``str(value)``
    - 嵌套 ``dict`` / ``list`` → ``json.dumps(value, sort_keys=True)``

    >>> get_by_path({"tool_name": "bash", "tool_input": {"command": "ls"}}, "tool_name")
    'bash'
    >>> get_by_path({"tool_name": "bash"}, "tool_input.command")
    ''
    >>> get_by_path({"is_error": True}, "is_error")
    'True'
    """
    if not path:
        return ""

    current: object = payload
    for key in path.split("."):
        if not isinstance(current, dict):
            return ""
        current = current.get(key)
        if current is None:
            return ""

    if isinstance(current, bool):
        return "True" if current else "False"
    if isinstance(current, (int, float)):
        return str(current)
    if isinstance(current, str):
        return current
    # dict / list / other
    try:
        return json.dumps(current, sort_keys=True)
    except (TypeError, ValueError):
        return str(current)


def eval_condition(c: Condition | None, payload: Payload) -> bool:
    """求值条件表达式。

    - ``c is None`` → True（无条件触发）
    - ``CombineMode.ALL_OF`` → 全部 atom 通过
    - ``CombineMode.ANY_OF`` → 至少一个 atom 通过

    >>> from suisuicode.permission.matcher import ExactMatcher
    >>> c = Condition(
    ...     mode=CombineMode.ALL_OF,
    ...     atoms=[AtomCondition(field="tool_name", matcher=ExactMatcher("write_file"))],
    ... )
    >>> eval_condition(c, {"tool_name": "write_file"})
    True
    >>> eval_condition(c, {"tool_name": "read_file"})
    False
    """
    if c is None:
        return True

    results = [
        atom.matcher.match(get_by_path(payload, atom.field)) for atom in c.atoms
    ]

    if c.mode is CombineMode.ALL_OF:
        return all(results)
    # ANY_OF
    return any(results)
