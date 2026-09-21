"""输入解析：parse(input_text) → (name, args, is_slash)。"""

from __future__ import annotations


def parse(input_text: str) -> tuple[str, str, bool]:
    """解析用户输入是否为 slash 命令。

    Returns:
        (name, args, is_slash):
        - ("", "", False): 非 slash 命令，应走 LLM
        - ("", "", True): 以 "/" 开头但无法识别为有效命令名（空、仅/）
        - ("name", "args", True): 有效命令名（已小写化）+ 参数字符串
    """
    text = input_text.strip()
    if not text.startswith("/"):
        return ("", "", False)

    # 仅 "/"
    if text == "/":
        return ("", "", True)

    # 取掉前导 "/"
    rest = text[1:]

    # "/" 后紧接空白 → 无效命令（如 "/ /help"）
    if rest and rest[0].isspace():
        return ("", "", True)

    # 按空白切分
    parts = rest.split(maxsplit=1)

    name = parts[0].lower()

    # 空 name（如 "/ "）
    if not name:
        return ("", "", True)

    args = parts[1].strip() if len(parts) > 1 else ""

    return (name, args, True)
