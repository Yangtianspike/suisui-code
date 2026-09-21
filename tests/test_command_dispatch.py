"""T3: parse 输入解析测试。"""

import pytest

from suisuicode.command.dispatch import parse


@pytest.mark.parametrize(
    "input_text, expected",
    [
        ("", ("", "", False)),
        ("   ", ("", "", False)),
        ("hello", ("", "", False)),
        ("/", ("", "", True)),
        ("/help", ("help", "", True)),
        ("  /HELP  ", ("help", "", True)),
        ("/help xx", ("help", "xx", True)),  # 尾随参数 → 有效命令 + args
        ("/help  ", ("help", "", True)),  # 仅尾部空白 → 有效
        ("//double", ("/double", "", True)),  # "//" 剩余部分作为 name
        ("/ /help", ("", "", True)),  # name 为 "" 或含空格 → miss
    ],
)
def test_parse(input_text, expected):
    assert parse(input_text) == expected
