"""Claude Code 风格 DSL：紧凑 if 表达式解析 + $VAR 变量替换。

将 Claude Code 风格的紧凑表达式转换为内部结构化 Condition，
同时提供反向的 payload 变量访问接口。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from suisuicode.hook.rule import AtomCondition, CombineMode, Condition, Payload
from suisuicode.permission.matcher import compile_matcher


# ── Tokenizer ───────────────────────────────────────────────


@dataclass
class _Tok:
    kind: str  # FIELD, OP, VAL_STR, VAL_REGEX, AND, OR
    raw: str
    pos: int = 0


def _tokenize(s: str, source: str = "") -> tuple[list[_Tok], str | None]:
    """将紧凑 if 字符串拆为 token 序列。

    Returns:
        (tokens, None) 成功；(空列表, error) 失败。
    """
    tokens: list[_Tok] = []
    i = 0
    n = len(s)

    while i < n:
        ch = s[i]

        # 空白跳过
        if ch in " \t\n":
            i += 1
            continue

        # &&
        if s[i : i + 2] == "&&":
            tokens.append(_Tok("AND", "&&", i))
            i += 2
            continue

        # ||
        if s[i : i + 2] == "||":
            tokens.append(_Tok("OR", "||", i))
            i += 2
            continue

        # !!=
        if s[i : i + 3] == "!!=":
            tokens.append(_Tok("OP", "!!=", i))
            i += 3
            continue

        # !=
        if s[i : i + 2] == "!=":
            tokens.append(_Tok("OP", "!=", i))
            i += 2
            continue

        # =~ (Claude Code 标准语法)
        if s[i : i + 2] == "=~":
            tokens.append(_Tok("OP", "=~", i))
            i += 2
            continue

        # ~= (suisuicode 兼容写法)
        if s[i : i + 2] == "~=":
            tokens.append(_Tok("OP", "~=", i))
            i += 2
            continue

        # ==
        if s[i : i + 2] == "==":
            tokens.append(_Tok("OP", "==", i))
            i += 2
            continue

        # 双引号字符串
        if ch == '"':
            j = i + 1
            while j < n and s[j] != '"':
                if s[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            if j >= n:
                return [], f"unclosed string at pos {i}"
            raw = s[i : j + 1]
            # unescape
            inner = s[i + 1 : j].replace('\\"', '"').replace("\\\\", "\\")
            tokens.append(_Tok("VAL_STR", inner, i))
            i = j + 1
            continue

        # 单引号字符串
        if ch == "'":
            j = i + 1
            while j < n and s[j] != "'":
                if s[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            if j >= n:
                return [], f"unclosed string at pos {i}"
            inner = s[i + 1 : j].replace("\\'", "'").replace("\\\\", "\\")
            tokens.append(_Tok("VAL_STR", inner, i))
            i = j + 1
            continue

        # 正则 /pattern/
        if ch == "/":
            j = i + 1
            while j < n and s[j] != "/":
                if s[j] == "\\" and j + 1 < n:
                    j += 2
                else:
                    j += 1
            if j >= n:
                return [], f"unclosed regex at pos {i}"
            inner = s[i + 1 : j]
            tokens.append(_Tok("VAL_REGEX", inner, i))
            i = j + 1
            continue

        # 字段名
        if ch.isidentifier():
            j = i
            while j < n and (s[j].isidentifier() or s[j] == "." or s[j] == "_"):
                j += 1
            raw = s[i:j]
            tokens.append(_Tok("FIELD", raw, i))
            i = j
            continue

        return [], f"unexpected character {ch!r} at pos {i}"

    return tokens, None


# ── Parser ──────────────────────────────────────────────────


_FIELD_MAP: dict[str, str] = {
    "tool": "tool_name",
    "args": "tool_input",
    "result": "tool_result",
}


def _map_field(raw: str) -> str:
    """将 Claude Code 风格字段映射为内部 payload 路径。

    tool          → tool_name
    args.command  → tool_input.command
    args.path     → tool_input.path
    """
    parts = raw.split(".")
    if parts[0] in _FIELD_MAP:
        parts[0] = _FIELD_MAP[parts[0]]
    return ".".join(parts)


def _compile_value(op: str, value: str, is_regex: bool) -> object:
    """根据操作符和值类型编译 matcher。

    ==  → exact
    ~= + string → glob
    ~= + regex → regex
    !=  → not exact
    !!== → not glob
    """
    # 构建 compile_matcher 前缀
    if op == "==":
        prefix = "="
    elif op in ("~=", "=~"):
        if is_regex:
            prefix = "~"
        else:
            prefix = ""  # glob（无前缀）
    elif op == "!=":
        prefix = "!="
    elif op == "!!=":
        prefix = "!"  # not glob
    else:
        prefix = "="

    return compile_matcher(f"{prefix}{value}", is_command=False)


def parse_if_expression(s: str) -> tuple[Condition | None, str | None]:
    """解析 Claude Code 风格的紧凑 if 字符串。

    Grammar::

        expr   = and_expr ("||" and_expr)*
        and_expr = atom ("&&" atom)*
        atom     = field op value
        field    = "tool" | IDENT ("." IDENT)*
        op       = "==" | "~=" | "!=" | "!!="
        value    = STRING | REGEX

    Examples::

        'tool == "WriteFile" && args.path ~= "*.py"'
            → all_of: tool_name exact "WriteFile", tool_input.path glob "*.py"

        'tool == "Bash" && args.command ~= /rm\\s+-rf/'
            → all_of: tool_name exact "Bash", tool_input.command regex "rm\\s+-rf"

    Returns:
        (Condition, None) 成功；(None, error_string) 失败。
    """
    tokens, err = _tokenize(s, "")
    if err is not None:
        return None, err
    if not tokens:
        return None, "empty expression"

    # 检查是否混用 && 和 ||
    has_and = any(t.kind == "AND" for t in tokens)
    has_or = any(t.kind == "OR" for t in tokens)
    if has_and and has_or:
        return None, (
            "mixed && and || in same expression is not supported; "
            "use a single level of all_of or any_of"
        )

    mode = CombineMode.ALL_OF  # 默认 &&；纯单 atom 时无所谓
    if has_or:
        mode = CombineMode.ANY_OF

    atoms: list[AtomCondition] = []
    i = 0
    while i < len(tokens):
        # 期望：FIELD OP VALUE
        if i + 2 >= len(tokens) or tokens[i].kind != "FIELD":
            return None, f"expected field at pos {tokens[i].pos}"
        if tokens[i + 1].kind != "OP":
            return None, f"expected operator at pos {tokens[i+1].pos}"
        val_t = tokens[i + 2]
        if val_t.kind not in ("VAL_STR", "VAL_REGEX"):
            return None, f"expected value at pos {val_t.pos}"

        field_raw = tokens[i].raw
        op_raw = tokens[i + 1].raw
        val_raw = val_t.raw
        is_regex = val_t.kind == "VAL_REGEX"

        field_path = _map_field(field_raw)
        try:
            matcher = _compile_value(op_raw, val_raw, is_regex)
        except ValueError as e:
            return None, f"invalid value at pos {tokens[i].pos}: {e}"

        atoms.append(AtomCondition(field=field_path, matcher=matcher))
        i += 3

        # 跳过 AND/OR 分隔符
        if i < len(tokens) and tokens[i].kind in ("AND", "OR"):
            i += 1

    if not atoms:
        return None, "no conditions found"

    return Condition(mode=mode, atoms=atoms), None


# ── Variable Substitution ───────────────────────────────────


# 顶级 payload 叶子键的快捷名映射
_VAR_ALIASES: dict[str, str] = {
    "TOOL_NAME": "tool_name",
    "FILE_PATH": "tool_input.path",
    "COMMAND": "tool_input.command",
    "ARGS": None,  # 特殊：JSON dump 整个 tool_input
    "ARGUMENTS": None,
    "RESULT": "tool_result",
    "EVENT": "event",
    "SESSION_ID": "session_id",
    "CWD": "cwd",
    "MODE": "mode",
}

_SUBST_RE = re.compile(r"\$([A-Z_][A-Z0-9_]*|[a-z_][a-z0-9_.]*|\{[^}]+\})")


def _get_payload_value(payload: Payload, path: str) -> str:
    """从 payload 中按点分隔路径取值，返回字符串。"""
    current: object = payload
    for key in path.split("."):
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return ""
        if current is None:
            return ""
    if isinstance(current, str):
        return current
    if isinstance(current, (bool, int, float)):
        return str(current)
    try:
        return json.dumps(current, sort_keys=True)
    except (TypeError, ValueError):
        return str(current)


def substitute_variables(template: str, payload: Payload) -> str:
    """替换模板字符串中的 ``$VAR`` 和 ``${VAR}``。

    可用变量：

    - ``$TOOL_NAME`` → tool_name
    - ``$FILE_PATH`` → tool_input.path
    - ``$COMMAND``   → tool_input.command
    - ``$ARGS``      → JSON dump of tool_input
    - ``$RESULT``    → tool_result（仅 PostToolUse）
    - ``$EVENT``     → event name
    - ``$SESSION_ID``→ session_id
    - ``$CWD``       → cwd
    - ``$MODE``      → permission mode

    通用路径：``$tool_input.command`` / ``${tool_input.command}``。

    >>> substitute_variables("black $FILE_PATH", {"tool_input": {"path": "x.py"}})
    'black x.py'
    """
    def _repl(m: re.Match) -> str:
        raw = m.group(1)
        # ${var} 或 $var
        var_name = raw.rstrip("}").lstrip("{") if raw else ""
        if not var_name:
            return ""
        if var_name.isupper():
            # 特殊：$ARGS / $ARGUMENTS → JSON dump tool_input
            if var_name in ("ARGS", "ARGUMENTS"):
                ti = payload.get("tool_input", {})
                if isinstance(ti, dict):
                    return json.dumps(ti, sort_keys=True)
                return str(ti)
            # 顶级别名
            alias = _VAR_ALIASES.get(var_name)
            if alias is not None:
                return _get_payload_value(payload, alias)
            # 未知大写变量 → 空
            return ""
        else:
            # 通用路径：$tool_input.path（或 $NOT_EXIST 等未知名）
            return _get_payload_value(payload, var_name)

    return _SUBST_RE.sub(_repl, template)
