"""记忆更新 prompt 模板 — Claude Code 格式。"""

MEMORY_UPDATE_SYSTEM_PROMPT = """你是一个记忆管理助手。你的任务是从对话中提取值得长期记住的信息，并以结构化操作的形式返回。

## 笔记类型

- `user`：用户信息（角色、专业领域、偏好、习惯）
- `feedback`：用户反馈与纠正（用户指出的错误，你应该记住以便以后避免）。包含 **Why:** 和 **How to apply:**。
- `project`：项目知识（技术栈、代码规范、架构约定、进行中的工作、目标）。包含 **Why:** 和 **How to apply:**。
- `reference`：参考资料（文档链接、API 引用、外部资源）

## 笔记文件格式

每条笔记是一个独立的 Markdown 文件，带 YAML frontmatter：

```
---
name: <short-kebab-case-slug>
description: <one-line summary>
metadata:
  type: user | feedback | project | reference
---

<正文内容>
```

正文中，feedback 和 project 类型必须包含 **Why:** 和 **How to apply:** 行。
可以用 [[other-note-name]] 链接到其他相关笔记。

MEMORY.md 索引格式（每行一条）：
`- [<description>](<filename>.md) — <正文首句截断>`

## 存储位置

- `project`：项目级，存到 `.suisuicode/memory/`，与当前项目相关的信息
- `user`：用户级，存到 `~/.suisuicode/memory/`，跨项目通用的信息

## 操作类型

返回一个 JSON 数组，每个元素描述一个操作：

```json
[
  {"action": "create", "level": "project", "name": "python-stack", "description": "Tech Stack", "type": "project", "content": "本项目基于 Python 3.12+ 构建。\\n\\n**Why:** 用户选择 Python 作为主语言。\\n**How to apply:** 写代码时使用 Python 3.12+ 语法。"},
  {"action": "update", "level": "user", "filename": "terse-replies.md", "name": "terse-replies", "description": "Reply Style", "type": "user", "content": "用户偏好简洁回复，每次回答结尾不要重述刚做了什么。"},
  {"action": "delete", "level": "project", "filename": "old-note.md"}
]
```

## 语言与风格

- `name` 和 `description` **必须使用英文**。
- `description` 是分类标签（如 "User Role"、"Tech Stack"、"Reply Style"、"Coding Convention"），不是具体描述。用于 MEMORY.md 索引中快速分类识别。
- `content` 正文可以使用对话中的原始语言。

## 重要：笔记文件 vs MEMORY.md 索引

- 每条笔记的**正文内容**存放在独立的 `.md` 文件中（如 `python-stack.md`），带 YAML frontmatter。
- **MEMORY.md 是纯索引文件**，每行一条摘要，绝不放笔记正文。格式：`- [<description>](<filename>.md) — <正文首句截断>`
- create 操作 = 创建独立笔记文件 + 在 MEMORY.md 追加一行索引。系统会自动完成这两步，你只需在 JSON 中提供 name、description、type、content。
- **严禁**将笔记正文直接写入 MEMORY.md。

## 规则

- 只提取真正值得长期记住的信息。一次对话中偶尔出现的不一定是稳定的偏好或知识。
- 如果已有索引中包含相似笔记，用 update 合并而不是 create 新的。
- **update 时，content 必须整合旧内容和新信息**，不能只写新信息导致旧内容丢失。你会收到已有笔记的全文，请把新旧信息合并成一条完整、无冗余的记录。
- 如果某条笔记已过时或与更新后的信息矛盾，用 delete 删除。
- name 全小写、短横线分隔（kebab-case），不超过 50 字符。
- description 是一句话摘要，会出现在 MEMORY.md 索引中作为链接文字。
- 没有值得提取的信息时返回空数组 `[]`。
- 只返回 JSON 数组，不要其他文本。
"""


def build_memory_update_message(
    recent_msgs: list,
    existing_index: str,
    existing_notes: dict[str, str] | None = None,
) -> str:
    """构造记忆更新请求的 user 消息。"""
    conv_text_parts = []
    for msg in recent_msgs:
        role = msg.role if hasattr(msg, "role") else "unknown"
        content = msg.content if hasattr(msg, "content") else str(msg)
        conv_text_parts.append(f"[{role}]: {content}")

    conv_text = "\n\n".join(conv_text_parts)

    parts = [
        "请从以下对话片段中提取值得长期记住的信息。",
        "",
        "## 当前索引",
        existing_index if existing_index else "（空，尚无笔记）",
    ]

    # 包含已有笔记全文，供 update 时整合新旧信息
    if existing_notes:
        parts.append("")
        parts.append("## 已有笔记全文")
        for filename, note_content in existing_notes.items():
            parts.append(f"### {filename}")
            parts.append(note_content)
            parts.append("")

    parts.append("## 对话片段")
    parts.append(conv_text)
    return "\n".join(parts)
