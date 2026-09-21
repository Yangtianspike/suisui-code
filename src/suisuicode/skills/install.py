"""Skill 远程安装：GitHub URL → 下载 SKILL.md + 资源 → 解压到 ~/.suisuicode/skills/。"""

from __future__ import annotations

import logging
import re
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# 限额常量
MAX_FILE_SIZE = 1 * 1024 * 1024  # 单文件 ≤ 1 MiB
MAX_TOTAL_SIZE = 8 * 1024 * 1024  # 总大小 ≤ 8 MiB
MAX_FILE_COUNT = 64  # 文件数 ≤ 64
MAX_RECURSION_DEPTH = 4  # 目录深度 ≤ 4

# URL 模式
_GITHUB_TREE_RE = re.compile(r"^https?://github\.com/([^/]+/[^/]+)/tree/([^/]+)/(.+)$")
_RAW_GITHUB_RE = re.compile(
    r"^https?://raw\.githubusercontent\.com/([^/]+/[^/]+)/([^/]+)/(.+)$"
)
_SKILLS_SH_RE = re.compile(r"^https?://([^/]+\.)?skills\.sh/(.+)$")


def parse_skill_url(url: str) -> tuple[str, str, str]:
    """解析 Skill 安装 URL，返回 (owner_repo, ref, path)。

    支持三种格式：
    - github.com/<owner>/<repo>/tree/<ref>/<path>
    - raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>
    - skills.sh/<path>  (未来扩展)

    Raises:
        ValueError: 不支持的 URL 格式
    """
    url = url.strip()

    m = _GITHUB_TREE_RE.match(url)
    if m:
        return m.group(1), m.group(2), m.group(3)

    m = _RAW_GITHUB_RE.match(url)
    if m:
        return m.group(1), m.group(2), m.group(3)

    m = _SKILLS_SH_RE.match(url)
    if m:
        # skills.sh 暂时不支持，占位
        raise ValueError("skills.sh URL 暂不支持，请使用 GitHub tree 或 raw URL")

    raise ValueError(
        f"不支持的 Skill URL 格式: {url}\n"
        f"支持的格式：\n"
        f"  - https://github.com/<owner>/<repo>/tree/<ref>/<path>\n"
        f"  - https://raw.githubusercontent.com/<owner>/<repo>/<ref>/<path>"
    )


async def install_from_url(
    source: str,
    install_root: Path,
    *,
    catalog=None,
    work_dir=None,
) -> str:
    """从 GitHub URL 下载 Skill 并安装到 install_root 下。

    Returns:
        安装后的 skill 名称（顶层目录名）

    Raises:
        ValueError: URL 格式错误、限额超限、无 SKILL.md
        RuntimeError: 下载或文件操作失败
    """
    import httpx

    owner_repo, ref, path = parse_skill_url(source)
    top_dir_name = Path(path).name if "/" not in path else path.split("/")[-1]
    if not top_dir_name:
        top_dir_name = owner_repo.split("/")[-1]

    install_root = Path(install_root).expanduser().resolve()
    install_root.mkdir(parents=True, exist_ok=True)

    # 暂存到临时目录
    staging = Path(tempfile.mkdtemp(prefix="suisuicode-skill-", dir=install_root.parent))
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            await _download_tree(
                client,
                owner_repo,
                ref,
                path,
                staging,
                0,
                total_size=0,
                file_count=0,
            )

        # 验证含 SKILL.md
        skill_md = staging / "SKILL.md"
        if not skill_md.is_file():
            # 检查子目录
            found = list(staging.rglob("SKILL.md"))
            if not found:
                shutil.rmtree(staging, ignore_errors=True)
                raise ValueError("下载的目录中未找到 SKILL.md，拒绝安装")
            # 使用第一个找到的 SKILL.md 所在目录
            staging = found[0].parent

        # atomic rename 到位
        target = install_root / top_dir_name
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(staging), str(target))

        logger.info("Skill %r installed to %s", top_dir_name, target)

        # 触发 reload
        if catalog is not None and work_dir is not None:
            catalog.reload(work_dir)

        return top_dir_name

    except Exception:
        # 清理 staging
        shutil.rmtree(staging, ignore_errors=True)
        raise


async def _download_tree(
    client,
    owner_repo: str,
    ref: str,
    path: str,
    dest: Path,
    depth: int,
    total_size: int = 0,
    file_count: int = 0,
) -> tuple[int, int]:
    """递归下载 GitHub 目录树。

    Returns:
        (total_size, file_count) 更新后的值
    """
    if depth > MAX_RECURSION_DEPTH:
        raise ValueError(f"目录深度超过限制 ({MAX_RECURSION_DEPTH})")

    api_url = f"https://api.github.com/repos/{owner_repo}/contents/{path}?ref={ref}"
    headers = {"Accept": "application/vnd.github.v3+json"}

    resp = await client.get(api_url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"GitHub API 请求失败: {resp.status_code} {api_url}")

    items = resp.json()
    if not isinstance(items, list):
        # 单个文件
        items = [items]

    dest.mkdir(parents=True, exist_ok=True)

    for item in items:
        item_type = item.get("type", "file")
        item_name = item.get("name", "")
        item_path = item.get("path", "")

        if item_type == "dir":
            sub_dest = dest / item_name
            total_size, file_count = await _download_tree(
                client,
                owner_repo,
                ref,
                item_path,
                sub_dest,
                depth + 1,
                total_size,
                file_count,
            )
        else:
            file_count += 1
            if file_count > MAX_FILE_COUNT:
                raise ValueError(f"文件数超过限制 ({MAX_FILE_COUNT})")

            size = item.get("size", 0)
            if size > MAX_FILE_SIZE:
                raise ValueError(
                    f"文件 {item_name!r} 大小 {size} 超过限制 ({MAX_FILE_SIZE})"
                )
            total_size += size
            if total_size > MAX_TOTAL_SIZE:
                raise ValueError(f"总大小超过限制 ({MAX_TOTAL_SIZE})")

            download_url = item.get("download_url")
            if not download_url:
                continue

            file_resp = await client.get(download_url)
            if file_resp.status_code != 200:
                logger.warning("下载文件失败 %s: %s", item_name, file_resp.status_code)
                continue

            (dest / item_name).write_bytes(file_resp.content)

    return total_size, file_count
