import textwrap
import pathlib
import pytest
from suisuicode.config import load, ConfigError


def write_yaml(tmp_path: pathlib.Path, content: str) -> str:
    p = tmp_path / "config.yaml"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(p)


def test_single_provider(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        providers:
          - name: claude
            protocol: anthropic
            api_key: sk-test
            model: claude-opus-4-5
    """,
    )
    cfg = load(path)
    assert len(cfg.providers) == 1
    assert cfg.providers[0].name == "claude"
    assert cfg.providers[0].thinking is False


def test_multiple_providers(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        providers:
          - name: claude
            protocol: anthropic
            api_key: sk-ant
            model: claude-opus-4-5
          - name: gpt
            protocol: openai
            api_key: sk-oai
            model: gpt-4o
    """,
    )
    cfg = load(path)
    assert len(cfg.providers) == 2
    assert cfg.providers[1].protocol == "openai"


def test_missing_api_key(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        providers:
          - name: claude
            protocol: anthropic
            model: claude-opus-4-5
    """,
    )
    with pytest.raises(ConfigError, match="api_key"):
        load(path)


def test_invalid_protocol(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        providers:
          - name: test
            protocol: gemini
            api_key: sk-x
            model: gemini-pro
    """,
    )
    with pytest.raises(ConfigError, match="protocol"):
        load(path)


def test_file_not_found():
    with pytest.raises(ConfigError, match="不存在"):
        load("/no/such/file.yaml")


def test_base_url_and_thinking(tmp_path):
    path = write_yaml(
        tmp_path,
        """
        providers:
          - name: local
            protocol: anthropic
            api_key: sk-x
            model: claude-3-5-sonnet-20241022
            base_url: http://localhost:8080
            thinking: true
    """,
    )
    cfg = load(path)
    assert cfg.providers[0].base_url == "http://localhost:8080"
    assert cfg.providers[0].thinking is True


# ── ch08 effective_context_window 测试 ─────────────────

def test_effective_context_window_unconfigured():
    from suisuicode.config import effective_context_window, ProviderConfig
    p = ProviderConfig(name="t", protocol="anthropic", api_key="k", model="m")
    assert effective_context_window(p) == 200000


def test_effective_context_window_zero():
    from suisuicode.config import effective_context_window, ProviderConfig
    p = ProviderConfig(name="t", protocol="openai", api_key="k", model="m", context_window=0)
    assert effective_context_window(p) == 128000


def test_effective_context_window_positive():
    from suisuicode.config import effective_context_window, ProviderConfig
    p = ProviderConfig(name="t", protocol="anthropic", api_key="k", model="m", context_window=80000)
    assert effective_context_window(p) == 80000


def test_effective_context_window_unknown_protocol():
    from suisuicode.config import effective_context_window, ProviderConfig
    p = ProviderConfig(name="t", protocol="deepseek", api_key="k", model="m")
    assert effective_context_window(p) == 200000


def test_context_window_from_yaml(tmp_path):
    """验证 context_window 字段从 YAML 解码。"""
    import yaml
    yaml_text = textwrap.dedent("""\
        providers:
          - name: test
            protocol: anthropic
            api_key: sk-x
            model: claude
            context_window: 80000
    """)
    path = str(tmp_path / "config.yaml")
    pathlib.Path(path).write_text(yaml_text, encoding="utf-8")
    cfg = load(path)
    assert cfg.providers[0].context_window == 80000
