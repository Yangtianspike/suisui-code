"""记忆管理器：编排项目级和用户级笔记的加载与异步 LLM 更新。"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING

from suisuicode.memory.prompts import (
    MEMORY_UPDATE_SYSTEM_PROMPT,
    build_memory_update_message,
)
from suisuicode.memory.store import Store
from suisuicode.memory.types import UpdateAction

if TYPE_CHECKING:
    from suisuicode.llm import Provider

logger = logging.getLogger(__name__)

# 索引注入上限
MAX_INDEX_BYTES = 25 * 1024  # 25KB


class Manager:
    """编排项目级和用户级 Store 的笔记加载和异步更新。

    线程安全：update_async 使用 asyncio.Lock 防并发更新。
    """

    def __init__(
        self,
        project_dir: str,
        user_dir: str,
        provider: "Provider | None" = None,
        model: str = "",
    ) -> None:
        self._project_store = Store(project_dir)
        self._user_store = Store(user_dir)
        self._provider = provider
        self._model = model
        self._update_lock = asyncio.Lock()

        # 启动时初始化目录和空的 MEMORY.md（幂等）
        self._project_store.init()
        self._user_store.init()

    # ── 索引加载 ────────────────────────────────────

    def load_index(self) -> str:
        """合并两级索引（项目级在前、用户级在后），截断到 25KB。"""
        project_idx = self._project_store.load_index()
        user_idx = self._user_store.load_index()

        combined = ""
        if project_idx:
            combined += project_idx
        if user_idx:
            if combined and not combined.endswith("\n"):
                combined += "\n"
            combined += user_idx

        # 截断
        encoded = combined.encode("utf-8")
        if len(encoded) > MAX_INDEX_BYTES:
            # 截断到 25KB，保留 "(index truncated)" 空间
            truncated = encoded[: MAX_INDEX_BYTES - 30].decode(
                "utf-8", errors="replace"
            )
            combined = truncated + "\n(index truncated)"

        return combined

    # ── Provider 设置 ───────────────────────────────

    def _load_all_notes(self) -> dict[str, str]:
        """合并两级所有笔记全文，项目级优先。"""
        all_notes: dict[str, str] = {}
        user_notes = self._user_store.load_all_notes()
        proj_notes = self._project_store.load_all_notes()
        # 项目级覆盖用户级同名文件
        all_notes.update(user_notes)
        all_notes.update(proj_notes)
        return all_notes

    # ── 文件列表查询 ───────────────────────────────

    def list_files(self) -> tuple[list[str], list[str]]:
        """列出项目层与用户层 memory 目录下的 .md 文件。

        Returns:
            (project_files, user_files) — 各自按文件名字典序排序。
            目录不存在视为空 list，不抛异常。
        """
        import os as _os

        def _ls(d: str) -> list[str]:
            try:
                names = [
                    n for n in _os.listdir(d)
                    if n.endswith(".md") and _os.path.isfile(_os.path.join(d, n))
                ]
                names.sort()
                return names
            except FileNotFoundError:
                return []
            except OSError:
                logger.warning("列出记忆文件失败: %s", d)
                return []

        return (
            _ls(self._project_store._dir),
            _ls(self._user_store._dir),
        )

    def set_provider(self, provider: "Provider", model: str) -> None:
        """延迟设置 provider（启动时 provider 未选定）。"""
        self._provider = provider
        self._model = model

    # ── 异步更新 ────────────────────────────────────

    async def update_async(self, recent_msgs: list) -> None:
        """异步执行记忆更新。

        不阻塞主会话：在独立 asyncio task 中运行。
        使用 asyncio.Lock 防并发更新（同一时间只有一个更新在跑）。
        任何失败静默记录日志，不上抛。
        """
        if self._provider is None:
            return

        # 防并发更新
        if self._update_lock.locked():
            return

        async with self._update_lock:
            try:
                await self._do_update(recent_msgs)
            except Exception:
                logger.exception("记忆更新失败")

    async def _do_update(self, recent_msgs: list) -> None:
        """执行单次记忆更新流程。"""
        existing_index = self.load_index()
        existing_notes = self._load_all_notes()

        # 构造请求 — 传入笔记全文让 LLM 合并新旧信息
        user_msg = build_memory_update_message(recent_msgs, existing_index, existing_notes)

        from suisuicode.llm import Message, Request, ROLE_USER, System as LlmSystem

        req = Request(
            messages=[Message(role=ROLE_USER, content=user_msg)],
            tools=[],  # 不传工具定义
            system=LlmSystem(stable=MEMORY_UPDATE_SYSTEM_PROMPT, environment=""),
        )

        # 调用 provider
        text_parts: list[str] = []
        try:
            async for ev in self._provider.stream(req):
                if ev.text:
                    text_parts.append(ev.text)
                if ev.err is not None:
                    logger.warning("记忆更新 LLM 请求出错: %s", ev.err)
                    return
                if ev.done:
                    break
        except Exception:
            logger.exception("记忆更新 LLM 调用失败")
            return

        full_text = "".join(text_parts).strip()

        # 解析 JSON
        try:
            actions_raw = json.loads(full_text)
        except json.JSONDecodeError:
            logger.warning("记忆更新 JSON 解析失败: %s", full_text[:200])
            return

        if not isinstance(actions_raw, list):
            return

        # 分发到两级 Store
        project_actions: list[UpdateAction] = []
        user_actions: list[UpdateAction] = []

        for item in actions_raw:
            try:
                action = UpdateAction(
                    action=item.get("action", ""),
                    level=item.get("level", "project"),
                    name=item.get("name", ""),
                    description=item.get("description", ""),
                    type=item.get("type", ""),
                    content=item.get("content", ""),
                    filename=item.get("filename", ""),
                )

                if action.level == "user":
                    user_actions.append(action)
                else:
                    project_actions.append(action)
            except Exception:
                logger.warning("记忆更新操作解析失败: %s", item)

        if project_actions:
            self._project_store.apply(project_actions)
        if user_actions:
            self._user_store.apply(user_actions)
