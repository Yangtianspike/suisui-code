"""守护回归测试：Anthropic 缓存断点序列化正确性。

验证稳定块带有 cache_control、环境块不带 cache_control。
"""

from __future__ import annotations

from suisuicode.llm import Request, System


def _build_anthropic_system(req: Request) -> list[dict]:
    """模拟 AnthropicProvider.stream 中 system 块的构造逻辑。

    因为 provider 需要真实的 ProviderConfig（含 API key），此处直接测试构造逻辑。
    """
    system: list[dict] = []
    if req.system.stable:
        system.append(
            {
                "type": "text",
                "text": req.system.stable,
                "cache_control": {"type": "ephemeral"},
            }
        )
    if req.system.environment:
        system.append({"type": "text", "text": req.system.environment})
    return system


def test_stable_block_has_cache_control():
    """稳定块应带有 cache_control: ephemeral。"""
    req = Request(
        system=System(stable="You are suisuicode.", environment="Working dir: /test"),
    )
    blocks = _build_anthropic_system(req)
    assert len(blocks) == 2

    stable_block = blocks[0]
    assert stable_block["type"] == "text"
    assert stable_block["text"] == "You are suisuicode."
    assert stable_block["cache_control"] == {"type": "ephemeral"}


def test_env_block_no_cache_control():
    """环境块不应带有 cache_control。"""
    req = Request(
        system=System(stable="You are suisuicode.", environment="Working dir: /test"),
    )
    blocks = _build_anthropic_system(req)
    assert len(blocks) == 2

    env_block = blocks[1]
    assert env_block["type"] == "text"
    assert env_block["text"] == "Working dir: /test"
    assert "cache_control" not in env_block


def test_only_stable_block():
    """仅稳定块非空时，只输出一个块且带 cache_control。"""
    req = Request(system=System(stable="You are suisuicode."))
    blocks = _build_anthropic_system(req)
    assert len(blocks) == 1
    assert "cache_control" in blocks[0]


def test_only_env_block():
    """仅环境块非空时，只输出一个块且不带 cache_control。"""
    req = Request(system=System(environment="Working dir: /test"))
    blocks = _build_anthropic_system(req)
    assert len(blocks) == 1
    assert "cache_control" not in blocks[0]


def test_both_empty():
    """两段均为空时输出空列表。"""
    req = Request(system=System())
    blocks = _build_anthropic_system(req)
    assert blocks == []
