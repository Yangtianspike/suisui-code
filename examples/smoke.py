#!/usr/bin/env python3
"""系统提示工程化 — 缓存命中端到端验证。

连发两条消息，观察首轮 cache_write > 0、次轮 cache_read > 0，
证明稳定前缀被缓存复用。

用法：
    python examples/smoke.py                        # 使用第一个 anthropic provider
    python examples/smoke.py --any                  # 使用第一个任意 provider
    python examples/smoke.py --config path/to.yaml  # 指定配置路径
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from suisuicode import __version__
from suisuicode.agent import Agent
from suisuicode.config import load as load_config
from suisuicode.conversation import Conversation
from suisuicode.llm import new_provider
from suisuicode.permission.engine import new_engine
from suisuicode.tool import new_default_registry


def _fmt(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _print_usage(label: str, usage: object) -> None:
    u = usage
    parts = [
        f"input={_fmt(u.input_tokens)}",
        f"output={_fmt(u.output_tokens)}",
        f"cache_write={_fmt(u.cache_write)}",
        f"cache_read={_fmt(u.cache_read)}",
    ]
    print(f"  [{label}]  {' | '.join(parts)}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="System prompt caching smoke test")
    parser.add_argument(
        "--config",
        default="~/.suisuicode/config.yaml",
        help="Config file path (default: ~/.suisuicode/config.yaml)",
    )
    parser.add_argument(
        "--any",
        action="store_true",
        dest="any_provider",
        help="Use any provider (not just anthropic)",
    )
    args = parser.parse_args()

    # ── 加载配置 ────────────────────────────────────────
    try:
        cfg = load_config(args.config)
    except Exception as e:
        print(f"✗ 配置加载失败: {e}")
        sys.exit(1)

    if not cfg.providers:
        print("✗ 配置中没有 provider")
        sys.exit(1)

    # 优先用 anthropic（缓存断点验证），否则回退
    provider_cfg = None
    if not args.any_provider:
        for p in cfg.providers:
            if p.protocol == "anthropic":
                provider_cfg = p
                break
    if provider_cfg is None:
        provider_cfg = cfg.providers[0]

    print(f"Provider: {provider_cfg.name} ({provider_cfg.model})")
    print(f"Protocol: {provider_cfg.protocol}")
    print()

    # ── 创建 provider / registry / engine / agent ────────
    provider = new_provider(provider_cfg)
    registry = new_default_registry()
    cwd = str(Path.cwd().resolve())
    engine, _ = new_engine(cwd)
    agent = Agent(provider, registry, engine, version=__version__)

    # ── 第一轮 ──────────────────────────────────────────
    conv = Conversation()
    conv.add_user("你好，请用一句话介绍你自己。")

    print("=== Round 1 (首次请求，期待 cache_write > 0) ===")
    async for ev in agent.run(conv):
        if ev.usage is not None:
            _print_usage("usage", ev.usage)
        if ev.err is not None:
            print(f"  [error] {ev.err}")
        if ev.done:
            print("  [done]")
    print()

    # ── 第二轮 ──────────────────────────────────────────
    conv.add_user("继续，再说一句。")

    print("=== Round 2 (第二次请求，期待 cache_read > 0) ===")
    async for ev in agent.run(conv):
        if ev.usage is not None:
            _print_usage("usage", ev.usage)
        if ev.err is not None:
            print(f"  [error] {ev.err}")
        if ev.done:
            print("  [done]")
    print()

    # ── 总结 ────────────────────────────────────────────
    print("=== 验证结果 ===")
    print("  ✓ 如果首轮 cache_write > 0，缓存已创建")
    print("  ✓ 如果次轮 cache_read > 0，缓存已命中")
    print("  ✓ 如果两轮都无缓存字段，说明端点不支持缓存")


if __name__ == "__main__":
    asyncio.run(main())
