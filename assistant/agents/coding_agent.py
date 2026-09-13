"""代码 AI：plan-act-observe 循环。

调用 DeepSeek function calling 执行工具（读写文件、执行命令），并持续输出事件，
供监控AI 判断进度/卡住、供上层做审批闸门。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from llm.deepseek import ChatResult, DeepSeekClient
from storage.models import (
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_HEARTBEAT,
    EVENT_NEED_APPROVAL,
    EVENT_STEP_START,
    EVENT_TOOL_CALL,
)
from tools.approval import ApprovalGate
from tools.base import Tool, ToolResult, to_openai_tool
from tools.file_tools import NEED_APPROVAL_PREFIX, make_file_tools
from tools.shell_tools import make_shell_tools

MAX_STEPS = 60
DEFAULT_MAX_TOKENS = 131072

# emit 回调签名：callable(type: str, payload: dict) -> None
Emitter = Callable[[str, dict], None]


class CodingAgent:
    def __init__(
        self,
        client: DeepSeekClient,
        workdir: str | Path,
        approval_list: Optional[list] = None,
        emit: Optional[Emitter] = None,
        max_steps: Optional[int] = None,
        max_tokens: Optional[int] = None,
        heartbeat_interval: Optional[int] = None,
    ):
        self.client = client
        self.workdir = Path(workdir)
        self.gate = ApprovalGate(approval_list)
        self.emit: Emitter = emit or (lambda _t, _p: None)
        self.max_steps = max_steps or MAX_STEPS
        self.max_tokens = max_tokens or DEFAULT_MAX_TOKENS
        self.heartbeat_interval = heartbeat_interval or 30  # 监控AI 指定的心跳间隔（秒）

        self.tools: list[Tool] = []
        self.tools += make_file_tools(self.workdir, self.gate)
        self.tools += make_shell_tools(self.workdir, self.gate)
        self.tool_map = {t.name: t for t in self.tools}

    def _chat_with_retry(self, messages: list, tool_schemas: list) -> ChatResult:
        """调用 LLM 带重试：瞬时错误（限流/网络/5xx）重试，仍失败则抛出。

        失败期间发 heartbeat 事件，让监控AI 知道这是「调用出错」而非「无进展」。
        """
        last_exc = None
        for attempt in range(1, 4):
            try:
                return self.client.chat(messages, tools=tool_schemas, max_tokens=self.max_tokens)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attempt < 3:
                    wait = min(2 ** attempt, 10)
                    self.emit(
                        EVENT_HEARTBEAT,
                        {"text": f"LLM 调用失败（第 {attempt} 次），{wait}s 后重试：{type(exc).__name__}: {exc}"},
                    )
                    time.sleep(wait)
        raise last_exc  # 三次都失败才走到这，last_exc 必不为 None

    def run(
        self,
        task: str,
        resume: Optional[dict] = None,
        on_checkpoint: Optional[Callable[[dict], None]] = None,
    ) -> str:
        """执行任务，返回最终摘要。

        resume: 断点续跑的上次状态 {'messages': [...], 'step': N}
        on_checkpoint: 每完成一步后的回调，用于持久化断点
        """
        system = (
            "你是一个代码 AI，在一个工作目录内自主完成任务。"
            "你可以调用工具读写文件、执行命令。每次回复尽量推进任务，"
            "写大文件时如果一次输出放不下，可以先用 write_file 写第一段、再用 append_file 追加后续段，直到写完。"
            f"在长时间任务里，每隔约 {self.heartbeat_interval} 秒，"
            "在调用工具的同一轮回复里附上一句简短进展说明（如「已完成 X，正在做 Y」），"
            "证明你没有卡死；不要把大任务憋成一步长时间不输出。"
            "任务完成后不要调用工具，直接给出简短中文总结，"
            "并且必须列出你改动/创建的所有文件与文件夹路径。"
        )
        if resume:
            messages = list(resume.get("messages") or [])
            start = int(resume.get("step") or 0) + 1
        else:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": task},
            ]
            start = 1
        tool_schemas = [to_openai_tool(t) for t in self.tools]

        for step in range(start, self.max_steps + 1):
            self.emit(EVENT_STEP_START, {"step": step})
            try:
                result = self._chat_with_retry(messages, tool_schemas)
            except Exception as e:
                self.emit(EVENT_ERROR, {"error": f"LLM 调用持续失败：{type(e).__name__}: {e}"})
                return f"任务失败：LLM 调用持续失败（{type(e).__name__}: {e}）"

            if result.tool_calls:
                # 模型边调用工具边附带的进展说明，作为心跳事件写回，供监控AI 判断是否卡死
                if result.content and result.content.strip():
                    self.emit(EVENT_HEARTBEAT, {"text": result.content.strip()[:200]})
                messages.append(
                    {
                        "role": "assistant",
                        "content": result.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {"name": tc.name, "arguments": tc.arguments},
                            }
                            for tc in result.tool_calls
                        ],
                    }
                )
                for tc in result.tool_calls:
                    self.emit(EVENT_TOOL_CALL, {"tool": tc.name, "args": tc.arguments})
                    tool_result = self._exec_tool(tc.name, tc.arguments)

                    if tool_result.content.startswith(NEED_APPROVAL_PREFIX):
                        path = tool_result.content.split(":", 1)[1]
                        self.emit(EVENT_NEED_APPROVAL, {"path": path, "tool": tc.name})
                        return f"需要用户同意后继续：{path}"

                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": tool_result.to_message(),
                        }
                    )
                if on_checkpoint:
                    on_checkpoint({"messages": messages, "step": step})
            else:
                final = result.content.strip()
                if not final:
                    # 模型返回空内容（通常是大文件输出被截断），不当作完成
                    self.emit(EVENT_ERROR, {"error": "模型返回空内容"})
                    return "任务未完成：模型返回了空响应，可能是单次输出太长被截断，建议把任务拆小一些再试。"
                self.emit(EVENT_DONE, {"step": step})
                return final

        self.emit(EVENT_ERROR, {"error": "达到最大步数仍未完成"})
        return "达到最大步数仍未完成。"

    def _exec_tool(self, name: str, arguments: str) -> ToolResult:
        tool = self.tool_map.get(name)
        if tool is None:
            return ToolResult(False, f"未知工具: {name}")
        try:
            kwargs = json.loads(arguments or "{}")
        except json.JSONDecodeError:
            return ToolResult(False, "工具参数不是合法 JSON")
        if not isinstance(kwargs, dict):
            return ToolResult(False, "工具参数应为 JSON 对象")
        try:
            return tool.func(**kwargs)
        except TypeError as e:
            return ToolResult(False, f"工具参数错误: {e}")
        except Exception as e:
            return ToolResult(False, f"工具执行异常: {e}")
