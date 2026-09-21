"""compact 包测试共享 fixture。"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from suisuicode.llm import Request, StreamEvent, Usage


class FakeCompactProvider:
    """脚本化 fake provider：按调用次数返回预编排的事件序列。"""

    def __init__(self, scripts: list[list[StreamEvent]]):
        self._scripts = scripts
        self._call_count = 0
        self.summarize_calls = 0
        self.stream_calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-model"

    async def stream(self, req: Request) -> AsyncIterator[StreamEvent]:
        self.stream_calls += 1
        if req.tools is None:
            self.summarize_calls += 1

        idx = min(self._call_count, len(self._scripts) - 1)
        self._call_count += 1

        for ev in self._scripts[idx]:
            yield ev


@pytest.fixture
def fake_provider():
    """创建一个脚本化的 fake provider。"""
    def _make(scripts: list[list[StreamEvent]]):
        return FakeCompactProvider(scripts)
    return _make
