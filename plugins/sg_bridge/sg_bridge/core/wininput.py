"""sg_bridge.core.wininput — Windows 输入注入与窗口定位（模拟真人操作）

设计要点
--------
* 只用 ``ctypes``，不依赖任何第三方库（PIL 仅用于可选的截图功能）。
* 导入时立刻设置 **DPI 感知**：本机显示缩放为 125%，若进程不感知 DPI，
  查询到的窗口/屏幕坐标会被系统按缩放折算（1920x1080 → 1536x864），
  导致点击位置偏移 1.25 倍。这是本方案最容易踩的坑。
* 键盘默认用 **scancode 模式**（``KEYEVENTF_SCANCODE``）：DirectInput 类游戏
  更可靠；可用 ``use_vk=True`` 回退到虚拟键模式。
* 鼠标用 **绝对坐标**（虚拟桌面归一化 0..65535），避免相对移动累积误差。
"""
from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Iterable, Sequence

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --------------------------------------------------------------------------- #
# DPI 感知（必须在查询任何窗口/屏幕坐标之前完成）
# --------------------------------------------------------------------------- #


def set_dpi_aware() -> str:
    """把当前进程设为 per-monitor DPI 感知，返回实际生效的模式名。"""
    # Windows 10 1703+：PER_MONITOR_AWARE_V2 = -4
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per_monitor_v2"
    except Exception:
        pass
    try:  # Windows 8.1+
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        return "per_monitor"
    except Exception:
        pass
    try:  # Vista+
        if user32.SetProcessDPIAware():
            return "system"
    except Exception:
        pass
    return "none"


DPI_MODE = set_dpi_aware()

# --------------------------------------------------------------------------- #
# Win32 常量
# --------------------------------------------------------------------------- #

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000

WHEEL_DELTA = 120

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

SW_RESTORE = 9
SW_SHOW = 5

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong

# --------------------------------------------------------------------------- #
# Win32 结构体
# --------------------------------------------------------------------------- #


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.SendInput.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
user32.GetSystemMetrics.restype = ctypes.c_int

# --- Win32 原型声明 -------------------------------------------------------- #
# 64 位下必须声明返回类型，否则句柄会被截断成 32 位（会导致崩溃或静默失败）。
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = (wintypes.HWND,)
user32.BringWindowToTop.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = (wintypes.HWND, ctypes.c_int)
user32.ShowWindow.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = (wintypes.DWORD, wintypes.DWORD, wintypes.BOOL)
user32.AttachThreadInput.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = (ctypes.POINTER(wintypes.POINT),)
user32.GetCursorPos.restype = wintypes.BOOL
user32.OpenClipboard.argtypes = (wintypes.HWND,)
user32.OpenClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = (wintypes.UINT,)
user32.GetClipboardData.restype = wintypes.HANDLE
user32.CloseClipboard.restype = wintypes.BOOL
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.QueryFullProcessImageNameW.argtypes = (
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


def _send(*inputs: INPUT) -> int:
    """调用 SendInput 发送一批事件，返回成功数量。"""
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        raise OSError(f"SendInput 失败（{sent}/{n}），错误码 {ctypes.get_last_error()}")
    return sent


def _keyboard_input(wVk: int, wScan: int, flags: int) -> INPUT:
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.ki = KEYBDINPUT(wVk=wVk, wScan=wScan, dwFlags=flags, time=0, dwExtraInfo=0)
    return item


def _mouse_input(dx: int, dy: int, data: int, flags: int) -> INPUT:
    item = INPUT()
    item.type = INPUT_MOUSE
    item.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=data, dwFlags=flags, time=0, dwExtraInfo=0)
    return item


# --------------------------------------------------------------------------- #
# 按键表
# --------------------------------------------------------------------------- #

