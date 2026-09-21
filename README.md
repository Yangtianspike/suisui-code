# SuisuiCode

从零构建的 AI 编程助手（AI Coding Agent），跑在终端里。

**716 项测试 | 15 个迭代章节 | 3 个 LLM 协议 | 6 个核心工具 | 160+ 个源文件**

---

## 这是什么

SuisuiCode 是一个运行在终端中的 AI 编程助手，类似 Claude Code。它不是一个 API wrapper —— 从 Agent Loop 到权限系统，从 MCP 协议到上下文压缩，每一层都是亲手写的。

![](https://img.shields.io/badge/python-3.12+-blue)
![](https://img.shields.io/badge/tests-716-green)
![](https://img.shields.io/badge/license-MIT-yellow)

### 核心特性

- **多 Provider 支持** — Anthropic Claude、OpenAI、DeepSeek 三种协议无缝切换
- **TUI 交互界面** — 基于 Textual 框架，输入框 + 滚动日志 + 状态栏
- **ReAct Agent Loop** — 多轮工具调用循环，五种停止条件，保序分批并发
- **6 个内置工具** — `read_file` / `write_file` / `edit_file` / `bash` / `glob` / `grep`
- **五层权限防御** — 黑名单 → 沙箱 → 规则引擎 → 模式兜底 → 人在回路
- **MCP 协议** — 集成 Model Context Protocol，支持 stdio + Streamable HTTP
- **两层上下文压缩** — 大文件落盘 + LLM 摘要 + PromptTooLong 自重试 + 熔断
- **项目记忆** — SUISUICODE.md 指令 + 自动笔记 + JSONL 会话存档 + `/resume` 恢复
- **Hook 生命周期** — 11 个事件钩子，shell/http/prompt 四种动作，支持 Claude Code 风格 DSL
- **Skill 系统** — 插件式技能，inline/fork 双模式执行，热重载
- **SubAgent 机制** — 预定义角色 + 后台任务 + Fork 隔离 + 工具过滤
- **Agent Team** — 多 Agent 协作，共享任务板 + 邮箱通信，支持 tmux 后端
- **Worktree 隔离** — Git worktree 子 Agent 沙箱，自动清理

---

## 快速开始

### 安装

```bash
git clone https://github.com/Yangtianspike/suisui-code.git
cd suisui-code
uv sync
```

### 配置

在 `~/.suisuicode/config.yaml` 中配置至少一个 Provider：

```yaml
providers:
  - name: claude
    protocol: anthropic
    api_key: sk-ant-your-key-here
    model: claude-opus-4-8
    context_window: 200000
```

也支持 OpenAI / DeepSeek：

```yaml
  - name: gpt
    protocol: openai
    api_key: sk-your-key-here
    model: gpt-4o
    base_url: https://api.openai.com/v1
```

### 启动

```bash
cd your-project
uv run --directory /path/to/suisuicode suisuicode
```

---

## 使用

| 命令 | 功能 |
|------|------|
| `/help` | 显示所有命令 |
| `/plan` | 进入 Plan Mode（只读工具，先规划后执行） |
| `/do` | 退出 Plan Mode，执行计划 |
| `/compact` | 手动触发上下文压缩 |
| `/resume` | 浏览和恢复历史会话 |
| `/memory` | 查看项目记忆 |
| `/hooks` | 列出已加载的 Hook |
| `/skill list` | 列出可用 Skill |
| `/clear` | 清空对话，开始新会话 |
| `/exit` | 退出 |

---

## 架构

```
src/suisuicode/
├── agent/          # ReAct Agent Loop + Fork + SubAgent
├── cli.py          # 入口，全链路串联
├── command/        # Slash 命令注册/分发/解析
├── compact/        # 两层上下文压缩 + Token 估算
├── config/         # YAML 配置加载
├── conversation.py # 多轮对话管理
├── coordinator/    # Coordinator Mode（Lead 工具收窄）
├── hook/           # Hook 生命周期引擎 + DSL
├── instructions/   # SUISUICODE.md 三层加载 + @include
├── llm/            # Provider 抽象层（Anthropic/OpenAI）
├── memory/         # 自动笔记 + MEMORY.md
├── permission/     # 五层权限引擎 + 匹配器
├── prompt/         # 系统提示工程（7 段结构）
├── session/        # JSONL 会话存档 + 恢复
├── skills/         # Skill 解析/编目/执行
├── subagent/       # 子 Agent 角色定义
├── task/           # 后台任务管理
├── team/           # 多 Agent 协作（Team/Task/Mailbox）
├── tool/           # 6 个核心工具 + 工具过滤
├── tui/            # Textual 界面 + 补全菜单
└── worktree/       # Git Worktree 隔离
```

---

## 技术栈

| 层 | 技术选择 |
|---|---------|
| 界面 | Textual (TUI) |
| LLM SDK | anthropic, openai |
| MCP | mcp >= 1.0 |
| 配置 | YAML |
| 异步 | asyncio |
| 测试 | pytest + pytest-asyncio |
| 静态检查 | ruff |

---

## 测试

```bash
# 全量测试（排除网络相关）
uv run pytest tests/ -k "not test_connection_refused" --ignore=tests/mcp --ignore=tests/compact/test_compact.py

# 分模块
uv run pytest tests/agent/ -v    # Agent 模块
uv run pytest tests/hook/ -v     # Hook 模块
uv run pytest tests/compact/ -v  # 压缩模块
```

---

## 开发历程

| 章节 | 主题 | 状态 |
|------|------|------|
| ch02 | 多 Provider + TUI | ✅ |
| ch03 | 工具系统（6 个核心工具） | ✅ |
| ch04 | ReAct Agent Loop | ✅ |
| ch05 | 系统提示工程化 | ✅ |
| ch06 | 权限系统（五层防御） | ✅ |
| ch07 | MCP 客户端 | ✅ |
| ch08 | 上下文管理（两层压缩） | ✅ |
| ch09 | 项目记忆 + 会话持久化 | ✅ |
| ch10 | Slash 命令体系 | ✅ |
| ch11 | Skill 系统 | ✅ |
| ch12 | Hook 生命周期 | ✅ |
| ch13 | SubAgent 机制 | ✅ |
| ch14 | Worktree 隔离 | ✅ |
| ch15 | Agent Team | ✅ |

---

## License

MIT
