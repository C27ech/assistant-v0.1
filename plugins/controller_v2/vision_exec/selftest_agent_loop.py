"""有状态 agent loop 自测脚本（只读观察，不执行键鼠动作）。

用法：
    python vision_exec/selftest_agent_loop.py

输出到 stdout：
    - ROUTE_*：接口路由结果（演示路由层能力，本脚本不执行接口动作）；
    - LOOP_*：新的有状态 agent loop 运行结果；
    - HISTORY_*：SQLite 历史落库数量证明。
"""

from __future__ import annotations

import json
from typing import Any, Dict

from router.router import route
from vision_exec import history as history_store
from vision_exec.executor import run_vision_task


def _emit(label: str, value: Any) -> None:
    text = json.dumps(value, ensure_ascii=False)
    print(f"{label} {text}")


def main() -> int:
    # 1) 路由结果：优先走接口通道的样例（不实际执行，避免误操作）。
    interface_task = "把当前前台窗口切到前台"
    decision = route(interface_task)
    _emit("ROUTE_TASK", interface_task)
    _emit("ROUTE_CHANNEL", decision.channel)
    _emit("ROUTE_INTERFACE", decision.interface_name)
    _emit("ROUTE_REASON", decision.reason)

    # 2) 直接跑新的有状态 agent loop（无害观察任务，禁止任何键鼠动作）。
    vision_task = (
        "请观察当前屏幕：只要能看到桌面或任意应用窗口，就直接判定任务完成；"
        "不要执行任何鼠标点击、键盘输入或滚动操作。"
    )
    result = run_vision_task(vision_task, max_rounds=5)

    _emit("LOOP_TASK", vision_task)
    _emit("LOOP_STATUS", result.get("status"))
    _emit("LOOP_ROUNDS", result.get("rounds"))
    _emit("LOOP_SESSION", result.get("session_id"))
    _emit("LOOP_HISTORY_COUNT", result.get("history_count"))
    _emit("LOOP_TERMINATE_REASON", result.get("error") or result.get("verify", {}).get("reason"))
    _emit("LOOP_ACTIONS", result.get("actions"))
    _emit("LOOP_ROUND_DETAILS", result.get("round_details"))

    # 3) SQLite 历史条数证明（当前 session + 全库）。
    session_id = result.get("session_id")
    _emit("HISTORY_SESSION_COUNT", history_store.count_history(session_id=session_id))
    _emit("HISTORY_TOTAL_COUNT", history_store.count_history())

    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
