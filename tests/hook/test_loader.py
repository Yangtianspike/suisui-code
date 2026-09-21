"""T9: hook.Loader 字段校验、加载错误、合并测试。"""

from __future__ import annotations

import textwrap


from suisuicode.hook.loader import load


def _write_hooks(tmp_path, content: str) -> str:
    """在 tmp_path 下写 .suisuicode/hooks.yaml，返回目录路径。"""
    mew_dir = tmp_path / ".suisuicode"
    mew_dir.mkdir()
    hooks_file = mew_dir / "hooks.yaml"
    hooks_file.write_text(textwrap.dedent(content), encoding="utf-8")
    return str(tmp_path)


class TestLoaderBasic:
    def test_no_file_returns_empty_engine(self, tmp_path):
        """无 hooks.yaml 不报错，返回空 Engine。"""
        eng = load(str(tmp_path))
        assert len(eng.rules) == 0
        assert len(eng.sources) == 0

    def test_valid_hooks(self, tmp_path):
        """两条合法 hook 正常加载。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: hook1
                event: SessionStart
                action:
                  type: shell
                  command: "echo hello"
              - name: hook2
                event: Stop
                action:
                  type: prompt
                  text: "done"
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 2
        assert eng.rules[0].name == "hook1"
        assert eng.rules[1].name == "hook2"
        assert len(eng.sources) == 1

    def test_empty_hooks_list(self, tmp_path):
        """hooks 为空列表。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks: []
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 0


class TestLoaderErrors:
    def test_missing_name(self, capsys, tmp_path):
        """name 缺失 → 跳过。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - event: SessionStart
                action:
                  type: shell
                  command: "echo hi"
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 0
        captured = capsys.readouterr()
        assert "name" in captured.err

    def test_unknown_event(self, capsys, tmp_path):
        """未知 event → 跳过。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: bad
                event: UnknownEvent
                action:
                  type: shell
                  command: "echo hi"
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 0
        captured = capsys.readouterr()
        assert "unknown event" in captured.err
        assert "UnknownEvent" in captured.err

    def test_invalid_action_type(self, capsys, tmp_path):
        """action.type 无效 → 跳过。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: bad
                event: SessionStart
                action:
                  type: invalid
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 0
        captured = capsys.readouterr()
        assert "unknown action type" in captured.err

    def test_all_of_and_any_of_conflict(self, capsys, tmp_path):
        """all_of + any_of 同时存在 → 跳过。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: bad
                event: SessionStart
                if:
                  all_of: []
                  any_of: []
                action:
                  type: shell
                  command: "echo hi"
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 0
        captured = capsys.readouterr()
        assert "all_of" in captured.err
        assert "any_of" in captured.err

    def test_async_blocking_event(self, capsys, tmp_path):
        """async + PreToolUse → 跳过。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: bad-async
                event: PreToolUse
                async: true
                action:
                  type: shell
                  command: "echo x"
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 0
        captured = capsys.readouterr()
        assert "async not allowed for blocking events" in captured.err

    def test_invalid_regex_in_condition(self, capsys, tmp_path):
        """条件中非法正则 → 跳过。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: bad-regex
                event: PreToolUse
                if:
                  all_of:
                    - field: tool_name
                      match:
                        type: regex
                        value: "[invalid"
                action:
                  type: shell
                  command: "echo x"
            """,
        )
        eng = load(root)
        capsys.readouterr()
        # Should have skipped due to invalid regex
        assert len(eng.rules) == 0

    def test_name_conflict_across_files(self, capsys, tmp_path, monkeypatch):
        """同名 hook → 保留先到者。"""
        _write_hooks(
            tmp_path,
            """
            hooks:
              - name: dup
                event: SessionStart
                action:
                  type: shell
                  command: "echo first"
            """,
        )
        # 用户级放 tmp_path/sub
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        user_mew = user_dir / ".suisuicode"
        user_mew.mkdir()
        user_mew.joinpath("hooks.yaml").write_text(
            textwrap.dedent(
                """
            hooks:
              - name: dup
                event: SessionEnd
                action:
                  type: shell
                  command: "echo second"
            """
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "pathlib.Path.home", lambda: user_dir
        )
        eng = load(str(tmp_path))
        # 仅保留第一条（项目级）
        assert len(eng.rules) == 1
        assert eng.rules[0].event.value == "SessionStart"
        captured = capsys.readouterr()
        assert "name conflict" in captured.err.lower()

    def test_shell_missing_command(self, capsys, tmp_path):
        """shell 缺 command → 跳过。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: bad-shell
                event: SessionStart
                action:
                  type: shell
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 0
        captured = capsys.readouterr()
        assert "command" in captured.err

    def test_http_missing_url(self, capsys, tmp_path):
        """http 缺 url → 跳过。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: bad-http
                event: SessionStart
                action:
                  type: http
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 0
        captured = capsys.readouterr()
        assert "url" in captured.err

    def test_subagent_missing_fields(self, capsys, tmp_path):
        """subagent 缺 agent_name/prompt → 跳过。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: bad-sub
                event: SessionStart
                action:
                  type: subagent
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 0
        captured = capsys.readouterr()
        assert "agent_name" in captured.err

    def test_invalid_timeout(self, capsys, tmp_path):
        """非法 timeout → 跳过。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: bad-to
                event: SessionStart
                timeout: "not-a-time"
                action:
                  type: shell
                  command: "echo hi"
            """,
        )
        eng = load(root)
        capsys.readouterr()
        assert len(eng.rules) == 0


