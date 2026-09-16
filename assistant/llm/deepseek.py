"""LLM 客户端封装（OpenAI 兼容接口：DeepSeek / 火山方舟 / 本地推理服务都能用）。

模型名一律从 .env 透传，代码不校验、不列举型号：
  · 通配 `*`（或留空）→ 请求里**不带 model 字段**，由端点用它自己的默认模型；
  · 具体型号（如 deepseek-chat）→ 原样发给端点。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from openai import OpenAI

from config.settings import is_wildcard

try:  # openai>=1.55 提供 Omit 哨兵：显式声明「这个字段不发送」
    from openai import Omit as _Omit
except ImportError:  # 老版本没法省略必填的 model → 退化成把通配串原样发出去
    _Omit = None  # type: ignore[assignment]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON 字符串


@dataclass
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    usage: dict = field(default_factory=dict)
    reasoning_content: str = ""  # 推理模型的思考过程（不计入正式回答）


class DeepSeekClient:
    def __init__(self, api_key: str, model: str = "", base_url: str = "https://api.deepseek.com"):
        if not api_key:
            raise ValueError("api_key 不能为空")
        self.model = (model or "").strip()
        self.client = OpenAI(api_key=api_key, base_url=base_url, timeout=300.0, max_retries=2)

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        temperature: float = 0.2,
        max_tokens: int = 16384,
    ) -> ChatResult:
        kwargs: dict[str, Any] = dict(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # 通配（* / 空）→ 不指定型号：请求里干脆不带 model 字段，由端点决定用哪个模型。
        if not is_wildcard(self.model):
            kwargs["model"] = self.model          # 具体型号：原样透传
        elif _Omit is not None:
            kwargs["model"] = _Omit()
        else:                                     # 老版 openai 省略不了该字段 → 原样发通配串
            kwargs["model"] = self.model
        if tools:
            kwargs["tools"] = tools

        resp = self.client.chat.completions.create(**kwargs)
        choice = resp.choices[0]
        msg = choice.message

        tool_calls = []
        if msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id or "",
                        name=tc.function.name,
                        arguments=tc.function.arguments or "{}",
                    )
                )

        usage: dict = {}
        if resp.usage:
            usage = {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
                "completion_tokens": getattr(resp.usage, "completion_tokens", None),
                "total_tokens": getattr(resp.usage, "total_tokens", None),
            }

        return ChatResult(
            content=msg.content or "",
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "",
            usage=usage,
            reasoning_content=getattr(msg, "reasoning_content", "") or "",
        )