#: 名称 → 虚拟键码
VK_TABLE: dict[str, int] = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "pause": 0x13,
    "capslock": 0x14, "esc": 0x1B, "escape": 0x1B, "space": 0x20,
    "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22,
    "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E, "del": 0x2E,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3,
    "lalt": 0xA4, "ralt": 0xA5, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64,
    "num5": 0x65, "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
    "multiply": 0x6A, "add": 0x6B, "subtract": 0x6D, "decimal": 0x6E,
    "divide": 0x6F, "numlock": 0x90, "scrolllock": 0x91,
    "semicolon": 0xBA, "equals": 0xBB, "comma": 0xBC, "minus": 0xBD,
    "period": 0xBE, "slash": 0xBF, "backtick": 0xC0,
    "bracketleft": 0xDB, "backslash": 0xDC, "bracketright": 0xDD,
    "quote": 0xDE,
}
for _i in range(1, 25):                       # F1..F24
    VK_TABLE[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":       # a..z
    VK_TABLE[_c] = ord(_c.upper())
for _d in "0123456789":                       # 0..9
    VK_TABLE[_d] = ord(_d)

#: 需要带 EXTENDEDKEY 标志的键
EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28,
    0x2C, 0x2D, 0x2E, 0x6F, 0x90, 0xA3, 0xA5, 0x5B, 0x5C, 0x5D,
}

MAPVK_VK_TO_VSC = 0


def resolve_key(key: "str | int") -> tuple[int, int, bool]:
    """把键名解析为 ``(vk, scancode, extended)``。"""
    if isinstance(key, int):
        vk = key
    else:
        name = key.strip().lower()
        if name not in VK_TABLE:
            raise KeyError(f"未知键名: {key!r}")
        vk = VK_TABLE[name]
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    return vk, scan, vk in EXTENDED_VKS


# --------------------------------------------------------------------------- #
# 键盘
# --------------------------------------------------------------------------- #


def key_down(key: "str | int", use_vk: bool = False) -> dict:
    """按下按键（不抬起）。用于长按（如游戏内按住 Ctrl 快进）。"""
    vk, scan, ext = resolve_key(key)
    flags = 0 if use_vk else KEYEVENTF_SCANCODE
    if ext:
        flags |= KEYEVENTF_EXTENDEDKEY
    _send(_keyboard_input(vk if use_vk else 0, scan, flags))
    return {"key": key, "vk": vk, "scan": scan, "extended": ext, "state": "down"}


def key_up(key: "str | int", use_vk: bool = False) -> dict:
    """抬起按键。"""
    vk, scan, ext = resolve_key(key)
    flags = KEYEVENTF_KEYUP | (0 if use_vk else KEYEVENTF_SCANCODE)
    if ext:
        flags |= KEYEVENTF_EXTENDEDKEY
    _send(_keyboard_input(vk if use_vk else 0, scan, flags))
    return {"key": key, "vk": vk, "scan": scan, "extended": ext, "state": "up"}


def key_press(key: "str | int", ms: int = 40, use_vk: bool = False) -> dict:
    """按一下（按下 → 等 ms → 抬起）——等价于真人敲键。"""
    t0 = time.perf_counter()
    key_down(key, use_vk=use_vk)
    time.sleep(max(0, ms) / 1000.0)
    key_up(key, use_vk=use_vk)
    return {"key": key, "hold_ms": ms,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}


def key_hold(key: "str | int", ms: int = 800, use_vk: bool = False) -> dict:
    """长按 ms 毫秒（快进/SKIP 通常就是长按某个键）。"""
    return key_press(key, ms=ms, use_vk=use_vk)