class TestLoaderMerging:
    def test_skip_invalid_keep_valid(self, tmp_path):
        """非法 hook 被跳过，合法 hook 不受影响。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: bad
                event: UnknownEvent
                action:
                  type: shell
                  command: "echo bad"
              - name: good
                event: SessionStart
                action:
                  type: shell
                  command: "echo good"
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 1
        assert eng.rules[0].name == "good"

    def test_duration_parsing(self, tmp_path):
        """各种 duration 格式正确解析。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: t1
                event: SessionStart
                timeout: "5s"
                action:
                  type: shell
                  command: "echo hi"
              - name: t2
                event: SessionStart
                timeout: "2m"
                action:
                  type: shell
                  command: "echo hi"
              - name: t3
                event: SessionStart
                timeout: "1h"
                action:
                  type: shell
                  command: "echo hi"
              - name: t4
                event: SessionStart
                timeout: 10.5
                action:
                  type: shell
                  command: "echo hi"
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 4
        assert eng.rules[0].timeout_s == 5.0
        assert eng.rules[1].timeout_s == 120.0
        assert eng.rules[2].timeout_s == 3600.0
        assert eng.rules[3].timeout_s == 10.5

    def test_condition_with_not_matcher(self, tmp_path):
        """条件中使用 not 类型的 match。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: not-bash
                event: PreToolUse
                if:
                  all_of:
                    - field: tool_name
                      match:
                        type: not
                        inner:
                          type: exact
                          value: "bash"
                action:
                  type: shell
                  command: "echo not-bash"
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 1

    def test_condition_with_payload_path(self, tmp_path):
        """条件使用嵌套字段路径。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - name: py-files
                event: PreToolUse
                if:
                  all_of:
                    - field: tool_name
                      match:
                        type: exact
                        value: "write_file"
                    - field: tool_input.path
                      match:
                        type: glob
                        value: "**/*.py"
                action:
                  type: shell
                  command: "echo py"
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 1
        # Verify condition structure
        cond = eng.rules[0].condition
        assert cond is not None
        assert len(cond.atoms) == 2
        assert cond.atoms[0].field == "tool_name"
        assert cond.atoms[1].field == "tool_input.path"


# ── Claude Code 风格 ───────────────────────────────────


