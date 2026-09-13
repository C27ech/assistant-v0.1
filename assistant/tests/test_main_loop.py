"""主链路冒烟测试：Supervisor.handle 的存取 + 回显，不依赖微信/DeepSeek。

运行：`python tests/test_main_loop.py`
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings  # noqa: E402
from supervisor.runtime import Runtime  # noqa: E402


def test_handle() -> None:
    with tempfile.TemporaryDirectory() as d:
        settings = Settings()
        settings.db_path = str(Path(d) / "test.db")  # 用临时库，避免污染正式库

        sv = Runtime(settings)

        reply = sv.handle("wxid_1", "老板", "帮我做一个计算器")
        assert "收到" in reply, f"回显应包含『收到』，实际: {reply!r}"

        history = sv.repo.get_history("老板")
        assert len(history) == 2, "应存有 in/out 两条消息"
        assert history[0]["direction"] == "in"
        assert history[0]["content"] == "帮我做一个计算器"
        assert history[1]["direction"] == "out"

        sv.db.close()
        print("main loop 冒烟测试通过 OK")


if __name__ == "__main__":
    test_handle()
