"""Fork 路径辅助：消息构造、嵌套检测（spec F22-F25）。"""

from __future__ import annotations

import copy

from suisuicode.llm import Message, ToolResult, ROLE_USER, ROLE_TOOL

FORK_BOILERPLATE_TAG = "<fork_boilerplate>"

FORK_BOILERPLATE = """<fork_boilerplate>
你是一个 Fork 出来的工作进程。你不是主 Agent。
规则（不可协商）：
1. 不能再 Fork（调用 Agent 工具会被拦截）。
2. 不要对话、不要提问、不要请求确认。
3. 直接使用工具：读文件、搜索代码、做修改。
4. 严格限制在你被分配的任务范围内。
5. 最终报告以 "Scope:" 开头，500 字以内。
</fork_boilerplate>

"""


def build_forked_messages(
    parent_msgs: list[Message],
    task: str,
) -> list[Message]:
    """把父对话克隆到 Fork 子对话，处理悬空 tool_use，追加 Boilerplate + task。

    行为：
      1. 深拷贝 parent_msgs
      2. 扫描末尾 assistant 消息的 tool_calls，如果对应的 RoleTool 消息缺失，
         生成一条 placeholder tool_results
      3. 追加 user 消息 = FORK_BOILERPLATE + task
    """
    cloned: list[Message] = copy.deepcopy(parent_msgs)

    # 收集所有未被配对消费的 tool_call_id
    pending_ids: set[str] = set()
    consumed_ids: set[str] = set()

    for msg in cloned:
        for tc in msg.tool_calls:
            pending_ids.add(tc.id)
        if msg.role == ROLE_TOOL:
            for tr in msg.tool_results:
                consumed_ids.add(tr.tool_call_id)

    unpaired = pending_ids - consumed_ids

    # 为未配对的 tool_use 生成 placeholder
    if unpaired:
        placeholders = [
            ToolResult(tool_call_id=tid, content="[forked, skipped]", is_error=True)
            for tid in unpaired
        ]
        cloned.append(Message(role=ROLE_TOOL, tool_results=placeholders))

    # 追加 Fork Boilerplate + task
    cloned.append(Message(role=ROLE_USER, content=FORK_BOILERPLATE + task))

    return cloned


def is_fork_context(msgs: list[Message]) -> bool:
    """判定一个 conversation 的消息历史是否来自 Fork。

    扫描所有 user/tool/assistant 消息内容寻找 ``<fork_boilerplate>`` 标签。
    QuerySource 检测的兜底机制。
    """
    for msg in msgs:
        if FORK_BOILERPLATE_TAG in msg.content:
            return True
        for tr in msg.tool_results:
            if FORK_BOILERPLATE_TAG in tr.content:
                return True
    return False
