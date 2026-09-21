"""项目指令文件（SUISUICODE.md）三层加载与 @include 展开。"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# @include 独占行正则：行首可含空白，只匹配 @include 后跟一个空格再接路径
_INCLUDE_RE = re.compile(r"^\s*@include\s+(.+)$")

# 警告注释模板
WARN_DEPTH = "<!-- @include 超过最大嵌套深度，已跳过: {path} -->"
WARN_CYCLE = "<!-- @include 检测到环路，已跳过: {path} -->"
WARN_ESCAPE = "<!-- @include 路径超出允许范围，已跳过: {path} -->"
WARN_BINARY = "<!-- @include 指向二进制文件，已跳过: {path} -->"


@dataclass
class Loader:
    """按优先级加载三层 SUISUICODE.md，处理 @include 展开。

    三个加载路径（优先级从高到低）：
    ① <project_root>/SUISUICODE.md          — 项目级
    ② <project_root>/.suisuicode/SUISUICODE.md — 项目配置级
    ③ ~/.suisuicode/SUISUICODE.md              — 用户级
    """

    project_root: str
    user_home: str | None = None
    max_depth: int = 5

    def __post_init__(self) -> None:
        if self.user_home is None:
            self.user_home = os.path.expanduser("~")

    # ── 公开接口 ─────────────────────────────────────

    def load(self) -> str:
        """按优先级加载三层指令文件，返回拼接后的完整指令文本。

        加载失败的层静默跳过，全部为空返回空字符串。
        """
        parts: list[str] = []

        # ① 项目根
        p1 = os.path.join(self.project_root, "SUISUICODE.md")
        boundary1 = os.path.realpath(self.project_root)
        content1 = self._load_file(p1, boundary1, depth=1, visited=set())
        if content1:
            parts.append(content1)

        # ② 项目 .suisuicode/
        p2 = os.path.join(self.project_root, ".suisuicode", "SUISUICODE.md")
        content2 = self._load_file(p2, boundary1, depth=1, visited=set())
        if content2:
            parts.append(content2)

        # ③ 用户 ~/.suisuicode/
        p3 = os.path.join(self.user_home, ".suisuicode", "SUISUICODE.md")
        boundary3 = os.path.realpath(os.path.join(self.user_home, ".suisuicode"))
        content3 = self._load_file(p3, boundary3, depth=1, visited=set())
        if content3:
            parts.append(content3)

        return "\n\n".join(parts)

    # ── 内部实现 ─────────────────────────────────────

    def _load_file(
        self,
        path: str,
        boundary: str,
        depth: int,
        visited: set[str],
    ) -> str:
        """加载单个文件，递归展开 @include。

        Args:
            path: 文件路径
            boundary: 路径逃逸检测的根边界
            depth: 当前嵌套层数（从 1 开始）
            visited: 环路检测集合（已解析为绝对路径的文件）
        """
        # 检查嵌套深度
        if depth > self.max_depth:
            return WARN_DEPTH.format(path=path)

        # 解析绝对路径
        try:
            abs_path = self._resolve_include(path, boundary)
        except OSError:
            return ""  # 文件不存在，静默跳过

        # 环路检测
        if abs_path in visited:
            return WARN_CYCLE.format(path=path)

        # 路径逃逸检测
        if not self._is_within(abs_path, boundary):
            return WARN_ESCAPE.format(path=path)

        # 读取文件
        try:
            with open(abs_path, "rb") as f:
                raw = f.read()
        except OSError:
            return ""  # 文件不可读，静默跳过

        # 二进制检测
        if b"\x00" in raw[:512]:
            return WARN_BINARY.format(path=path)

        try:
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            return WARN_BINARY.format(path=path)

        # 逐行处理 @include
        new_visited = visited | {abs_path}
        lines = text.splitlines()
        result_lines: list[str] = []

        for line in lines:
            m = _INCLUDE_RE.match(line)
            if m:
                include_target = m.group(1).strip()
                if include_target:
                    # 解析 @include 的路径（相对于当前文件所在目录）
                    current_dir = os.path.dirname(abs_path)
                    try:
                        expanded = self._resolve_include(include_target, current_dir)
                    except OSError:
                        # include 的文件不存在，静默跳过
                        continue
                    # 递归展开
                    replacement = self._load_file(
                        expanded, boundary, depth + 1, new_visited
                    )
                    result_lines.append(replacement)
                else:
                    result_lines.append(line)
            else:
                result_lines.append(line)

        return "\n".join(result_lines)

    # ── 辅助方法 ─────────────────────────────────────

    @staticmethod
    def _resolve_include(target: str, base_dir: str) -> str:
        """解析 @include 目标为绝对路径。

        如果 target 是绝对路径直接使用（需逃逸检测），
        否则相对于 base_dir 解析。
        """
        p = Path(target)
        if p.is_absolute():
            return os.path.realpath(str(p))
        return os.path.realpath(os.path.join(base_dir, target))

    @staticmethod
    def _is_within(abs_path: str, boundary: str) -> bool:
        """检查 abs_path 是否在 boundary 目录范围内。"""
        try:
            Path(abs_path).relative_to(boundary)
            return True
        except ValueError:
            return False


def load_instructions(project_root: str) -> str:
    """便捷函数：加载项目指令文本。"""
    loader = Loader(project_root=project_root)
    return loader.load()