def key_combo(keys: Sequence["str | int"], ms: int = 40, pause_ms: int = 20,
              use_vk: bool = False) -> dict:
    """组合键，如 ``["ctrl", "s"]``：依次按下，逆序抬起。"""
    t0 = time.perf_counter()
    pressed: list = []
    for k in keys:
        key_down(k, use_vk=use_vk)
        pressed.append(k)
        time.sleep(max(0, pause_ms) / 1000.0)
    time.sleep(max(0, ms) / 1000.0)
    for k in reversed(pressed):
        key_up(k, use_vk=use_vk)
        time.sleep(max(0, pause_ms) / 1000.0)
    return {"keys": list(keys), "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}


def type_text(text: str, per_char_ms: int = 12) -> dict:
    """逐字符输入文本（``KEYEVENTF_UNICODE``，可输入中文）。

    只适用于普通 Windows 窗口（如记事本、存档命名框）。游戏内的按键响应
    走 DirectInput，请用 :func:`key_press`。
    """
    for ch in text:
        code = ord(ch)
        _send(_keyboard_input(0, code, KEYEVENTF_UNICODE),
              _keyboard_input(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
        time.sleep(max(0, per_char_ms) / 1000.0)
    return {"text": text, "chars": len(text)}


# --------------------------------------------------------------------------- #
# 鼠标
# --------------------------------------------------------------------------- #

_BUTTON_FLAGS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def virtual_screen() -> tuple[int, int, int, int]:
    """虚拟桌面 ``(x, y, w, h)``（多显示器时为并集）。"""
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def cursor_pos() -> tuple[int, int]:
    """当前光标屏幕坐标。"""
    pt = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        raise OSError("GetCursorPos 失败")
    return pt.x, pt.y


def mouse_move(x: int, y: int) -> dict:
    """把光标移动到屏幕绝对坐标 (x, y)。"""
    vx, vy, vw, vh = virtual_screen()
    nx = int(round((x - vx) * 65535 / max(1, vw - 1)))
    ny = int(round((y - vy) * 65535 / max(1, vh - 1)))
    _send(_mouse_input(nx, ny, 0, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE))
    return {"x": x, "y": y, "cursor": list(cursor_pos())}


def mouse_move_rel(dx: int, dy: int) -> dict:
    """相对移动光标（像素）。"""
    _send(_mouse_input(dx, dy, 0, MOUSEEVENTF_MOVE))
    return {"dx": dx, "dy": dy, "cursor": list(cursor_pos())}


def mouse_down(button: str = "left") -> dict:
    """按下鼠标键（不抬起）。"""
    if button not in _BUTTON_FLAGS:
        raise KeyError(f"未知鼠标键: {button!r}（left/right/middle）")
    _send(_mouse_input(0, 0, 0, _BUTTON_FLAGS[button][0]))
    return {"button": button, "state": "down"}


def mouse_up(button: str = "left") -> dict:
    """抬起鼠标键。"""
    if button not in _BUTTON_FLAGS:
        raise KeyError(f"未知鼠标键: {button!r}（left/right/middle）")
    _send(_mouse_input(0, 0, 0, _BUTTON_FLAGS[button][1]))
    return {"button": button, "state": "up"}


def click(x: "int | None" = None, y: "int | None" = None, button: str = "left",
          count: int = 1, ms: int = 30, move_first: bool = True) -> dict:
    """点击（可先移动到目标坐标）。"""
    t0 = time.perf_counter()
    if x is not None and y is not None and move_first:
        mouse_move(x, y)
        time.sleep(0.02)
    for i in range(max(1, count)):
        mouse_down(button)
        time.sleep(max(0, ms) / 1000.0)
        mouse_up(button)
        if i + 1 < count:
            time.sleep(0.04)
    return {"button": button, "count": count, "x": x, "y": y,
            "cursor": list(cursor_pos()),
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1)}


def drag(x1: int, y1: int, x2: int, y2: int, button: str = "left",
         duration_ms: int = 300, steps: int = 12) -> dict:
    """按住拖动（滑条/地图用）。"""
    steps = max(2, steps)
    mouse_move(x1, y1)
    time.sleep(0.03)
    mouse_down(button)
    try:
        for i in range(1, steps + 1):
            mx = x1 + (x2 - x1) * i // steps
            my = y1 + (y2 - y1) * i // steps
            mouse_move(mx, my)
            time.sleep(max(1, duration_ms // steps) / 1000.0)
    finally:
        mouse_up(button)
    return {"from": [x1, y1], "to": [x2, y2], "button": button, "steps": steps}


def scroll(amount: int, x: "int | None" = None, y: "int | None" = None,
           horizontal: bool = False) -> dict:
    """滚轮：``amount`` >0 向上/向左，<0 向下/向右（1 格 = 120）。"""
    if x is not None and y is not None:
        mouse_move(x, y)
        time.sleep(0.02)
    _send(_mouse_input(0, 0, int(amount), MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL))
    return {"amount": amount, "horizontal": horizontal}


def sleep_ms(ms: int) -> dict:
    """等待毫秒（脚本节奏用）。"""
    time.sleep(max(0, ms) / 1000.0)
    return {"slept_ms": ms}


# --------------------------------------------------------------------------- #
# 窗口
# --------------------------------------------------------------------------- #

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumWindows.argtypes = (WNDENUMPROC, wintypes.LPARAM)
user32.EnumWindows.restype = wintypes.BOOL


def _window_text(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(hwnd, buf, n + 2)
    return buf.value


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid_of(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _process_name(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(h)


def _rects(hwnd: int) -> dict:
    wr = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    cr = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return {
        "window_rect": [wr.left, wr.top, wr.right - wr.left, wr.bottom - wr.top],
        "client_rect": [pt.x, pt.y, cr.right, cr.bottom],   # 客户区屏幕坐标 (x, y, w, h)
        "client_size": [cr.right, cr.bottom],
    }


def window_info(hwnd: int) -> dict:
    """单个窗口的完整信息（含客户区屏幕坐标，用于归一化点击）。"""
    info = {
        "hwnd": int(hwnd),
        "title": _window_text(hwnd),
        "class": _class_name(hwnd),
        "pid": _pid_of(hwnd),
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "minimized": bool(user32.IsIconic(hwnd)),
        "foreground": user32.GetForegroundWindow() == hwnd,
    }
    info["process"] = _process_name(info["pid"])
    info.update(_rects(hwnd))
    return info


def list_windows(title_contains: "str | None" = None,
                 process_contains: "str | None" = None,
                 class_contains: "str | None" = None,
                 visible_only: bool = True) -> list[dict]:
    """列出顶层窗口（可按标题/进程/类名过滤）。"""
    out: list[dict] = []

    def cb(hwnd, _lparam):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        info = window_info(hwnd)
        if title_contains and title_contains.lower() not in info["title"].lower():
            return True
        if process_contains and process_contains.lower() not in info["process"].lower():
            return True
        if class_contains and class_contains.lower() not in info["class"].lower():
            return True
        out.append(info)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def find_window(title_contains: "str | None" = None,
                process_contains: "str | None" = None,
                class_contains: "str | None" = None) -> "dict | None":
    """找第一个匹配的窗口；优先返回非最小化的。"""
    hits = list_windows(title_contains, process_contains, class_contains)
    if not hits:
        return None
    for h in hits:
        if not h["minimized"]:
            return h
    return hits[0]


def foreground_window() -> "dict | None":
    """当前前台窗口信息。"""
    hwnd = user32.GetForegroundWindow()
    return window_info(hwnd) if hwnd else None


def focus_window(hwnd: int, tries: int = 6, restore: bool = True) -> dict:
    """把窗口切到前台并激活（含 Windows 前台锁的常见绕过手段）。

    游戏必须处于前台，SendInput 的按键才会送进游戏窗口。
    """
    hwnd = int(hwnd)
    if restore and user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    elif restore:
        user32.ShowWindow(hwnd, SW_SHOW)

    self_tid = kernel32.GetCurrentThreadId()
    used = 0
    for used in range(1, max(1, tries) + 1):
        if user32.GetForegroundWindow() == hwnd:
            return {"ok": True, "hwnd": hwnd, "tries_used": used, "foreground": True}
        fg = user32.GetForegroundWindow()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and self_tid != fg_tid:
            attached = bool(user32.AttachThreadInput(self_tid, fg_tid, True))
        # 轻敲 ALT 可解除 Windows 前台锁
        _send(_keyboard_input(0, 0x38, 0), _keyboard_input(0, 0x38, KEYEVENTF_KEYUP))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if attached:
            user32.AttachThreadInput(self_tid, fg_tid, False)
        time.sleep(0.08)

    ok = user32.GetForegroundWindow() == hwnd
    return {"ok": ok, "hwnd": hwnd, "foreground": ok, "tries_used": used,
            "note": "" if ok else "前台切换失败；可手动点一下游戏窗口后重试"}


def client_to_screen(hwnd: int, x: int, y: int) -> tuple[int, int]:
    """客户区坐标 → 屏幕坐标。"""
    pt = wintypes.POINT(int(x), int(y))
    if not user32.ClientToScreen(int(hwnd), ctypes.byref(pt)):
        raise OSError("ClientToScreen 失败")
    return pt.x, pt.y


def click_norm(hwnd: int, nx: float, ny: float, button: str = "left",
               count: int = 1, ms: int = 30) -> dict:
    """按**客户区归一化坐标**点击（0..1）：与分辨率无关，推荐使用。"""
    info = window_info(hwnd)
    cx, cy, w, h = info["client_rect"]
    x = cx + int(round(max(0.0, min(1.0, nx)) * (w - 1)))
    y = cy + int(round(max(0.0, min(1.0, ny)) * (h - 1)))
    res = click(x, y, button=button, count=count, ms=ms)
    res.update({"norm": [nx, ny], "client_rect": info["client_rect"]})
    return res


# --------------------------------------------------------------------------- #
# 剪贴板（自测用：验证键盘输入是否真的生效）
# --------------------------------------------------------------------------- #

CF_UNICODETEXT = 13


def read_clipboard_text(retries: int = 5) -> str:
    """读取剪贴板文本（用于自动化自测）。剪贴板被占用时会重试。"""
    opened = False
    for _ in range(max(1, retries)):
        if user32.OpenClipboard(None):
            opened = True
            break
        time.sleep(0.1)
    if not opened:
        raise OSError("OpenClipboard 失败（剪贴板被其他程序占用）")
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return ""
        try:
            return ctypes.c_wchar_p(ptr).value or ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


# --------------------------------------------------------------------------- #
# 截图（可选；本方案不依赖视觉，仅用于抽查/留证）
# --------------------------------------------------------------------------- #


def screenshot(path: str, bbox: "tuple[int, int, int, int] | None" = None,
               max_width: "int | None" = None, quality: int = 85) -> dict:
    """截屏保存为图片。``bbox`` 为 ``(left, top, right, bottom)`` 屏幕坐标。

    ``max_width``：按比例缩小到该宽度以内（方便"看图"；读大图容易超限）。
    路径以 ``.jpg/.jpeg`` 结尾时保存为 JPEG（体积更小）。
    """
    from PIL import ImageGrab  # 延迟导入：PIL 不是本模块的硬依赖

    img = ImageGrab.grab(bbox=bbox, all_screens=True)
    original = list(img.size)
    if max_width and img.size[0] > max_width:
        ratio = max_width / img.size[0]
        img = img.resize((max_width, max(1, int(round(img.size[1] * ratio)))), resample=3)
    lower = path.lower()
    if lower.endswith((".jpg", ".jpeg")):
        img.convert("RGB").save(path, "JPEG", quality=quality)
    else:
        img.save(path, "PNG")
    return {"path": path, "size": list(img.size), "original_size": original,
            "bbox": list(bbox) if bbox else None}


def screenshot_window(path: str, hwnd: int, client_only: bool = True,
                      max_width: "int | None" = None) -> dict:
    """截取指定窗口（默认只截客户区）。"""
    info = window_info(hwnd)
    if client_only:
        x, y, w, h = info["client_rect"]
    else:
        x, y, w, h = info["window_rect"]
    return screenshot(path, (x, y, x + w, y + h), max_width=max_width)

