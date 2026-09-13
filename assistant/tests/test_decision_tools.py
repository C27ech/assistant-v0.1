"""决策AI 工具循环冒烟测试：用假 LLM + 假执行器验证工具调用。

运行：`python tests/test_decision_tools.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.deepseek import ChatResult, ToolCall  # noqa: E402
from supervisor.decision import DecisionAgent  # noqa: E402


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, tools=None, **kwargs):
        r = self.responses[self.calls % len(self.responses)]
        self.calls += 1
        return r


def test_run_with_tools() -> None:
    calls = []

    def executor(name, args):
        calls.append((name, args))
        return f"ok:{name}"

    client = FakeClient(
        [
            ChatResult(
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="spawn_agent", arguments='{"task": "写文件", "workdir": "/tmp"}'),
                    ToolCall(id="c2", name="spawn_agent", arguments='{"task": "写测试", "workdir": "/tmp"}'),
                ],
            ),
            ChatResult(content="已启动两个代码AI", tool_calls=[]),
        ]
    )
    agent = DecisionAgent(client)
    out = agent.run([], "帮我写两个模块", executor)

    assert out == "已启动两个代码AI"
    assert len(calls) == 2
    assert calls[0][0] == "spawn_agent"
    assert calls[0][1]["task"] == "写文件"
    print("decision tools: 多工具调用 OK")


def test_run_no_tools() -> None:
    client = FakeClient([ChatResult(content="你好，需要我做什么？", tool_calls=[])])
    agent = DecisionAgent(client)
    out = agent.run([], "你好", lambda n, a: "should not be called")
    assert out == "你好，需要我做什么？"
    print("decision tools: 直接回复 OK")


if __name__ == "__main__":
    test_run_with_tools()
    test_run_no_tools()