class TestClaudeCodeStyle:
    def test_id_field(self, tmp_path):
        """id 作为 name 别名。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - id: my-hook
                event: SessionStart
                action:
                  type: shell
                  command: "echo hi"
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 1
        assert eng.rules[0].name == "my-hook"

    def test_once_field(self, tmp_path):
        """once 作为 only_once 别名。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - id: h1
                event: SessionStart
                once: true
                action:
                  type: shell
                  command: "echo hi"
            """,
        )
        eng = load(root)
        assert eng.rules[0].only_once is True

    def test_command_action_type(self, tmp_path):
        """type: command → 内部 shell。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - id: fmt
                event: PostToolUse
                action:
                  type: command
                  command: "black $FILE_PATH"
                async: true
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 1
        assert eng.rules[0].action.type.value == "shell"
        assert eng.rules[0].asyncio_mode is True

    def test_compact_if_string(self, tmp_path):
        """if 字符串 → 解析为 Condition。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - id: block-write
                event: pre_tool_use
                if: 'tool == "WriteFile" && args.path ~= "*.py"'
                action:
                  type: command
                  command: "echo blocked"
                reject: true
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 1
        r = eng.rules[0]
        assert r.event.value == "PreToolUse"  # snake_case → PascalCase
        assert r.reject is True
        assert r.condition is not None
        assert len(r.condition.atoms) == 2

    def test_snake_case_events(self, tmp_path):
        """snake_case 事件名自动映射。"""
        events_snake = [
            "session_start", "session_end", "session_resume",
            "user_prompt_submit", "stop", "pre_user_message",
            "pre_tool_use", "post_tool_use", "pre_compact",
            "post_compact", "notification",
        ]
        items = "\n".join(
            f"              - id: h-{e}\n"
            f"                event: {e}\n"
            f"                action:\n"
            f"                  type: shell\n"
            f"                  command: echo hi"
            for e in events_snake
        )
        content = f"hooks:\n{items}"
        mew_dir = tmp_path / ".suisuicode"
        mew_dir.mkdir()
        hooks_file = mew_dir / "hooks.yaml"
        hooks_file.write_text(content, encoding="utf-8")
        eng = load(str(tmp_path))
        assert len(eng.rules) == 11

    def test_message_field_alias(self, tmp_path):
        """prompt action: message 作为 text 别名。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - id: ctx
                event: session_start
                action:
                  type: prompt
                  message: |
                    - 项目: Python 3.12
                    - 规范: .flake8
                once: true
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 1
        assert eng.rules[0].action.prompt is not None
        assert "Python 3.12" in eng.rules[0].action.prompt.text
        assert eng.rules[0].only_once is True

    def test_reject_on_non_blocking_rejected(self, capsys, tmp_path):
        """reject 在非拦截事件上 → 跳过。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - id: bad-reject
                event: session_start
                reject: true
                action:
                  type: command
                  command: "echo x"
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 0
        captured = capsys.readouterr()
        assert "reject only allowed" in captured.err

    def test_full_claude_code_style(self, tmp_path):
        """完整的 Claude Code 风格 hook 配置。"""
        root = _write_hooks(
            tmp_path,
            """
            hooks:
              - id: auto-format
                event: post_tool_use
                if: 'tool == "WriteFile" && args.path ~= "*.py"'
                action:
                  type: command
                  command: "ruff format $FILE_PATH"
                async: true

              - id: block-vendor
                event: pre_tool_use
                if: 'tool == "WriteFile" && args.path ~= "vendor/*"'
                action:
                  type: command
                  command: "echo vendor 目录由包管理工具管理，请勿手动修改"
                reject: true

              - id: project-context
                event: session_start
                action:
                  type: prompt
                  message: |
                    - 项目信息:
                    - 技术栈: Python 3.12 + FastAPI
                once: true
            """,
        )
        eng = load(root)
        assert len(eng.rules) == 3

        # hook 1: auto-format
        r1 = eng.rules[0]
        assert r1.name == "auto-format"
        assert r1.event.value == "PostToolUse"
        assert r1.asyncio_mode is True
        assert r1.condition is not None
        assert r1.condition.atoms[0].field == "tool_name"

        # hook 2: block-vendor
        r2 = eng.rules[1]
        assert r2.name == "block-vendor"
        assert r2.reject is True

        # hook 3: project-context
        r3 = eng.rules[2]
        assert r3.name == "project-context"
        assert r3.only_once is True
        assert "Python 3.12" in r3.action.prompt.text
