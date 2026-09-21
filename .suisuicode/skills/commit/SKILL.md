---
name: commit
description: 生成规范的 Git commit message（Conventional Commits 格式）。当用户提到 commit、提交、git message、conventional commit、生成 commit 信息、提交代码、写 commit 时使用此 skill。即使没提到"commit"关键词，只要是让 AI 帮忙写 git 提交信息，都应触发。
mode: inline
---

# Commit Message 生成

按 Conventional Commits 规范为当前变更生成可读、信息完整的 commit message。

## 流程

### 1. 获取变更内容

先运行 `git diff --staged` 查看已暂存的变更。如果 staged 区为空，再运行 `git diff` 查看未暂存变更。如果没有任何 diff，告知用户并停止。

### 2. 分析变更

快速分析 diff 内容，确定：
- **范围**：改动涉及几个文件？是否跨模块？
- **性质**：新功能 / bug 修复 / 重构 / 文档 / 测试 / 构建？
- **核心变更**：一句话总结这个 diff 做了什么

### 3. 生成 commit message

按以下结构生成：

```
<type>(<scope>): <subject>
```

**type 选一**：
- `feat` — 新功能或能力
- `fix` — 修复了一个 bug
- `refactor` — 重构，行为不变
- `docs` — 仅文档变更
- `test` — 添加或修改测试
- `chore` — 构建、依赖、工具配置
- `perf` — 性能优化
- `style` — 格式、空格（不影响逻辑）

**scope**：用英文表示受影响的模块/包/文件（如 `agent`, `skills`, `tui`, `compact`）。不确定就省略。

**subject**：
- 中文描述，不超过 50 字
- 用祈使句（"添加"而非"添加了"）
- 不加句号

**body**（可选）：
- 仅在需要解释 WHY 时添加
- 解释变更的原因，不是复述 diff
- 每条不超过 72 字符

### 4. 展示与确认

向用户展示生成的消息，等待用户确认。不要未经确认就执行 `git commit`。

**如果用户要求直接提交**：确认 message 后执行 `git commit -m "..."` 或 `git commit -am "..."`。

## 示例

**好**：
```
feat(skills): 添加 Catalog 三层扫描支持

Claude Code 的 ~/.claude/skills/ 目录中的 skill 现在可以被
SuisuiCode 自动发现，优先级低于项目级和用户 SuisuiCode 目录。
```

**不好**：
```
feat: update code
fix: 修了一个问题
chore(skills): 改了 catalog.py
```

## 何时使用 body

- 修改了多个模块 → 说明之间的关系
- 修复了一个非显而易见的 bug → 说明根因
- 引入了一个新约定或模式 → 说明动机
- 简单改动（加个参数、改个名字）→ 不需要 body
