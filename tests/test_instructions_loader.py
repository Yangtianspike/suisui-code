"""ch09 项目指令加载器测试：三层加载、@include 展开、深度/环路/逃逸检测。"""

import os
import tempfile

from suisuicode.instructions.loader import Loader, load_instructions


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def test_load_empty_when_no_files():
    """三个路径都没有 SUISUICODE.md → 返回空字符串。"""
    with tempfile.TemporaryDirectory() as tmp:
        loader = Loader(project_root=tmp, user_home=tmp)
        result = loader.load()
        assert result == ""


def test_load_single_file():
    """只有项目根有 SUISUICODE.md → 返回其内容。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "SUISUICODE.md"), "项目规范：使用中文")
        loader = Loader(project_root=tmp, user_home=tmp)
        result = loader.load()
        assert "项目规范：使用中文" in result


def test_load_three_layers():
    """三个路径各放不同内容 → 三份内容都存在且项目根在前。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "SUISUICODE.md"), "PROJECT_ROOT")
        _write(os.path.join(tmp, ".suisuicode", "SUISUICODE.md"), "PROJECT_CONFIG")
        user_home = os.path.join(tmp, "user_home")
        _write(os.path.join(user_home, ".suisuicode", "SUISUICODE.md"), "USER_LEVEL")

        loader = Loader(project_root=tmp, user_home=user_home)
        result = loader.load()
        # 项目根在最前面
        assert result.index("PROJECT_ROOT") < result.index("PROJECT_CONFIG")
        assert result.index("PROJECT_CONFIG") < result.index("USER_LEVEL")


def test_missing_files_silent():
    """缺失文件静默跳过，不报错。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "SUISUICODE.md"), "only_project")
        loader = Loader(project_root=tmp, user_home=tmp)
        result = loader.load()
        assert result == "only_project"


def test_include_expand():
    """@include 正常展开：引用文件内容替换该行。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "SUISUICODE.md"), "开头\n@include rules/style.md\n结尾")
        _write(os.path.join(tmp, "rules", "style.md"), "STYLE_CONTENT")

        loader = Loader(project_root=tmp, user_home=tmp)
        result = loader.load()
        assert "STYLE_CONTENT" in result
        assert "@include" not in result
        assert "开头" in result
        assert "结尾" in result


def test_include_nested():
    """嵌套 @include：A include B，B include C → 全部展开。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "SUISUICODE.md"), "@include a.md")
        _write(os.path.join(tmp, "a.md"), "@include sub/b.md")
        _write(os.path.join(tmp, "sub", "b.md"), "DEEP_CONTENT")

        loader = Loader(project_root=tmp, user_home=tmp)
        result = loader.load()
        assert "DEEP_CONTENT" in result


def test_include_depth_limit():
    """超过 max_depth 层不展开，出现深度警告。"""
    with tempfile.TemporaryDirectory() as tmp:
        # 构造链: SUISUICODE.md → l1 → l2 → l3 → l4 → l5 → l6
        _write(os.path.join(tmp, "SUISUICODE.md"), "@include l1.md")
        _write(os.path.join(tmp, "l1.md"), "@include l2.md")
        _write(os.path.join(tmp, "l2.md"), "@include l3.md")
        _write(os.path.join(tmp, "l3.md"), "@include l4.md")
        _write(os.path.join(tmp, "l4.md"), "@include l5.md")
        _write(os.path.join(tmp, "l5.md"), "@include l6.md")
        _write(os.path.join(tmp, "l6.md"), "TOO_DEEP")

        loader = Loader(project_root=tmp, user_home=tmp, max_depth=5)
        result = loader.load()
        # 第 6 层不展开
        assert "TOO_DEEP" not in result
        assert "超过最大嵌套深度" in result


def test_include_cycle_detection():
    """环路检测：A include B、B include A → 第二次不展开。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "SUISUICODE.md"), "@include a.md")
        _write(os.path.join(tmp, "a.md"), "@include b.md")
        _write(os.path.join(tmp, "b.md"), "@include a.md\nCONTENT_B")

        loader = Loader(project_root=tmp, user_home=tmp)
        result = loader.load()
        assert "检测到环路" in result
        assert "CONTENT_B" in result


def test_include_path_escape():
    """项目级 SUISUICODE.md 中 @include 跳出项目根 → 不加载。"""
    with tempfile.TemporaryDirectory() as tmp:
        proj = os.path.join(tmp, "project")
        outside = os.path.join(tmp, "outside")
        os.makedirs(proj)
        os.makedirs(outside)
        _write(os.path.join(outside, "secret.md"), "SECRET")
        _write(
            os.path.join(proj, "SUISUICODE.md"),
            f"@include {outside}{os.sep}secret.md",
        )

        loader = Loader(project_root=proj, user_home=tmp)
        result = loader.load()
        assert "SECRET" not in result
        assert "路径超出允许范围" in result


def test_include_missing_file_silent():
    """@include 指向不存在的文件 → 静默跳过。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "SUISUICODE.md"), "@include notfound.md\nCONTENT")

        loader = Loader(project_root=tmp, user_home=tmp)
        result = loader.load()
        assert "CONTENT" in result


def test_binary_file_skip():
    """@include 指向二进制文件 → 跳过并警告。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "SUISUICODE.md"), "@include data.bin")
        with open(os.path.join(tmp, "data.bin"), "wb") as f:
            f.write(b"\x00\x01\x02\x03")

        loader = Loader(project_root=tmp, user_home=tmp)
        result = loader.load()
        assert "二进制文件" in result


def test_non_standalone_include_preserved():
    """不在独占行上的 @include 不做替换，保持原文。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write(
            os.path.join(tmp, "SUISUICODE.md"),
            "段落中 @include something 不展开",
        )

        loader = Loader(project_root=tmp, user_home=tmp)
        result = loader.load()
        assert "@include something" in result


def test_load_instructions_convenience():
    """load_instructions 便捷函数正常工作。"""
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "SUISUICODE.md"), "convenience test")
        result = load_instructions(tmp)
        assert "convenience test" in result
