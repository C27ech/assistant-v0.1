"""Live 测试：验证 DeepSeek key 与决策AI 能否正常对话。

运行：`python scripts/live_test.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings  # noqa: E402
from llm.deepseek import DeepSeekClient  # noqa: E402
from supervisor.decision import DecisionAgent  # noqa: E402


def main() -> None:
    s = Settings.load()
    if not s.api_key_decision:
        print("✗ 未配置 DEEPSEEK_API_KEY_DECISION，请先编辑 .env")
        sys.exit(1)

    print(f"模型: {s.decision_model}")
    print(f"base_url: {s.deepseek_base_url}")
    print("正在调用决策AI ...\n")

    client = DeepSeekClient(s.api_key_decision, s.decision_model, s.deepseek_base_url)
    agent = DecisionAgent(client)

    try:
        reply = agent.reply([], "你好，帮我做一个带图形界面的计算器")
    except Exception as e:
        print(f"✗ 调用失败: {e}")
        sys.exit(1)

    print("决策AI 回复：")
    print("=" * 40)
    print(reply)
    print("=" * 40)


if __name__ == "__main__":
    main()
