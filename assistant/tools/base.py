"""工具抽象：定义工具、执行结果，以及转成 OpenAI function 格式。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolResult:
    success: bool
    content: str
    data: Any = None

    def to_message(self) -> str:
        """转成回传给大模型的文本。"""
        prefix = "" if self.success else "[错误] "
        return prefix + self.content


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., ToolResult]


def to_openai_tool(tool: Tool) -> dict:
    """转成 OpenAI function calling 需要的 tools 项。"""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }
