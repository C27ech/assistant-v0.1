"""controller_v2 CLI 主入口。

用法：
    python main.py "<任务文本>"

契约：
- stdout 只输出一行 JSON 结果，supervisor 按行解析；
- 所有日志 / 诊断信息一律走 stderr；
- 空任务打印错误并 exit(2)；
- 接口通道成功 / 失败退出码 0 / 1；
- 视觉执行成功 / 失败退出码 0 / 1；
- 所有未预期异常兜底为一行 JSON 并 exit(1)。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, List, Optional


def _log(message: str) -> None:
    """所有日志统一写 stderr，避免污染 stdout。"""
    try:
        print(message, file=sys.stderr)
    except Exception:  # noqa: BLE001 - 日志失败不影响主流程。
        pass


def _emit_json(obj: Dict[str, Any]) -> None:
    """向 stdout 输出一行 JSON 结果。"""
    try:
        text = json.dumps(obj, ensure_ascii=False)
    except Exception:  # noqa: BLE001 - 兜底为 ASCII 转义。
        text = json.dumps(obj, ensure_ascii=True)

    try:
        sys.stdout.write(text + "\n")
    except UnicodeEncodeError:
        # Windows 控制台 / 重定向编码兜底：ASCII 转义后必然可写。
        sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _vision_result_to_exit(result: Dict[str, Any]) -> int:
    """把 vision_exec.run_vision_task 的返回精简映射为契约 JSON，并返回退出码。"""
    status = "ok" if result.get("status") == "ok" else "fail"

    try:
        rounds = int(result.get("rounds", 0))
    except (TypeError, ValueError):
        rounds = 0

    error = result.get("error")
    error = error if isinstance(error, str) else None

    _emit_json(
        {
            "status": status,
            "channel": "vision",
            "rounds": rounds,
            "error": error,
        }
    )
    return 0 if status == "ok" else 1


def _run_vision(task: str) -> int:
    """视觉兜底路径：调用 vision_exec.run_vision_task。"""
    from vision_exec.executor import run_vision_task

    result = run_vision_task(task)
    if not isinstance(result, dict):
        result = {}

    _log("[main] vision 执行返回 " + str(result.get("status", "unknown")))
    return _vision_result_to_exit(result)


def _run_interface(decision: Any, task: str) -> int:
    """接口通道路径：调用 action.execute_interface。"""
    from action.action import execute_interface
    from router.channels import build_interface_action

    action = build_interface_action(decision.interface_name, decision.params)
    if action is None:
        _log(f"[main] 接口动作构建失败: {decision.interface_name}，降级视觉执行")
        return _run_vision(task)

    result = execute_interface(action)
    if not isinstance(result, dict):
        result = {}

    ok = bool(result.get("ok"))
    status = "ok" if ok else "fail"
    detail = result.get("reason") or result.get("status") or ("ok" if ok else "fail")
    if not isinstance(detail, str):
        detail = str(detail)

    _emit_json(
        {
            "status": status,
            "channel": decision.interface_name,
            "detail": detail,
        }
    )
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    """CLI 主入口。

    :param argv: 参数列表；为 None 时使用 sys.argv。
    :returns: 进程退出码（0=成功，1=失败，2=任务为空）。
    """
    if argv is None:
        argv = sys.argv

    try:
        # 1) 读取任务文本；为空则打印错误并 exit(2)。
        if len(argv) < 2:
            _log("错误：缺少任务文本。用法：python main.py \"<任务文本>\"")
            return 2

        task = argv[1]
        if not isinstance(task, str) or not task.strip():
            _log("错误：任务文本不能为空。")
            return 2

        task = task.strip()

        if len(argv) > 2:
            _log("警告：检测到多个位置参数，仅使用第一个作为任务文本。")

        # 2) 路由判定。
        from router.router import route

        decision = route(task)
        _log(f"[router] channel={decision.channel} reason={decision.reason}")

        # 3) 危险操作被硬护栏拦截：直接失败，不执行接口，也不降级视觉。
        if decision.channel == "blocked":
            _emit_json(
                {
                    "status": "blocked",
                    "channel": decision.interface_name,
                    "reason": decision.reason,
                }
            )
            return 1

        # 4) 接口通道且 handler 可用 -> 走接口执行器。
        if decision.channel == "interface":
            from router.channels import get_channel

            spec = get_channel(decision.interface_name)
            if spec is not None and spec.available():
                return _run_interface(decision, task)

            _log(
                f"[router] 接口通道 {decision.interface_name} 不可用，降级视觉执行"
            )
            return _run_vision(task)

        # 4) vision / 接口降级 -> 走视觉执行器。
        return _run_vision(task)

    except Exception as exc:  # noqa: BLE001 - 兜底保证不崩溃、不无输出。
        _emit_json(
            {
                "status": "fail",
                "channel": None,
                "error": str(exc),
            }
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
