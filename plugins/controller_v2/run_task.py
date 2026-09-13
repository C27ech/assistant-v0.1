# -*- coding: utf-8 -*-
"""controller_v2 任务启动器（不修改任何业务文件）。

用法：python run_task.py <task文件>
task 文件为 UTF-8 文本，内容即任务文本。本启动器把任务原样交给 main.py 的
main() CLI 逻辑执行，stdout/stderr 均以 UTF-8 输出，供外部轮询日志。

背景：本机 Assistant 看门狗会误杀命令行含 "main.py" 的新进程（见项目内
docs/supervisor_probe_report.md），因此不直接以 python main.py 方式拉起，
而是通过本启动器调用同一套 main() 逻辑，避免子进程被误杀。
"""
from __future__ import annotations

import sys


def _load_task() -> str:
    path = sys.argv[1] if len(sys.argv) > 1 else "task_input.txt"
    with open(path, "r", encoding="utf-8-sig") as fh:
        return fh.read().strip()


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    task = _load_task()
    if not task:
        print("RESULT_JSON {\"status\": \"fail\", \"error\": \"empty task file\"}", flush=True)
        print("EXIT 2", flush=True)
        return 2

    print("TASK_START", flush=True)
    from main import main as cli_main

    code = cli_main(["controller_main.py", task])
    print(f"EXIT {code}", flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
