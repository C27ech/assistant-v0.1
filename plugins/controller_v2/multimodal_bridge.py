"""subprocess 桥接入口。

supervisor 通过命令行调用：
    python multimodal_bridge.py --command "<任务文本>"

契约：stdout 只输出一行 JSON，结构为：
    {"ok": true|false, "result": "<给 supervisor 的文本>", "error": "<可选，失败原因>"}

路由逻辑：
    router.route(command) 判定走接口通道还是视觉通道；
    接口可用时优先调用 action 层 execute_interface，接口失败/不可用时降级
    vision_exec.run_vision_task(command)；任何异常都会被 catch 并规整为 JSON，
    绝不裸崩。
"""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional, Tuple


def _emit(ok: bool, result: str, error: Optional[str] = None) -> None:
    """把结果规整为一行 JSON 写到 stdout。"""
    payload: Dict[str, Any] = {"ok": bool(ok), "result": str(result or "")}
    if error:
        payload["error"] = str(error)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _parse_command(argv) -> Optional[str]:
    """解析 --command 参数，兼容 ``--command value`` 与 ``--command=value``。

    参数缺失或 ``--command`` 后无值时返回 None。
    """
    command: Optional[str] = None
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--command":
            if i + 1 >= len(argv):
                return None
            command = argv[i + 1]
            i += 2
        elif arg.startswith("--command="):
            command = arg[len("--command="):]
            i += 1
        else:
            i += 1
    return command


def _exception_summary(exc: BaseException) -> str:
    """返回异常类型 + 消息 + 最后触发位置，作为一行可读摘要。"""
    tb = exc.__traceback__
    last: Optional[str] = None
    while tb is not None:
        frame = tb.tb_frame
        last = f"{frame.f_code.co_filename}:{tb.tb_lineno} in {frame.f_code.co_name}"
        tb = tb.tb_next

    base = f"{type(exc).__name__}: {exc}"
    if last:
        return f"{base}（触发位置：{last}）"
    return base


def _handle(command: str) -> Tuple[bool, str, Optional[str]]:
    """执行路由判定并调用对应通道，返回 (ok, result, error)。"""
    from router import route
    from router.channels import build_interface_action
    from action.action import execute_interface
    from vision_exec import run_vision_task

    decision = route(command)

    interface_fail_note: Optional[str] = None
    if decision.channel == "interface" and decision.interface_name:
        action = build_interface_action(decision.interface_name, decision.params)
        if action is not None:
            result = execute_interface(action)
            if isinstance(result, dict) and result.get("ok"):
                reason = result.get("reason") or "执行成功"
                return True, f"接口通道[{decision.interface_name}]执行成功：{reason}", None

            if isinstance(result, dict):
                reason = result.get("reason") or result.get("status") or "未知原因"
            else:
                reason = str(result)
            interface_fail_note = (
                f"接口通道[{decision.interface_name}]执行失败（{reason}），已降级视觉执行"
            )

    # 视觉通道（或接口不可用 / 接口执行失败后的兜底）。
    vision = run_vision_task(command)
    if not isinstance(vision, dict):
        return False, "视觉执行返回结果非法", "视觉执行返回结果非法"

    status = vision.get("status")
    verify = vision.get("verify") if isinstance(vision.get("verify"), dict) else {}
    rounds = vision.get("rounds") or 0
    actions = vision.get("actions") or []

    if status == "ok":
        reason = verify.get("reason") or "任务完成"
        text = f"视觉执行完成：{reason}（共 {rounds} 轮，执行 {len(actions)} 个动作）"
        return True, text, None

    error = verify.get("reason") or vision.get("error") or "视觉执行未完成任务"
    text = f"视觉执行失败：{error}"
    if interface_fail_note:
        error = f"{interface_fail_note}；{error}"
    return False, text, error


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    command = _parse_command(args)
    if command is None:
        _emit(False, "", "缺少 --command 参数")
        return 2

    if not command.strip():
        _emit(False, "", "任务文本为空")
        return 2

    try:
        ok, result, error = _handle(command)
    except Exception as exc:  # noqa: BLE001 - 桥接层兜底，绝不让脚本裸崩。
        _emit(False, "", _exception_summary(exc))
        return 1

    _emit(ok, result, error)
    return 0


if __name__ == "__main__":
    sys.exit(main())
