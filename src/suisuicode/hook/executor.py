"""Hook 动作执行器：shell / prompt / http / subagent 四类。"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass

import httpx

from suisuicode.hook.dsl import substitute_variables
from suisuicode.hook.rule import (
    ActionType,
    HttpAction,
    Payload,
    PromptAction,
    Rule,
    ShellAction,
    SubagentAction,
)


@dataclass
class ExecutionResult:
    """单条 Hook 动作的执行结果。

    blocked/prompt/err 三语义互斥：
    - blocked=True → 拦截命中（仅拦截类事件有效）
    - prompt 非空 → 注入提示文本
    - err 非 None → hook 自身失败（不拦截，仅记录）
    """

    blocked: bool = False
    reason: str = ""
    prompt: str = ""
    err: Exception | None = None


class Executor:
    """四类动作执行器。"""

    def __init__(self) -> None:
        self._http_client = httpx.AsyncClient(timeout=30.0)

    async def run(
        self, rule: Rule, payload: Payload, *, blocking: bool
    ) -> ExecutionResult:
        """根据 action.type 分派到对应执行方法。

        Claude Code 风格 $VAR 在运行时替换（payload 此时已确定）。
        """
        action = rule.action
        reject = rule.reject
        if action.type is ActionType.SHELL:
            # $VAR 替换
            cmd = substitute_variables(action.shell.command, payload)
            sa = ShellAction(command=cmd)
            return await self._run_shell(
                sa, payload, blocking, reject, rule.timeout_s
            )
        if action.type is ActionType.PROMPT:
            # $VAR 替换
            text = substitute_variables(action.prompt.text, payload)
            return ExecutionResult(prompt=text)
        if action.type is ActionType.HTTP:
            # $VAR 替换在 URL 和 body
            ha = action.http
            url = substitute_variables(ha.url, payload) if ha.url else ha.url
            body = None
            if ha.body is not None:
                body = substitute_variables(ha.body, payload)
            ha_sub = HttpAction(
                url=url,
                method=ha.method,
                headers={
                    k: substitute_variables(v, payload)
                    for k, v in (ha.headers or {}).items()
                },
                body=body,
            )
            return await self._run_http(
                ha_sub, payload, blocking, reject, rule.timeout_s
            )
        if action.type is ActionType.SUBAGENT:
            return self._run_subagent(action.subagent)
        return ExecutionResult(
            err=RuntimeError(f"unknown action type: {action.type}")
        )

    # ── shell ─────────────────────────────────────────

    async def _run_shell(
        self,
        sa: ShellAction,
        payload: Payload,
        blocking: bool,
        reject: bool,
        timeout: float,
    ) -> ExecutionResult:
        """执行 shell 命令，通过 stdin 传 payload JSON。

        - reject=True + blocking → stdout/stderr 直接当拒绝原因，不等 exit 2
        - returncode 2 + blocking → 拦截（无 reject 时的默认行为）
        - returncode 0 → 放行
        - 其它非零 → hook 失败但不拦截
        """
        payload_json = json.dumps(payload, sort_keys=True).encode()
        try:
            proc = await asyncio.create_subprocess_shell(
                sa.command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(payload_json), timeout=timeout
            )
        except asyncio.TimeoutError:
            return ExecutionResult(
                err=TimeoutError(
                    f"shell command timed out after {timeout}s: "
                    f"{sa.command[:120]}"
                )
            )
        except Exception as e:
            return ExecutionResult(err=e)

        out_bytes = stderr or stdout
        out_text = out_bytes.decode(errors="replace").rstrip("\n")

        # reject 模式：输出即拒绝原因
        if reject and blocking:
            return ExecutionResult(blocked=True, reason=out_text)

        # legacy exit 2 模式
        if blocking and proc.returncode == 2:
            return ExecutionResult(blocked=True, reason=out_text)

        if proc.returncode == 0:
            return ExecutionResult()

        # 非零非拦截信号 → hook 失败但不拦截
        return ExecutionResult(
            err=RuntimeError(f"exit {proc.returncode}: {out_text}")
        )

    # ── prompt ────────────────────────────────────────

    def _run_prompt(self, pa: PromptAction) -> ExecutionResult:
        """prompt 动作：直接返回 text 作为注入内容。"""
        return ExecutionResult(prompt=pa.text)

    # ── http ──────────────────────────────────────────

    async def _run_http(
        self,
        ha: HttpAction,
        payload: Payload,
        blocking: bool,
        reject: bool,
        timeout: float,
    ) -> ExecutionResult:
        """发送 HTTP 请求。

        - reject=True + blocking → 响应 body 直接当拒绝原因
        - 非 reject: status 2xx + body 含 {"decision":"block","reason":"..."} → 拦截
        - 其它 → 放行
        - 网络错/超时/JSON 解析失败 → hook 失败
        """
        method = ha.method or "POST"
        try:
            if ha.body is None:
                body = json.dumps(payload, sort_keys=True)
            else:
                body = ha.body.format_map(payload)
        except (KeyError, ValueError) as e:
            return ExecutionResult(err=ValueError(f"template render error: {e}"))

        try:
            resp = await self._http_client.request(
                method,
                ha.url,
                content=body,
                headers=ha.headers or {},
                timeout=timeout,
            )
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            return ExecutionResult(err=e)

        if 200 <= resp.status_code < 300:
            # reject 模式：响应整个 body 即拒绝原因
            if reject and blocking:
                reason = resp.text.strip()
                return ExecutionResult(blocked=True, reason=reason)

            # 非 reject 模式：检查 decision 字段
            try:
                data = json.loads(resp.text)
            except (json.JSONDecodeError, TypeError):
                return ExecutionResult()
            if (
                isinstance(data, dict)
                and data.get("decision") == "block"
                and blocking
            ):
                reason = str(data.get("reason", ""))
                return ExecutionResult(blocked=True, reason=reason)

        return ExecutionResult()

    # ── subagent (stub) ───────────────────────────────

    def _run_subagent(self, sa: SubagentAction) -> ExecutionResult:
        """subagent 占位实现：仅打 stderr 日志，不报错也不拦截。"""
        print(
            f"[hook subagent] not yet implemented, skipped: {sa.agent_name}",
            file=sys.stderr,
        )
        return ExecutionResult()
