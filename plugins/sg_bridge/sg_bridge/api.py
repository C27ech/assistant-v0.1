"""sg_bridge.api — 工具注册表与实现（HTTP 与 MCP 共用同一份定义）

所有工具函数都返回 ``data`` 字典；:func:`call` 统一包成响应信封：

    {"ok": true,  "data": {...}}
    {"ok": false, "error": {"code": "ESTATE", "message": "..."}}

工具命名（外部调用用 ``sg_`` 前缀，MCP 里为 ``sg_<name>``）：
会话/窗口 → list_games, list_windows, attach, detach, status, focus, window_info, cursor_pos
键盘     → key_press, key_down, key_up, key_hold, key_combo, type_text
鼠标     → mouse_move, mouse_move_rel, click, click_norm, drag, drag_norm,
           scroll, mouse_down, mouse_up
语义动作 → game_bindings, game_action
工具     → sleep, sequence, screenshot, ping
"""
from __future__ import annotations

import time
from typing import Any, Callable

from .core import games as G
from .core import ime as IME
from .core import ocr as OCR
from .core import wininput as W

#: 会话状态：当前"附着"的窗口（AI 通过 attach 指定，后续操作默认作用于它）
STATE: dict[str, Any] = {
    "game": None,       # 作品 key（"SG"/"SG0"/...），可为 None（附着任意窗口）
    "hwnd": None,
    "attached_at": None,
    "mode": "control",  # 预留：read_only 时不执行输入
}

TOOLS: list[dict[str, Any]] = []


def tool(name: str, description: str, schema: "dict | None" = None):
    """把函数注册为可外部调用的工具。"""

    def deco(fn: Callable[..., Any]):
        TOOLS.append({
            "name": name,
            "description": description,
            "inputSchema": schema or {"type": "object", "properties": {}},
            "func": fn,
        })
        return fn

    return deco


def get_tool(name: str) -> "dict | None":
    for t in TOOLS:
        if t["name"] == name:
            return t
    return None


def tool_specs() -> list[dict]:
    """给 MCP / HTTP 的元数据（不含函数对象）。"""
    return [{"name": t["name"], "description": t["description"],
             "inputSchema": t["inputSchema"]} for t in TOOLS]


_ERROR_CODES = {
    KeyError: "EBADARG",
    ValueError: "EBADARG",
    RuntimeError: "ESTATE",
    OSError: "EWIN32",
    PermissionError: "EPERM",
}


def call(name: str, args: "dict | None" = None) -> dict:
    """执行工具并返回统一信封（不会抛异常）。"""
    t = get_tool(name)
    if not t:
        return {"ok": False, "error": {"code": "ENOTOOL", "message": f"未知工具: {name}"}}
    args = args or {}
    try:
        data = t["func"](**args)
        return {"ok": True, "data": data}
    except TypeError as exc:            # 参数不匹配
        return {"ok": False, "error": {"code": "EBADARG", "message": str(exc)}}
    except Exception as exc:            # noqa: BLE001
        code = _ERROR_CODES.get(type(exc), "EINTERNAL")
        return {"ok": False, "error": {"code": code, "message": f"{type(exc).__name__}: {exc}"}}


# --------------------------------------------------------------------------- #
# 会话 / 窗口
# --------------------------------------------------------------------------- #


@tool("list_games", "列出四部作品：安装是否存在、是否在运行、窗口句柄与客户区尺寸。")
def t_list_games() -> dict:
    return {"games": G.detect_running(), "dpi_mode": W.DPI_MODE,
            "virtual_screen": list(W.virtual_screen())}


@tool("list_windows", "列出当前可见的顶层窗口（可按标题/进程/类名过滤）。",
      {"type": "object", "properties": {
          "title_contains": {"type": "string"},
          "process_contains": {"type": "string"},
          "class_contains": {"type": "string"},
          "limit": {"type": "integer", "default": 50}}})
