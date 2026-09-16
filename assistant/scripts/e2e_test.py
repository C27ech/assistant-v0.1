"""端到端演示：真实跑一遍完整闭环（决策AI → 编排器 → 代码AI → 监控）。

运行：`python scripts/e2e_test.py`
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import Settings  # noqa: E402
from supervisor.runtime import Runtime  # noqa: E402


def _agent_states(runtime: Runtime) -> dict:
    states = {}
    for t in runtime.repo.list_tasks():
        for a in runtime.repo.list_agents(t["id"]):
            states[a["id"]] = a["status"]
    return states


def main() -> None:
    settings = Settings.load()
    tmp = Path(tempfile.mkdtemp(prefix="e2e_"))
    workdir = tmp / "work"
    workdir.mkdir()
    settings.db_path = str(tmp / "e2e.db")  # 独立临时库，避免历史串扰
    print(f"工作目录: {workdir}")

    runtime = Runtime(settings)
    runtime.run_monitor_loop(interval=2)

    task = (
        f"请在目录 {workdir} 里创建一个简单的 Python 项目，包含两个文件：\n"
        "1. calculator.py：实现 add(a, b) 和 multiply(a, b) 两个函数；\n"
        "2. main.py：导入 calculator，调用 add 和 multiply 并打印结果。\n"
        "只用 write_file 写这两个文件，不要执行任何命令。"
    )

    print("发送需求给决策AI ...\n")
    reply = runtime.handle("console-user", "console", task)
    print("决策AI 回复：")
    print("=" * 50)
    print(reply)
    print("=" * 50)

    print("\n监控代码AI 执行（每 2 秒轮询）...")
    seen = {}
    deadline = time.time() + 90
    while time.time() < deadline:
        states = _agent_states(runtime)
        for aid, st in states.items():
            if seen.get(aid) != st:
                seen[aid] = st
                print(f"  [{aid}] -> {st}")
        if states and all(st not in ("running", "queued") for st in states.values()):
            break
        time.sleep(2)

    print("\n最终文件:")
    files = sorted(workdir.glob("*.py"))
    if not files:
        print("  （无 .py 文件）")
    for f in files:
        print(f"  --- {f.name} ---")
        print("  " + f.read_text(encoding="utf-8").replace("\n", "\n  "))

    runtime.orchestrator.shutdown()
    print("\n完成。")


if __name__ == "__main__":
    main()

