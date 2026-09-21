"""InstallSkill 工具：从 URL 远程安装 Skill。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from suisuicode.tool import Result, Tool

if TYPE_CHECKING:
    from suisuicode.skills.catalog import Catalog

logger = logging.getLogger(__name__)

INSTALL_ROOT = "~/.suisuicode/skills"


class InstallSkillTool(Tool):
    """从 GitHub URL 安装 Skill 到用户级目录。"""

    is_system_tool = False

    def __init__(self, catalog: "Catalog | None" = None, work_dir: str = ".") -> None:
        self._catalog = catalog
        self._work_dir = work_dir

    def set_catalog(self, catalog: "Catalog") -> None:
        self._catalog = catalog

    def name(self) -> str:
        return "InstallSkill"

    def description(self) -> str:
        return (
            "从 GitHub URL 远程安装一个 Skill。"
            "支持 github.com tree URL 或 raw.githubusercontent.com URL。"
            "安装后的 skill 自动可用，无需重启。"
            "限额：单文件 ≤1 MiB，总 ≤8 MiB，≤64 文件，≤4 层目录。"
        )

    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "Skill 的 GitHub URL。"
                        "格式：https://github.com/<owner>/<repo>/tree/<ref>/<path>"
                    ),
                },
            },
            "required": ["url"],
        }

    @property
    def read_only(self) -> bool:
        return False  # 写盘 + 网络，必须经过权限检查

    async def execute(self, args: str) -> Result:
        try:
            params = json.loads(args)
        except json.JSONDecodeError:
            return Result(is_error=True, content="InstallSkill 参数必须是有效 JSON")

        url = params.get("url")
        if not url or not isinstance(url, str):
            return Result(is_error=True, content="InstallSkill 缺少必填参数: url")

        try:
            from suisuicode.skills.install import install_from_url

            install_root = Path(INSTALL_ROOT).expanduser()
            work_dir = Path(self._work_dir).resolve() if self._work_dir else Path.cwd()

            skill_name = await install_from_url(
                url,
                install_root,
                catalog=self._catalog,
                work_dir=work_dir,
            )
            return Result(
                content=(
                    f"Skill '{skill_name}' installed successfully to "
                    f"{install_root / skill_name}.\n"
                    f"使用 /skill list 查看，/{skill_name} 调用。"
                )
            )
        except ValueError as e:
            return Result(is_error=True, content=str(e))
        except Exception as e:
            logger.warning("InstallSkill 失败: %s", e, exc_info=True)
            return Result(is_error=True, content=f"安装失败: {e}")