def t_list_windows(title_contains: "str | None" = None,
                   process_contains: "str | None" = None,
                   class_contains: "str | None" = None,
                   limit: int = 50) -> dict:
    wins = W.list_windows(title_contains, process_contains, class_contains)
    return {"count": len(wins), "windows": wins[:max(1, limit)]}


@tool("attach", "附着到目标窗口（作品 key 如 SG0 / 窗口句柄 / 标题片段）；默认切到前台，"
                "并**确保输入法为英文**（避免中文 IME 吃掉按键）。",
      {"type": "object", "properties": {
          "target": {"type": "string", "description": "SG/SG0/SGLBP/SGMDE 或 hwnd 或标题片段"},
          "focus": {"type": "boolean", "default": True},
          "ensure_english": {"type": "boolean", "default": True,
                             "description": "切到英文输入状态（强烈建议开启）"}},
       "required": ["target"]})
def t_attach(target: str, focus: bool = True, ensure_english: bool = True) -> dict:
    win = G.resolve_window(target)
    STATE["hwnd"] = win["hwnd"]
    key = str(target).strip().upper()
    STATE["game"] = key if key in G.GAMES else None
    STATE["attached_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    res = {"game": STATE["game"], "window": win}
    if focus:
        res["focus"] = W.focus_window(win["hwnd"])
    if ensure_english:
        res["ime"] = IME.ensure_english(win["hwnd"])
    return res


@tool("ime_status", "查看目标窗口的键盘布局与输入法状态（是否中文 IME、是否组字模式）。",
      {"type": "object", "properties": {"target": {"type": "string"}}})
def t_ime_status(target: "str | None" = None) -> dict:
    hwnd = _require_hwnd(target)
    return IME.status(hwnd)


@tool("ime_ensure_english", "把目标窗口切到英文输入（请求 en-US 布局 + 关闭 IME/设为半角英文）。",
      {"type": "object", "properties": {"target": {"type": "string"}}})
def t_ime_ensure_english(target: "str | None" = None) -> dict:
    return IME.ensure_english(_require_hwnd(target))


@tool("detach", "解除附着（不改动游戏，仅清空会话状态）。")
def t_detach() -> dict:
    old = dict(STATE)
    STATE.update({"game": None, "hwnd": None, "attached_at": None})
    return {"detached": True, "previous": old}


@tool("status", "当前会话与系统状态：附着的窗口、前台窗口、DPI 模式、光标位置、虚拟桌面。")
def t_status() -> dict:
    hwnd = STATE["hwnd"]
    win = None
    if hwnd:
        try:
            win = W.window_info(hwnd)
        except Exception:
            win = {"hwnd": hwnd, "missing": True}
    return {
        "attached": {"game": STATE["game"], "hwnd": hwnd, "since": STATE["attached_at"],
                     "mode": STATE["mode"]},
        "window": win,
        "foreground": W.foreground_window(),
        "cursor": list(W.cursor_pos()),
        "dpi_mode": W.DPI_MODE,
        "virtual_screen": list(W.virtual_screen()),
    }


@tool("focus", "把窗口切到前台并激活（不传 target 则用已附着的窗口）。",
      {"type": "object", "properties": {"target": {"type": "string"}}})
def t_focus(target: "str | None" = None) -> dict:
    win = G.resolve_window(target) if target else G.resolve_window(None, hwnd=STATE["hwnd"])
    return W.focus_window(win["hwnd"])


@tool("window_info", "读取指定窗口的详细信息（客户区屏幕坐标、是否前台等）。",
      {"type": "object", "properties": {"target": {"type": "string"}}})
def t_window_info(target: "str | None" = None) -> dict:
    win = G.resolve_window(target) if target else G.resolve_window(None, hwnd=STATE["hwnd"])
    return {"window": win}


@tool("cursor_pos", "当前光标屏幕坐标。")
def t_cursor_pos() -> dict:
    x, y = W.cursor_pos()
    return {"x": x, "y": y}


def _require_hwnd(target: "str | None" = None) -> int:
    """归一化坐标的基准窗口：优先参数，其次已附着窗口。"""
    if target:
        return G.resolve_window(target)["hwnd"]
    if STATE["hwnd"]:
        return STATE["hwnd"]
    fg = W.foreground_window()
    if not fg:
        raise RuntimeError("没有前台窗口，请先 attach")
    return fg["hwnd"]


# --------------------------------------------------------------------------- #
# 键盘
# --------------------------------------------------------------------------- #


def _focus_attached() -> "dict | None":
    """输入前**强制**把游戏切回前台（否则按键会打到别的窗口上）。

    注意：只做聚焦，不做 IME 检查（那一步较重，由 game_action/attach 负责）。
    """
    hwnd = STATE["hwnd"]
    if not hwnd:
        return None
    try:
        return W.focus_window(hwnd)
    except Exception:                              # noqa: BLE001
        return None


@tool("key_press", "敲一下键（按下→等待→抬起）：推进对话、确认、翻页等最常用。"
                   "调用前会自动把游戏切回前台。",
      {"type": "object", "properties": {
          "key": {"type": "string", "description": "enter/space/escape/up/down/ctrl/f5/a……"},
          "ms": {"type": "integer", "default": 40},
          "use_vk": {"type": "boolean", "default": False, "description": "改用虚拟键模式"}},
       "required": ["key"]})
def t_key_press(key: str, ms: int = 40, use_vk: bool = False) -> dict:
    _focus_attached()
    return W.key_press(key, ms=ms, use_vk=use_vk)


@tool("key_down", "按下键不抬起（配合 key_up 可做长按/组合键）。",
      {"type": "object", "properties": {"key": {"type": "string"},
                                        "use_vk": {"type": "boolean", "default": False}},
       "required": ["key"]})
def t_key_down(key: str, use_vk: bool = False) -> dict:
    _focus_attached()
    return W.key_down(key, use_vk=use_vk)


@tool("key_up", "抬起键。",
      {"type": "object", "properties": {"key": {"type": "string"},
                                        "use_vk": {"type": "boolean", "default": False}},
       "required": ["key"]})
def t_key_up(key: str, use_vk: bool = False) -> dict:
    return W.key_up(key, use_vk=use_vk)


@tool("key_hold", "长按某键 ms 毫秒（快进/SKIP 常用）。",
      {"type": "object", "properties": {"key": {"type": "string"},
                                        "ms": {"type": "integer", "default": 800}},
       "required": ["key"]})
def t_key_hold(key: str, ms: int = 800) -> dict:
    return W.key_hold(key, ms=ms)


@tool("key_combo", "组合键，如 [\"ctrl\",\"s\"]。",
      {"type": "object", "properties": {
          "keys": {"type": "array", "items": {"type": "string"}},
          "ms": {"type": "integer", "default": 40}},
       "required": ["keys"]})
def t_key_combo(keys: list, ms: int = 40) -> dict:
    return W.key_combo(keys, ms=ms)


@tool("type_text", "输入文本（逐字符，可输入中文；用于窗口/命名框，不适用于游戏内 DirectInput）。",
      {"type": "object", "properties": {"text": {"type": "string"},
                                        "per_char_ms": {"type": "integer", "default": 12}},
       "required": ["text"]})
def t_type_text(text: str, per_char_ms: int = 12) -> dict:
    return W.type_text(text, per_char_ms=per_char_ms)


# --------------------------------------------------------------------------- #
# 鼠标
# --------------------------------------------------------------------------- #


@tool("mouse_move", "把光标移动到屏幕绝对坐标。",
      {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
       "required": ["x", "y"]})
def t_mouse_move(x: int, y: int) -> dict:
    return W.mouse_move(x, y)


@tool("mouse_move_rel", "相对移动光标（像素）。",
      {"type": "object", "properties": {"dx": {"type": "integer"}, "dy": {"type": "integer"}},
       "required": ["dx", "dy"]})
def t_mouse_move_rel(dx: int, dy: int) -> dict:
    return W.mouse_move_rel(dx, dy)


@tool("click", "点击屏幕坐标（可指定按键与次数）。",
      {"type": "object", "properties": {
          "x": {"type": "integer"}, "y": {"type": "integer"},
          "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
          "count": {"type": "integer", "default": 1},
          "ms": {"type": "integer", "default": 30}}})
def t_click(x: "int | None" = None, y: "int | None" = None, button: str = "left",
            count: int = 1, ms: int = 30) -> dict:
    _focus_attached()
    return W.click(x, y, button=button, count=count, ms=ms)


@tool("click_norm", "按**窗口客户区归一化坐标**(0..1)点击——分辨率无关，推荐使用。",
      {"type": "object", "properties": {
          "nx": {"type": "number"}, "ny": {"type": "number"},
          "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
          "count": {"type": "integer", "default": 1},
          "target": {"type": "string", "description": "不传则用已附着窗口"}},
       "required": ["nx", "ny"]})
def t_click_norm(nx: float, ny: float, button: str = "left", count: int = 1,
                 target: "str | None" = None) -> dict:
    _focus_attached()
    return W.click_norm(_require_hwnd(target), nx, ny, button=button, count=count)


@tool("drag", "按住拖动（滑条/地图）。",
      {"type": "object", "properties": {
          "x1": {"type": "integer"}, "y1": {"type": "integer"},
          "x2": {"type": "integer"}, "y2": {"type": "integer"},
          "duration_ms": {"type": "integer", "default": 300}},
       "required": ["x1", "y1", "x2", "y2"]})
def t_drag(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> dict:
    return W.drag(x1, y1, x2, y2, duration_ms=duration_ms)


@tool("drag_norm", "按客户区归一化坐标拖动。",
      {"type": "object", "properties": {
          "nx1": {"type": "number"}, "ny1": {"type": "number"},
          "nx2": {"type": "number"}, "ny2": {"type": "number"},
          "duration_ms": {"type": "integer", "default": 300},
          "target": {"type": "string"}},
       "required": ["nx1", "ny1", "nx2", "ny2"]})
def t_drag_norm(nx1: float, ny1: float, nx2: float, ny2: float,
                duration_ms: int = 300, target: "str | None" = None) -> dict:
    win = G.resolve_window(target) if target else G.resolve_window(None, hwnd=_require_hwnd())
    cx, cy, w, h = win["client_rect"]

    def to_px(nx: float, ny: float) -> tuple[int, int]:
        return cx + int(nx * (w - 1)), cy + int(ny * (h - 1))

    x1, y1 = to_px(nx1, ny1)
    x2, y2 = to_px(nx2, ny2)
    res = W.drag(x1, y1, x2, y2, duration_ms=duration_ms)
    res.update({"norm": [[nx1, ny1], [nx2, ny2]], "client_rect": win["client_rect"]})
    return res


@tool("scroll", "滚轮（>0 向上/左，<0 向下/右；1 格 = 120）。",
      {"type": "object", "properties": {
          "amount": {"type": "integer"},
          "x": {"type": "integer"}, "y": {"type": "integer"},
          "horizontal": {"type": "boolean", "default": False}},
       "required": ["amount"]})
def t_scroll(amount: int, x: "int | None" = None, y: "int | None" = None,
             horizontal: bool = False) -> dict:
    return W.scroll(amount, x, y, horizontal=horizontal)


@tool("mouse_down", "按下鼠标键（不抬起）。",
      {"type": "object", "properties": {"button": {"type": "string", "default": "left"}}})
def t_mouse_down(button: str = "left") -> dict:
    return W.mouse_down(button)


@tool("mouse_up", "抬起鼠标键。",
      {"type": "object", "properties": {"button": {"type": "string", "default": "left"}}})
def t_mouse_up(button: str = "left") -> dict:
    return W.mouse_up(button)


# --------------------------------------------------------------------------- #
# 语义动作 / 批量脚本
# --------------------------------------------------------------------------- #

#: 支持的步骤操作（供 sequence 与 bindings.json 使用）
OPS = ("key_press", "key_down", "key_up", "click", "click_norm", "scroll",
       "mouse_move", "sleep", "screenshot", "game_action")

_CURRENT_WINDOW: dict[str, Any] = {"hwnd": None}


def _apply_op(op: dict, default_hwnd: "int | None" = None) -> dict:
    """执行单个步骤；``op`` 形如 ``{"op": "key_press", "key": "enter", "ms": 40}``。"""
    if not isinstance(op, dict):
        raise TypeError('步骤必须是对象，如 {"op":"key_press","key":"enter"}')
    kind = str(op.get("op", "")).lower()
    hwnd = op.get("hwnd") or default_hwnd or _CURRENT_WINDOW["hwnd"]
    if kind == "key_press":
        return {"op": kind, "result": W.key_press(op.get("key"), ms=int(op.get("ms", 40)))}
    if kind == "key_down":
        return {"op": kind, "result": W.key_down(op.get("key"))}
    if kind == "key_up":
        return {"op": kind, "result": W.key_up(op.get("key"))}
    if kind == "click":
        return {"op": kind, "result": W.click(op.get("x"), op.get("y"),
                                              button=op.get("button", "left"),
                                              count=int(op.get("count", 1)),
                                              ms=int(op.get("ms", 30)))}
    if kind == "click_norm":
        if not hwnd:
            raise RuntimeError("click_norm 需要窗口：请先 attach 或传 target")
        return {"op": kind, "result": W.click_norm(hwnd, op.get("nx"), op.get("ny"),
                                                   button=op.get("button", "left"),
                                                   count=int(op.get("count", 1)))}
    if kind == "scroll":
        return {"op": kind, "result": W.scroll(int(op.get("amount", 120)),
                                               op.get("x"), op.get("y"))}
    if kind == "mouse_move":
        return {"op": kind, "result": W.mouse_move(int(op.get("x")), int(op.get("y")))}
    if kind == "sleep":
        return {"op": kind, "result": W.sleep_ms(int(op.get("ms", 100)))}
    if kind == "screenshot":
        path = op.get("path") or "shot.png"
        mw = op.get("max_width")
        if hwnd and op.get("client_only", True):
            return {"op": kind, "result": W.screenshot_window(path, hwnd, max_width=mw)}
        return {"op": kind, "result": W.screenshot(path, max_width=mw)}
    if kind == "game_action":
        return {"op": kind, "result": t_game_action(op.get("action"),
                                                    game=op.get("game"),
                                                    ms=op.get("ms"),
                                                    focus=bool(op.get("focus", True)))}
    raise KeyError(f"未知步骤 op={kind!r}（支持：{', '.join(OPS)}）")


def _focus_if_game(game: "str | None", target: "str | None", focus: bool) -> "dict | None":
    """有指明目标就把窗口切到前台，保证输入落到游戏里；同时确保**英文输入**。"""
    if not focus:
        return None
    win = None
    if target:
        win = G.resolve_window(target)
    elif game:
        wins = G.find_game_windows(game)
        win = wins[0] if wins else None
    elif STATE["hwnd"]:
        win = W.window_info(STATE["hwnd"])
    if not win:
        return None
    _CURRENT_WINDOW["hwnd"] = win["hwnd"]
    res = W.focus_window(win["hwnd"])
    # 中文输入法会吃掉按键（Z/X/ENTER 等），每次操作前都兜底切英文
    try:
        res["ime"] = IME.ensure_english(win["hwnd"])
    except Exception as exc:                      # noqa: BLE001
        res["ime_error"] = f"{type(exc).__name__}: {exc}"
    return res


@tool("game_bindings", "查看语义动作表（advance/skip/menu/… 分别对应哪些按键），含置信度与备注。",
      {"type": "object", "properties": {"game": {"type": "string"}}})
def t_game_bindings(game: "str | None" = None) -> dict:
    return {"game": game, "bindings": G.bindings(game),
            "config_path": G.BINDINGS_PATH, "ops": list(OPS)}


@tool("game_action", "执行语义动作（advance/confirm/back/menu/help/tips/skip/auto/phone/save/…）。"
                     "绑定来源＝游戏内 HELP 画面。危险动作（quit/title/fullscreen/quick_save/quick_load）"
                     "需显式 allow_dangerous=True。",
      {"type": "object", "properties": {
          "action": {"type": "string"},
          "game": {"type": "string", "description": "SG/SG0/SGLBP/SGMDE，可选"},
          "target": {"type": "string", "description": "窗口句柄或标题片段，可选"},
          "ms": {"type": "integer", "description": "覆盖步骤时长（快进常需要）"},
          "focus": {"type": "boolean", "default": True},
          "allow_dangerous": {"type": "boolean", "default": False,
                              "description": "确认执行危险动作（退出/回标题/全屏/覆盖存档）"}},
       "required": ["action"]})
def t_game_action(action: str, game: "str | None" = None, target: "str | None" = None,
                  ms: "int | None" = None, focus: bool = True,
                  allow_dangerous: bool = False) -> dict:
    table = G.bindings(game)
    if action not in table:
        raise KeyError(f"未知动作: {action!r}（可用：{', '.join(sorted(table))}）")
    entry = table[action]
    if entry.get("dangerous") and not allow_dangerous:
        raise PermissionError(
            f"动作 '{action}' 会显著改变游戏状态（{entry.get('note', '')}）；"
            f"如确需执行，请传 allow_dangerous=True")
    focus_res = _focus_if_game(game, target, focus)
    results = []
    for op in entry.get("steps", []):
        step = dict(op)
        if ms is not None and step.get("op") == "key_press":
            step["ms"] = int(ms)
        results.append(_apply_op(step, default_hwnd=_CURRENT_WINDOW["hwnd"]))
    return {"action": action, "confidence": entry.get("confidence"),
            "dangerous": bool(entry.get("dangerous")), "note": entry.get("note"),
            "focus": focus_res, "steps": results}


@tool("sequence", "按顺序批量执行多个步骤（一次往返完成多步）；op 见 game_bindings 的 ops。",
      {"type": "object", "properties": {
          "steps": {"type": "array", "items": {"type": "object"}},
          "game": {"type": "string"},
          "target": {"type": "string"},
          "focus": {"type": "boolean", "default": True},
          "stop_on_error": {"type": "boolean", "default": True}},
       "required": ["steps"]})
def t_sequence(steps: list, game: "str | None" = None, target: "str | None" = None,
               focus: bool = True, stop_on_error: bool = True) -> dict:
    focus_res = _focus_if_game(game, target, focus)
    results, errors = [], []
    for i, op in enumerate(steps):
        try:
            results.append(_apply_op(op, default_hwnd=_CURRENT_WINDOW["hwnd"]))
        except Exception as exc:  # noqa: BLE001
            errors.append({"index": i, "op": op, "error": f"{type(exc).__name__}: {exc}"})
            if stop_on_error:
                break
    return {"executed": len(results), "of": len(steps), "focus": focus_res,
            "results": results, "errors": errors}


@tool("sleep", "等待若干毫秒（脚本节奏）。",
      {"type": "object", "properties": {"ms": {"type": "integer", "default": 200}}})
def t_sleep(ms: int = 200) -> dict:
    return W.sleep_ms(ms)


@tool("screenshot", "截图保存为图片（可选功能；本方案不依赖视觉，仅供抽查/留证）。"
                    "可传 max_width 缩小成便于查看的小图、路径用 .jpg 更省空间。",
      {"type": "object", "properties": {
          "path": {"type": "string"},
          "target": {"type": "string"},
          "client_only": {"type": "boolean", "default": True},
          "max_width": {"type": "integer", "description": "按比例缩小到该宽度内（如 1280）"}},
       "required": ["path"]})
def t_screenshot(path: str, target: "str | None" = None, client_only: bool = True,
                 max_width: "int | None" = None) -> dict:
    if target or STATE["hwnd"]:
        hwnd = _require_hwnd(target)
        if client_only:
            return W.screenshot_window(path, hwnd, max_width=max_width)
        x, y, w, h = W.window_info(hwnd)["window_rect"]
        return W.screenshot(path, (x, y, x + w, y + h), max_width=max_width)
    return W.screenshot(path, max_width=max_width)


@tool("read_screen", "截取游戏窗口并 OCR，返回屏幕上每一行文字及其坐标"
                     "（bbox 为窗口客户区像素坐标）。这是「看得懂画面」的核心工具。",
      {"type": "object", "properties": {
          "target": {"type": "string", "description": "不传则用已附着窗口"},
          "crop": {"type": "string", "description": "只识别某区域：x,y,w,h（像素或 0~1 比例）"},
          "scale": {"type": "number", "default": 2.0, "description": "放大倍数，利于小字识别"},
          "lang": {"type": "string", "default": "zh-Hans-CN"},
          "save_path": {"type": "string", "description": "截图另存路径（可选）"}}})
def t_read_screen(target: "str | None" = None, crop: "str | None" = None, scale: float = 2.0,
                  lang: str = "zh-Hans-CN", save_path: "str | None" = None) -> dict:
    res = OCR.read_screen(_require_hwnd(target), crop=crop, scale=scale, lang=lang,
                          save_path=save_path)
    return {"shot": res["shot"], "elapsed_ms": res["elapsed_ms"],
            "lines": [{"text": ln["text"], "bbox": ln["bbox"]} for ln in res["lines"]]}


@tool("find_text", "在屏幕上查找文字（OCR），返回匹配行与坐标，便于随后点击。",
      {"type": "object", "properties": {
          "text": {"type": "string"},
          "target": {"type": "string"},
          "crop": {"type": "string"},
          "scale": {"type": "number", "default": 2.0},
          "exact": {"type": "boolean", "default": False}},
       "required": ["text"]})
def t_find_text(text: str, target: "str | None" = None, crop: "str | None" = None,
                scale: float = 2.0, exact: bool = False) -> dict:
    return OCR.find_text(_require_hwnd(target), needle=text, crop=crop, scale=scale, exact=exact)


@tool("click_text", "在屏幕上找到该文字并点击它的中心（OCR 定位 → 进程内鼠标点击）。",
      {"type": "object", "properties": {
          "text": {"type": "string"},
          "target": {"type": "string"},
          "crop": {"type": "string"},
          "scale": {"type": "number", "default": 2.0},
          "exact": {"type": "boolean", "default": False},
          "button": {"type": "string", "default": "left"},
          "index": {"type": "integer", "default": 0, "description": "命中多个时选第几个"}},
       "required": ["text"]})
def t_click_text(text: str, target: "str | None" = None, crop: "str | None" = None,
                 scale: float = 2.0, exact: bool = False, button: str = "left",
                 index: int = 0) -> dict:
    hwnd = _require_hwnd(target)
    found = OCR.find_text(hwnd, needle=text, crop=crop, scale=scale, exact=exact)
    if not found["hits"]:
        raise RuntimeError(f"屏幕上找不到文字: {text!r}（当前识别到 {len(found['lines'])} 行）")
    idx = max(0, min(index, len(found["hits"]) - 1))
    hit = found["hits"][idx]
    cx, cy, cw, ch = W.window_info(hwnd)["client_rect"]
    x = cx + hit["center"][0]
    y = cy + hit["center"][1]
    res = W.click(x, y, button=button)
    res.update({"matched_text": hit["text"], "hit_index": idx, "hits": len(found["hits"]),
                "client_rect": [cx, cy, cw, ch], "ocr_lines": found["lines"]})
    return res


@tool("ping", "健康检查：版本、工具数量、DPI 模式、虚拟桌面。")
def t_ping() -> dict:
    from . import __version__
    return {"version": __version__, "tools": len(TOOLS),
            "dpi_mode": W.DPI_MODE, "virtual_screen": list(W.virtual_screen())}