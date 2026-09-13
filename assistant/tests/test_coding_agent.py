"""代码AI 冒烟测试：用假 LLM 验证 agent loop、事件流、审批闸门。

运行：`python tests/test_coding_agent.py`
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.coding_agent import CodingAgent  # noqa: E402
from llm.deepseek import ChatResult, ToolCall  # noqa: E402


class FakeClient:
    """按顺序返回预设响应的假 LLM 客户端。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, tools=None, **kwargs):
        r = self.responses[self.calls % len(self.responses)]
        self.calls += 1
        return r


def test_write_then_done() -> None:
    with tempfile.TemporaryDirectory() as d:
        events = []
        client = FakeClient(
            [
                ChatResult(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="write_file", arguments='{"path":"a.txt","content":"hello world"}')
                    ],
                ),
                ChatResult(content="已完成", tool_calls=[]),
            ]
        )
        agent = CodingAgent(client, d, emit=lambda t, p: events.append((t, p)))
        out = agent.run("写一个 a.txt 文件")

        assert (Path(d) / "a.txt").read_text() == "hello world"
        assert out == "已完成"
        types = [t for t, _ in events]
        assert "step_start" in types
        assert "tool_call" in types
        assert "done" in types
        print("coding agent: 写文件→完成 OK")


def test_approval_halt() -> None:
    with tempfile.TemporaryDirectory() as d:
        events = []
        client = FakeClient(
            [
                ChatResult(
                    content="",
                    tool_calls=[
                        ToolCall(id="c1", name="write_file", arguments='{"path":"secret.txt","content":"x"}')
                    ],
                ),
            ]
        )
        agent = CodingAgent(
            client, d, approval_list=["secret.txt"], emit=lambda t, p: events.append((t, p))
        )
        out = agent.run("写 secret.txt")

        assert "需要用户同意" in out
        assert not (Path(d) / "secret.txt").exists()
        assert ("need_approval", {"path": "secret.txt", "tool": "write_file"}) in events
        print("coding agent: 审批闸门拦截 OK")


if __name__ == "__main__":
    test_write_then_done()
    test_approval_halt()
