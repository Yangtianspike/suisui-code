from __future__ import annotations

import datetime
import os
import platform
import subprocess
from dataclasses import dataclass


@dataclass
class Environment:
    """运行环境信息——不缓存，每轮请求动态渲染。"""

    working_dir: str
    platform: str
    date: str
    git_status: str  # git 状态摘要；非 git 目录/git 不可用时为空
    version: str
    model: str

    def render(self) -> str:
        """渲染为「环境信息」段文本——逐行 Key: Value，空值项省略。"""
        lines: list[str] = ["环境信息："]
        if self.working_dir:
            lines.append(f"  工作目录：{self.working_dir}")
        if self.platform:
            lines.append(f"  平台：{self.platform}")
        if self.date:
            lines.append(f"  当前日期：{self.date}")
        if self.git_status:
            lines.append(f"  Git 状态：{self.git_status}")
        if self.version:
            lines.append(f"  应用版本：{self.version}")
        if self.model:
            lines.append(f"  当前模型：{self.model}")
        return "\n".join(lines)


def gather_git_branch() -> str:
    """采集当前 git 分支名。失败返回空。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if result.returncode != 0:
            return ""
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def _gather_git_status() -> str:
    """采集 git 状态摘要。失败/非 git 目录时返回空。"""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if result.returncode != 0:
            return ""
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        if not lines:
            return "clean"
        if len(lines) <= 5:
            return "; ".join(lines[:5])
        return f"{'; '.join(lines[:5])}; … and {len(lines) - 5} more"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""


def gather_environment(version: str, model: str) -> Environment:
    """采集运行环境信息。

    各字段独立安全：异常项降级留空，不中断会话。
    """
    try:
        wd = os.getcwd()
    except OSError:
        wd = ""

    try:
        plat = platform.system().lower()
    except Exception:
        plat = ""

    try:
        today = datetime.date.today().isoformat()
    except Exception:
        today = ""

    git = _gather_git_status()

    return Environment(
        working_dir=wd,
        platform=plat,
        date=today,
        git_status=git,
        version=version,
        model=model,
    )
