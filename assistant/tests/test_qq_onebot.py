"""QQ 渠道冒烟测试：OneBot 消息解析。

运行：`python tests/test_qq_onebot.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from channel.qq_onebot import QQOneBotChannel  # noqa: E402


def test_extract_text() -> None:
    # 纯文本
    event = {
        "post_type": "message",
        "message_type": "private",
        "user_id": 123456,
        "message": [{"type": "text", "data": {"text": "你好，帮我写个计算器"}}],
    }
    assert QQOneBotChannel._extract_text(event) == "你好，帮我写个计算器"

    # 多段拼接
    event2 = {
        "message": [
            {"type": "text", "data": {"text": "前半段"}},
            {"type": "face", "data": {"id": "1"}},
            {"type": "text", "data": {"text": "后半段"}},
        ]
    }
    assert QQOneBotChannel._extract_text(event2) == "前半段后半段"

    print("qq onebot 消息解析 OK")


if __name__ == "__main__":
    test_extract_text()
