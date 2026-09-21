"""笔记文件存储：单级目录的笔记 CRUD 与 MEMORY.md 索引管理 — Claude Code 格式。"""

from __future__ import annotations

import logging
import os
import threading

import yaml

from suisuicode.memory.types import UpdateAction

logger = logging.getLogger(__name__)

INDEX_FILENAME = "MEMORY.md"
# Claude Code 索引格式: `- [<description>](<filename>) — <hook>`
INDEX_LINE_FMT = "- [{description}]({filename}) — {hook}\n"


class Store:
    """管理单级（项目级或用户级）的笔记文件和索引。"""

    def __init__(self, dir: str) -> None:
        self._dir = dir
        self._lock = threading.Lock()

    def init(self) -> None:
        """初始化存储目录和空的 MEMORY.md（幂等）。

        启动时调用，让用户能感知 memory 功能已就绪。
        已有目录和文件时不做任何更改。
        """
        os.makedirs(self._dir, exist_ok=True)
        index_path = os.path.join(self._dir, INDEX_FILENAME)
        if not os.path.isfile(index_path):
            with open(index_path, "w", encoding="utf-8") as f:
                f.write("")

    def ensure_dir(self) -> None:
        """创建目录（幂等）。"""
        os.makedirs(self._dir, exist_ok=True)

    def load_index(self) -> str:
        """读取 MEMORY.md 内容；不存在返回空字符串。"""
        index_path = os.path.join(self._dir, INDEX_FILENAME)
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return ""
        except OSError:
            return ""

    def load_all_notes(self) -> dict[str, str]:
        """读取所有笔记文件的完整内容，返回 {filename: content}。

        MEMORY.md 本身不包含在内。
        """
        notes: dict[str, str] = {}
        try:
            for entry in os.scandir(self._dir):
                if not entry.is_file():
                    continue
                name = os.path.basename(entry.path)
                if name == INDEX_FILENAME or not name.endswith(".md"):
                    continue
                try:
                    with open(entry.path, "r", encoding="utf-8") as f:
                        notes[name] = f.read()
                except OSError:
                    continue
        except OSError:
            return {}
        return notes

    def apply(self, actions: list[UpdateAction]) -> None:
        """执行 create/update/delete 操作。

        所有操作在锁内串行，防止连续更新的读写冲突。
        """
        with self._lock:
            self.ensure_dir()
            for action in actions:
                try:
                    if action.action == "create":
                        self._do_create(action)
                    elif action.action == "update":
                        self._do_update(action)
                    elif action.action == "delete":
                        self._do_delete(action)
                except Exception:
                    logger.exception(
                        "记忆操作失败: action=%s filename=%s",
                        action.action,
                        action.filename or action.name,
                    )

    # ── 内部操作 ────────────────────────────────────

    def _do_create(self, action: UpdateAction) -> None:
        """创建新笔记文件和索引行。"""
        filename = f"{action.name}.md"
        filepath = os.path.join(self._dir, filename)

        content = _build_note_file(
            name=action.name,
            description=action.description,
            note_type=action.type,
            body=action.content,
        )

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        self._append_index(action.description, action.content, filename)

    def _do_update(self, action: UpdateAction) -> None:
        """更新已有笔记文件的内容和 frontmatter。"""
        filepath = os.path.join(self._dir, action.filename)

        # 读取旧 frontmatter，保留未提供的新值
        old_name = action.name
        old_desc = action.description
        old_type = action.type or ""
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                old = _parse_note_file(f.read())
            if old:
                old_fm = old[0]
                if old_fm.get("name") and not old_name:
                    old_name = old_fm["name"]
                if old_fm.get("description") and not old_desc:
                    old_desc = old_fm["description"]
                if old_fm.get("metadata", {}).get("type") and not old_type:
                    old_type = old_fm["metadata"]["type"]
        except (OSError, FileNotFoundError):
            pass

        content = _build_note_file(
            name=old_name,
            description=old_desc,
            note_type=old_type,
            body=action.content,
        )

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        self._update_index_line(
            action.filename, old_desc, action.content
        )

    def _do_delete(self, action: UpdateAction) -> None:
        """删除笔记文件和索引对应行。"""
        filepath = os.path.join(self._dir, action.filename)
        try:
            os.remove(filepath)
        except FileNotFoundError:
            pass

        self._remove_index_line(action.filename)

    # ── 索引操作 ────────────────────────────────────

    def _index_path(self) -> str:
        return os.path.join(self._dir, INDEX_FILENAME)

    def _append_index(
        self, description: str, content: str, filename: str
    ) -> None:
        """在 MEMORY.md 尾部追加一行。"""
        hook = _make_hook(content)
        line = INDEX_LINE_FMT.format(
            description=description, filename=filename, hook=hook
        )
        with open(self._index_path(), "a", encoding="utf-8") as f:
            f.write(line)

    def _update_index_line(
        self, filename: str, description: str, content: str
    ) -> None:
        """更新 MEMORY.md 中对应笔记的行（按 filename 匹配）。"""
        index = self.load_index()
        lines = index.splitlines()
        new_lines = []
        updated = False
        hook = _make_hook(content)
        new_line = INDEX_LINE_FMT.format(
            description=description, filename=filename, hook=hook
        ).rstrip("\n")
        for line in lines:
            if filename in line:
                new_lines.append(new_line)
                updated = True
            else:
                new_lines.append(line)
        if not updated:
            new_lines.append(new_line)
        with open(self._index_path(), "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")

    def _remove_index_line(self, filename: str) -> None:
        """从 MEMORY.md 移除包含 filename 的行。"""
        index = self.load_index()
        lines = index.splitlines()
        new_lines = [line for line in lines if filename not in line]
        with open(self._index_path(), "w", encoding="utf-8") as f:
            f.write("\n".join(new_lines) + "\n")


# ── 辅助函数 ──────────────────────────────────────


def _build_note_file(name: str, description: str, note_type: str, body: str) -> str:
    """拼装 Claude Code 格式的笔记文件（YAML frontmatter + 正文）。"""
    frontmatter = {
        "name": name,
        "description": description,
        "metadata": {"type": note_type},
    }
    fm = yaml.safe_dump(
        frontmatter, allow_unicode=True, default_flow_style=False
    ).strip()
    return f"---\n{fm}\n---\n\n{body}\n"


def _parse_note_file(text: str) -> tuple[dict, str] | None:
    """解析带 YAML frontmatter 的笔记文件，返回 (frontmatter_dict, body)。"""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        fm = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    return (fm or {}, parts[2].strip())


def _make_hook(content: str, max_len: int = 80) -> str:
    """从笔记正文提取 hook（用于 MEMORY.md 索引行）。"""
    text = content.strip()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    hook = lines[0] if lines else text
    if len(hook) > max_len:
        hook = hook[: max_len - 3] + "..."
    return hook
