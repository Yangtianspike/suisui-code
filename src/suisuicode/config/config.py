"""Provider 配置结构、YAML 加载与 context_window 派生。"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Literal

import yaml

from suisuicode.config.protocol_defaults import (
    DEFAULT_ANTHROPIC_CONTEXT_WINDOW,
    DEFAULT_OPENAI_CONTEXT_WINDOW,
)


class ConfigError(Exception):
    pass


@dataclass
class ProviderConfig:
    name: str
    protocol: Literal["anthropic", "openai", "deepseek"]
    api_key: str
    model: str
    base_url: str | None = None
    thinking: bool = False
    context_window: int = 0  # 单位 token；0 表示走协议默认


@dataclass
class FeaturesConfig:
    """ch15: Feature flags。"""

    coordinator_mode: bool = False
    fork_teammate: bool = False


@dataclass
class Config:
    providers: list[ProviderConfig] = field(default_factory=list)
    enable_subagent_background: bool | None = None  # YAML key: enableSubAgentBackground；默认 True
    features: FeaturesConfig = field(default_factory=FeaturesConfig)

    def effective_enable_subagent_background(self) -> bool:
        """返回 enable_subagent_background 的有效值（None → True）。"""
        if self.enable_subagent_background is None:
            return True
        return self.enable_subagent_background


def load(path: str) -> Config:
    p = pathlib.Path(path).expanduser()
    if not p.exists():
        raise ConfigError(f"配置文件不存在: {path}")

    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML 格式错误: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError("配置文件格式错误：顶层应为 YAML 映射")

    raw_providers = raw.get("providers")
    if not raw_providers:
        raise ConfigError("配置文件缺少 providers 列表，或列表为空")
    if not isinstance(raw_providers, list):
        raise ConfigError("providers 应为列表")

    providers: list[ProviderConfig] = []
    for i, item in enumerate(raw_providers):
        if not isinstance(item, dict):
            raise ConfigError(f"providers[{i}] 格式错误")
        providers.append(_parse_provider(i, item))

    features = FeaturesConfig()
    raw_features = raw.get("features")
    if isinstance(raw_features, dict):
        features = FeaturesConfig(
            coordinator_mode=bool(
                raw_features.get("coordinator_mode", False)
            ),
            fork_teammate=bool(
                raw_features.get("fork_teammate", False)
            ),
        )

    return Config(
        providers=providers,
        enable_subagent_background=raw.get("enableSubAgentBackground"),
        features=features,
    )


def _parse_provider(idx: int, item: dict) -> ProviderConfig:
    def require(field_name: str) -> str:
        val = item.get(field_name)
        if not val:
            raise ConfigError(f"providers[{idx}].{field_name} 不能为空")
        return str(val)

    name = require("name")
    protocol = require("protocol")
    api_key = require("api_key")
    model = require("model")

    if protocol not in ("anthropic", "openai", "deepseek"):
        raise ConfigError(
            f"providers[{idx}].protocol 非法：{protocol!r}（支持 anthropic / openai / deepseek）"
        )

    return ProviderConfig(
        name=name,
        protocol=protocol,  # type: ignore[arg-type]
        api_key=api_key,
        model=model,
        base_url=item.get("base_url") or None,
        thinking=bool(item.get("thinking", False)),
        context_window=int(item.get("context_window", 0)),
    )


def effective_context_window(p: ProviderConfig) -> int:
    """返回 provider 的有效上下文窗口大小。

    配置值 > 0 时优先使用；否则按 protocol 给默认值。
    """
    if p.context_window > 0:
        return p.context_window
    if p.protocol == "anthropic":
        return DEFAULT_ANTHROPIC_CONTEXT_WINDOW
    if p.protocol == "openai":
        return DEFAULT_OPENAI_CONTEXT_WINDOW
    return DEFAULT_ANTHROPIC_CONTEXT_WINDOW
