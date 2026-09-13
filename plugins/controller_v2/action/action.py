"""键鼠物理执行层 + 「接口优先」执行器骨架。

坐标语义（重要）：
    本层所有物理执行原语（move/click/double_click/right_click/drag/scroll 的位置参数）
    一律接收 **0~1000 千分比归一化坐标**，在执行前通过 ``core.coord.norm_to_physical``
    换算为物理像素并 clamp。严禁把千分比坐标直接当物理像素使用。

执行器：
    - execute(action)         对单个动作 dict 做护栏校验后分发到键鼠原语。
    - execute_interface(action)  「接口优先」骨架：维护 win32/cli/http 三类接口通道注册表，
      供后续 router C 层调用；本棒只提供骨架与分发，不实现具体业务接口。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional, Tuple

from core.coord import norm_to_physical
from core.guard import check_action
from core.screen import ensure_dpi_awareness

# ---------------------------------------------------------------------------
# 接口通道注册表（router C 层「接口优先」使用）
# ---------------------------------------------------------------------------
# 每个通道是一个 dict: name -> handler(action) -> dict
# handler 返回结果建议形如：
#   {"ok": bool, "reason": str, "result": Any(可选)}
# 若 handler 不存在，execute_interface 返回 ok=False + status="not_implemented"，
# router C 层可据此回退到键鼠物理兜底。
_INTERFACE_REGISTRY: Dict[str, Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]]] = {
    "win32": {},
    "cli": {},
    "http": {},
}

_INTERFACE_CHANNELS = tuple(_INTERFACE_REGISTRY.keys())


def register_interface_handler(
    channel: str,
    name: str,
    handler: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> None:
    """注册一个接口通道处理器。

    :param channel: "win32" | "cli" | "http"。
    :param name: 处理器名称（如 "window_activate" / "run_command" / "http_post"）。
    :param handler: 可调用对象，接收完整 action dict，返回结果 dict。
    :raises ValueError: channel 非法。
    """
    if channel not in _INTERFACE_REGISTRY:
        raise ValueError(f"非法接口通道: {channel}，可选 {list(_INTERFACE_CHANNELS)}")
    _INTERFACE_REGISTRY[channel][name] = handler


# ---------------------------------------------------------------------------
# 物理执行原语
# ---------------------------------------------------------------------------

def _norm(x: Any) -> float:
    return float(x)


def move(x: float, y: float, duration: float = 0.3) -> Tuple[int, int]:
    """移动鼠标到归一化坐标 (x, y)，返回物理像素坐标。"""
    ensure_dpi_awareness()
    px, py = norm_to_physical(x, y)
    import pyautogui

    pyautogui.moveTo(px, py, duration=duration)
    return px, py


def click(
    x: float,
    y: float,
    button: str = "left",
    clicks: int = 1,
    interval: float = 0.0,
) -> Tuple[int, int]:
    """在归一化坐标 (x, y) 处点击。"""
    ensure_dpi_awareness()
    px, py = norm_to_physical(x, y)
    import pyautogui

    pyautogui.click(px, py, button=button, clicks=int(clicks), interval=interval)
    return px, py


def double_click(x: float, y: float) -> Tuple[int, int]:
    """在归一化坐标 (x, y) 处双击。"""
    ensure_dpi_awareness()
    px, py = norm_to_physical(x, y)
    import pyautogui

    pyautogui.doubleClick(px, py)
    return px, py


def right_click(x: float, y: float) -> Tuple[int, int]:
    """在归一化坐标 (x, y) 处右键单击。"""
    ensure_dpi_awareness()
    px, py = norm_to_physical(x, y)
    import pyautogui

    pyautogui.rightClick(px, py)
    return px, py


def drag(
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    button: str = "left",
    duration: float = 0.5,
) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """从归一化起点拖拽到归一化终点。"""
    ensure_dpi_awareness()
    start_px, start_py = norm_to_physical(start_x, start_y)
    end_px, end_py = norm_to_physical(end_x, end_y)
    import pyautogui

    pyautogui.moveTo(start_px, start_py, duration=0.1)
    pyautogui.dragTo(end_px, end_py, button=button, duration=duration)
    return (start_px, start_py), (end_px, end_py)


def scroll(
    amount: int,
    x: Optional[float] = None,
    y: Optional[float] = None,
) -> None:
    """滚动鼠标滚轮；amount 为正向上滚动，为负向下滚动。

    若提供 (x, y) 归一化坐标，会先把鼠标移动到该点再滚动。
    """
    ensure_dpi_awareness()
    import pyautogui

    if x is not None and y is not None:
        px, py = norm_to_physical(x, y)
        pyautogui.moveTo(px, py, duration=0.0)
        pyautogui.scroll(int(amount), px, py)
    else:
        pyautogui.scroll(int(amount))


def key(key_name: str) -> None:
    """按下并释放单个键（如 'enter'、'esc'、'a'）。"""
    ensure_dpi_awareness()
    import pyautogui

    pyautogui.press(str(key_name))


def hotkey(*keys: str) -> None:
    """同时按下组合键（如 hotkey('ctrl', 'c')）。"""
    ensure_dpi_awareness()
    import pyautogui

    pyautogui.hotkey(*[str(k) for k in keys])


def type_text(text: str, interval: float = 0.02) -> None:
    """输入文本。优先使用 pynput（对 Unicode 更友好），失败回退 pyautogui。"""
    ensure_dpi_awareness()
    text = str(text)

    try:
        from pynput.keyboard import Controller as KeyboardController

        KeyboardController().type(text)
    except Exception:
        import pyautogui

        pyautogui.write(text, interval=interval)


# ---------------------------------------------------------------------------
# action dict -> 原语 分发
# ---------------------------------------------------------------------------

def _first(action: Dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in action and action[name] is not None:
            return action[name]
    return default


def _require_position(action: Dict[str, Any]) -> Tuple[float, float]:
    x = _first(action, "x", "norm_x", "nx")
    y = _first(action, "y", "norm_y", "ny")
    if x is None or y is None:
        raise ValueError("动作缺少归一化坐标字段 x/y")
    return _norm(x), _norm(y)


def _handle_mouse_move(action: Dict[str, Any]) -> None:
    x, y = _require_position(action)
    move(x, y, duration=float(_first(action, "duration", default=0.3)))


def _handle_mouse_click(action: Dict[str, Any]) -> None:
    x, y = _require_position(action)
    click(
        x,
        y,
        button=str(_first(action, "button", default="left")),
        clicks=int(_first(action, "clicks", default=1)),
        interval=float(_first(action, "interval", default=0.0)),
    )


def _handle_mouse_double_click(action: Dict[str, Any]) -> None:
    x, y = _require_position(action)
    double_click(x, y)


def _handle_mouse_right_click(action: Dict[str, Any]) -> None:
    x, y = _require_position(action)
    right_click(x, y)


def _handle_mouse_drag(action: Dict[str, Any]) -> None:
    start_x = _first(action, "from_x", "start_x")
    start_y = _first(action, "from_y", "start_y")
    end_x = _first(action, "to_x", "end_x")
    end_y = _first(action, "to_y", "end_y")
    if None in (start_x, start_y, end_x, end_y):
        raise ValueError("拖拽动作缺少 from_x/from_y/to_x/to_y 归一化坐标")
    drag(
        _norm(start_x),
        _norm(start_y),
        _norm(end_x),
        _norm(end_y),
        button=str(_first(action, "button", default="left")),
        duration=float(_first(action, "duration", default=0.5)),
    )


def _handle_scroll(action: Dict[str, Any]) -> None:
    amount = _first(action, "amount", "clicks", default=0)
    x = _first(action, "x", "norm_x")
    y = _first(action, "y", "norm_y")
    if x is not None and y is not None:
        scroll(int(amount), _norm(x), _norm(y))
    else:
        scroll(int(amount))


def _handle_key_input(action: Dict[str, Any]) -> None:
    key_name = _first(action, "key", "key_name")
    if key_name is None:
        raise ValueError("key_input 动作缺少 key 字段")
    key(str(key_name))


def _handle_hotkey(action: Dict[str, Any]) -> None:
    keys = _first(action, "keys", "key")
    if keys is None:
        raise ValueError("hotkey 动作缺少 keys 字段")
    if isinstance(keys, str):
        keys = [part for part in keys.replace(" ", "").split("+") if part]
    if not keys:
        raise ValueError("hotkey 动作 keys 为空")
    hotkey(*[str(k) for k in keys])


def _handle_type(action: Dict[str, Any]) -> None:
    text = _first(action, "text")
    if text is None:
        raise ValueError("type 动作缺少 text 字段")
    type_text(str(text), interval=float(_first(action, "interval", default=0.02)))


_ACTION_HANDLERS: Dict[str, Callable[[Dict[str, Any]], None]] = {
    "mouse_move": _handle_mouse_move,
    "mouse_click": _handle_mouse_click,
    "mouse_double_click": _handle_mouse_double_click,
    "mouse_right_click": _handle_mouse_right_click,
    "mouse_drag": _handle_mouse_drag,
    "scroll": _handle_scroll,
    "key_input": _handle_key_input,
    "hotkey": _handle_hotkey,
    "type": _handle_type,
}


def execute(action: Dict[str, Any]) -> Dict[str, Any]:
    """执行单个键鼠物理动作。

    :param action: 动作 dict，需含 ``type`` 字段；坐标字段使用 0~1000 归一化值。
    :returns: ``{"ok": bool, "reason": str, "action_type": str}``。
    """
    ok, reason = check_action(action)
    if not ok:
        return {
            "ok": False,
            "reason": reason,
            "action_type": str(action.get("type", "")),
        }

    action_type = str(action.get("type", "")).strip().lower()
    handler = _ACTION_HANDLERS.get(action_type)
    if handler is None:
        return {
            "ok": False,
            "reason": f"未注册的物理动作类型: {action_type}",
            "action_type": action_type,
        }

    try:
        handler(action)
    except Exception as exc:  # noqa: BLE001 - 执行异常统一返回失败结构。
        return {
            "ok": False,
            "reason": f"执行异常: {exc}",
            "action_type": action_type,
        }

    return {"ok": True, "reason": "ok", "action_type": action_type}


# ---------------------------------------------------------------------------
# 「接口优先」执行器骨架
# ---------------------------------------------------------------------------

def execute_interface(action: Dict[str, Any]) -> Dict[str, Any]:
    """接口优先执行器（骨架）。

    契约（供后续 router C 层调用）：
        action 支持两种形态：

        1) 扁平形态：
           {
             "type": "interface",
             "channel": "win32" | "cli" | "http",
             "name": "handler_name",
             "args": {...},          # 处理器自有参数
             "timeout": 15,          # 可选
             "fallback": {...}       # 可选：接口不可用时的键鼠兜底动作
           }

        2) 嵌套形态：
           {
             "type": "interface",
             "interface": {"channel": "win32", "name": "xxx", "args": {...}}
           }

    返回契约：
        - 通道非法：         {"ok": False, "status": "invalid_channel", "reason": ...}
        - 处理器未注册：     {"ok": False, "status": "not_implemented", "reason": ...}
        - 处理器已注册：     调用 handler(action) 并透传其返回 dict（附 channel/name/status）。
        - 处理器抛异常：     {"ok": False, "status": "handler_error", "reason": ...}

    本棒只负责注册表与分发；具体 win32/cli/http 业务接口由 router C 层通过
    ``register_interface_handler`` 注册后使用。
    """
    if not isinstance(action, dict):
        return {"ok": False, "status": "invalid_action", "reason": "接口动作必须是 dict"}

    nested = action.get("interface")
    if isinstance(nested, dict):
        channel = nested.get("channel")
        name = nested.get("name")
    else:
        channel = action.get("channel")
        name = action.get("name")

    channel = str(channel).strip().lower() if channel else ""
    name = str(name).strip() if name else ""

    if channel not in _INTERFACE_REGISTRY:
        return {
            "ok": False,
            "status": "invalid_channel",
            "channel": channel,
            "reason": f"非法接口通道: {channel or '(空)'}，可选 {list(_INTERFACE_CHANNELS)}",
        }

    handler = _INTERFACE_REGISTRY[channel].get(name)
    if handler is None:
        return {
            "ok": False,
            "status": "not_implemented",
            "channel": channel,
            "name": name,
            "reason": (
                f"接口处理器未注册: {channel}:{name}。"
                "本棒仅提供骨架，具体业务接口由 router C 层注册；可回退键鼠兜底。"
            ),
        }

    try:
        result = handler(action)
        if not isinstance(result, dict):
            result = {"ok": True, "result": result}
        result.setdefault("channel", channel)
        result.setdefault("name", name)
        result.setdefault("status", "executed")
        return result
    except Exception as exc:  # noqa: BLE001 - 处理器异常统一包装。
        return {
            "ok": False,
            "status": "handler_error",
            "channel": channel,
            "name": name,
            "reason": f"接口处理器执行异常: {exc}",
        }


__all__ = [
    "move",
    "click",
    "double_click",
    "right_click",
    "drag",
    "scroll",
    "key",
    "hotkey",
    "type_text",
    "execute",
    "execute_interface",
    "register_interface_handler",
]
