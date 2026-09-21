"""ch09 Memory 子包测试：Store CRUD、Manager 索引加载、异步更新 — Claude Code 格式。"""

import os
import tempfile

from suisuicode.memory.store import Store
from suisuicode.memory.manager import Manager
from suisuicode.memory.types import UpdateAction


def test_store_create_note():
    """apply create → 文件存在、frontmatter 正确、MEMORY.md 有对应行。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp)
        store.apply(
            [
                UpdateAction(
                    action="create",
                    level="project",
                    name="terse-replies",
                    description="用户偏好简洁回复",
                    type="user",
                    content="用户偏好简洁回复，每次完成后不要在结尾重述刚做了什么。",
                )
            ]
        )

        # 文件存在（文件名 = name.md）
        note_path = os.path.join(tmp, "terse-replies.md")
        assert os.path.exists(note_path)

        # 读取 frontmatter — Claude Code 格式
        with open(note_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "name: terse-replies" in content
        assert "description: 用户偏好简洁回复" in content
        assert "metadata:" in content
        assert "type: user" in content
        assert "---" in content

        # 索引格式: - [<description>](<filename>) — <hook>
        index = store.load_index()
        assert "terse-replies.md" in index
        assert "用户偏好简洁回复" in index


def test_store_update_note():
    """apply update → 文件内容更新、MEMORY.md 对应行更新。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp)

        # 先创建
        store.apply(
            [
                UpdateAction(
                    action="create",
                    level="project",
                    name="python-stack",
                    description="项目使用 Python",
                    type="project",
                    content="This project is built with Python.",
                )
            ]
        )

        old_index = store.load_index()
        assert "Python" in old_index

        # 更新
        store.apply(
            [
                UpdateAction(
                    action="update",
                    level="project",
                    filename="python-stack.md",
                    name="python-stack",
                    description="项目使用 Python 3.12+",
                    type="project",
                    content="本项目基于 Python 3.12+ 构建。\n\n**Why:** 用户选择 Python。\n**How to apply:** 使用 Python 3.12+ 语法。",
                )
            ]
        )

        # 文件内容更新
        note_path = os.path.join(tmp, "python-stack.md")
        with open(note_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "Python 3.12+" in content
        assert "name: python-stack" in content

        # 索引更新
        new_index = store.load_index()
        assert "Python 3.12+" in new_index


def test_store_delete_note():
    """apply delete → 文件不存在、MEMORY.md 对应行消失。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp)

        # 先创建
        store.apply(
            [
                UpdateAction(
                    action="create",
                    level="project",
                    name="old-convention",
                    description="旧约定待删除",
                    type="project",
                    content="这条笔记会被删除。",
                )
            ]
        )

        note_path = os.path.join(tmp, "old-convention.md")
        assert os.path.exists(note_path)

        # 删除
        store.apply(
            [
                UpdateAction(
                    action="delete",
                    level="project",
                    filename="old-convention.md",
                )
            ]
        )

        assert not os.path.exists(note_path)
        index = store.load_index()
        assert "旧约定待删除" not in index


def test_manager_load_index():
    """两级各有索引 → 合并返回，项目级在前。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "project_memory")
        user = os.path.join(tmp, "user_memory")
        os.makedirs(proj)
        os.makedirs(user)

        # 写项目级索引 — Claude Code 格式
        with open(os.path.join(proj, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("- [项目使用 RESTful API](api-conventions.md) — 使用 RESTful 风格\n")

        # 写用户级索引
        with open(os.path.join(user, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write("- [用户偏好中文回复](chinese-reply.md) — 使用简体中文\n")

        mgr = Manager(project_dir=proj, user_dir=user)
        result = mgr.load_index()

        assert "RESTful" in result
        assert "中文回复" in result
        # 项目级在前
        assert result.index("RESTful") < result.index("中文")


def test_manager_load_index_truncate():
    """构造超 25KB 索引 → 截断 + (index truncated) 标注。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "project_memory")
        user = os.path.join(tmp, "user_memory")
        os.makedirs(proj)
        os.makedirs(user)

        # 写一个很大的项目级索引 — Claude Code 格式
        big_line = "- [item](item.md) — " + "x" * 80 + "\n"
        big_content = big_line * 400  # 约 40KB
        with open(os.path.join(proj, "MEMORY.md"), "w", encoding="utf-8") as f:
            f.write(big_content)

        mgr = Manager(project_dir=proj, user_dir=user)
        result = mgr.load_index()

        assert "(index truncated)" in result
        assert len(result.encode("utf-8")) <= 25 * 1024 + 100  # 允许少量超出


def test_manager_update_async_no_provider():
    """无 provider 时 update_async 安全返回（不抛异常）。"""
    import asyncio

    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "project_memory")
        user = os.path.join(tmp, "user_memory")
        mgr = Manager(project_dir=proj, user_dir=user, provider=None)
        # 不抛异常
        asyncio.run(mgr.update_async([]))


def test_store_empty_index():
    """新 Store 加载索引返回空字符串。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp)
        assert store.load_index() == ""


def test_store_init_creates_empty_index():
    """init() 创建空的 MEMORY.md（无 header）。"""
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(tmp)
        store.init()
        idx_path = os.path.join(tmp, "MEMORY.md")
        assert os.path.isfile(idx_path)
        with open(idx_path, "r") as f:
            content = f.read()
        # 空文件或仅空白
        assert content.strip() == ""


def test_note_file_frontmatter_format():
    """验证笔记文件的 frontmatter 符合 Claude Code 格式。"""
    from suisuicode.memory.store import _build_note_file, _parse_note_file

    body = _build_note_file(
        name="my-note",
        description="一句话摘要",
        note_type="project",
        body="正文内容。\n\n**Why:** 原因。\n**How to apply:** 怎么做。",
    )

    # 解析回 frontmatter
    result = _parse_note_file(body)
    assert result is not None
    fm, body_text = result
    assert fm["name"] == "my-note"
    assert fm["description"] == "一句话摘要"
    assert fm["metadata"]["type"] == "project"
    assert "正文内容" in body_text
    assert "**Why:**" in body_text
    assert "**How to apply:**" in body_text
