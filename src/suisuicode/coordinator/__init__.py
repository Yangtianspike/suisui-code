"""Coordinator Mode — Lead 工具收窄 + 四阶段提示词。

双锁机制：feature flag (features.coordinator_mode) + 环境变量 (SUISUICODE_COORDINATOR_MODE)。
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from suisuicode.config.config import Config

# ── 白名单 ────────────────────────────────────────────────

COORDINATOR_ALLOWED_TOOLS: list[str] = [
    "Agent",
    "TeamCreate",
    "TeamDelete",
    "TaskCreate",
    "TeamTaskGet",
    "TeamTaskList",
    "TaskUpdate",
    "TeamSendMessage",
    "read_file",
    "glob",
    "grep",
    "bash",
]

# ── 系统提示词后缀 ──────────────────────────────────────

COORDINATOR_SYSTEM_PROMPT_SUFFIX = """
## Coordinator Mode

你是团队的协调者（Lead），负责将任务拆解、分派给队员、收集结果并合并。
你的工作分四个阶段：

### 阶段 1: Research（调研）
- 用 read_file / glob / grep 了解代码库当前状态
- 识别需要改动的模块和文件

### 阶段 2: Synthesis（综合规划）
- 根据调研结果制定分派计划
- 为每个队员创建 Task（TaskCreate），写好清晰的指令
- 用 Agent 工具派出队员执行各自的任务

### 阶段 3: Wait（等待汇报）
- 用 SendMessage 给队员发具体指令后，**停手等汇报**
- **禁止**在派出队员后立刻自己调 read_file / glob / grep / bash 探索
- **禁止**用 sleep / TaskList 轮询凑时间
- 队员完成工作后你会收到通知，那时再继续

### 阶段 4: Verification（验证与合并）
- 读队员产出的报告文件（read_file）
- 用 bash 跑 git diff / git status 等收敛操作
- 用 bash 跑 git merge 合并各队员的 worktree 分支
- 冲突用 read_file + edit_file（非 Coordinator Mode）或 bash 解决

你是协调者而非执行者——你的力量来自队员的并行工作。
"""


# ── 检测函数 ──────────────────────────────────────────────


def is_enabled(cfg: Config) -> bool:
    """双锁检测：feature flag 开 + 环境变量为真。"""
    try:
        features = getattr(cfg, "features", None)
        if features is None:
            return False
        if not getattr(features, "coordinator_mode", False):
            return False
    except Exception:
        return False

    env_val = os.environ.get("SUISUICODE_COORDINATOR_MODE", "")
    return env_truthy(env_val)


def allowed_tools() -> list[str]:
    """返回 Coordinator Mode 允许的工具名列表。"""
    return list(COORDINATOR_ALLOWED_TOOLS)


def system_prompt_suffix() -> str:
    """返回 Coordinator 系统提示词后缀。"""
    return COORDINATOR_SYSTEM_PROMPT_SUFFIX


def env_truthy(v: str) -> bool:
    """判断环境变量值是否为"真"（大小写不敏感）。"""
    return v.lower() in {"1", "true", "yes"}
