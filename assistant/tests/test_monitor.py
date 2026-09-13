"""监控AI 冒烟测试：infer() 规则判断。

运行：`python tests/test_monitor.py`
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from supervisor.monitor import Monitor  # noqa: E402


def test_infer() -> None:
    mon = Monitor(repo=None, stuck_timeout=120)
    now = datetime(2026, 1, 1, 12, 0, 0)

    assert mon.infer("running", [{"type": "done", "created_at": now}], now=now)["status"] == "done"
    assert mon.infer("running", [{"type": "need_approval", "created_at": now}], now=now)["status"] == "needs_user"
    assert mon.infer("running", [{"type": "error", "created_at": now}], now=now)["status"] == "stuck"
    assert mon.infer("running", [{"type": "step_start", "created_at": now}], now=now)["status"] == "running"

    old = now - timedelta(seconds=300)
    assert mon.infer("running", [{"type": "step_start", "created_at": old}], now=now)["status"] == "stuck"

    assert mon.infer("stopped", [], now=now)["status"] == "stopped"
    assert mon.infer("running", [], now=now)["status"] == "stuck"

    print("monitor 冒烟测试通过 OK")


if __name__ == "__main__":
    test_infer()
