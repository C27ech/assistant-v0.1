#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows 截图工具 —— 纯 ctypes 实现（不引入 pywin32 / mss 等新依赖）

能力：
    1. 枚举顶层窗口（标题 / 进程 / 类名 / 位置 / 是否前台 / 是否最小化 / 是否被 DWM 隐藏）；
    2. 枚举显示器（序号 / 设备名 / 分辨率 / 是否主屏 / 虚拟桌面坐标）；
    3. 截取指定窗口：优先 PrintWindow(PW_RENDERFULLCONTENT)（窗口被遮挡也能截到），
       取到空白时退回「屏幕区域截取」；
    4. 截取指定显示器、指定矩形区域（虚拟桌面坐标）；
    5. 可选：把目标窗口切到前台（--activate 时调用，默认不动用户桌面）。

依赖：仅 Pillow（本插件原本就依赖）+ 标准库 ctypes。
非 Windows 平台 import 不会报错，但所有窗口相关函数会抛 WinCaptureUnsupported。
"""

import ctypes
import os
import time
from ctypes import wintypes

from PIL import Image, ImageGrab

IS_WINDOWS = os.name == "nt"


class WinCaptureError(Exception):
    """截图相关的可预期错误（窗口找不到、区域越界、非 Windows 等）。"""


class WinCaptureUnsupported(WinCaptureError):
    """当前平台不支持窗口操作。"""


class WindowNotFound(WinCaptureError):
    """按标题 / hwnd 找不到窗口。"""


if not IS_WINDOWS:  # 非 Windows：只保留异常定义，调用方自行降级
    user32 = gdi32 = dwmapi = None
    DPI_MODE = "n/a"
else:  # pragma: no cover - 仅在 Windows 上执行
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
    except OSError:
        dwmapi = None


# --------------------------------------------------------------------------- #
# 常量 / 类型
# --------------------------------------------------------------------------- #

HWND = wintypes.HWND
HDC = ctypes.c_void_p
HBITMAP = ctypes.c_void_p
HGDIOBJ = ctypes.c_void_p
HMONITOR = ctypes.c_void_p
LPRECT = ctypes.POINTER(wintypes.RECT)

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CMONITORS = 80

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000

PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14

MONITORINFOF_PRIMARY = 0x00000001
MONITOR_DEFAULTTONEAREST = 2

SW_RESTORE = 9
SW_SHOW = 5

DIB_RGB_COLORS = 0
BI_RGB = 0

# 虚拟屏幕（多显示器拼成的整个桌面）坐标 = 物理像素（已置 DPI 感知）


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


def _setup_prototypes():
    """显式声明 restype/argtypes：64 位下不声明会把指针当 int32 截断。"""
    if not IS_WINDOWS:
        return
    u = user32
    g = gdi32

    u.EnumWindows.argtypes = (
        ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM),
        wintypes.LPARAM,
    )
    u.GetWindowTextLengthW.argtypes = (HWND,)
    u.GetWindowTextLengthW.restype = ctypes.c_int
    u.GetWindowTextW.argtypes = (HWND, wintypes.LPWSTR, ctypes.c_int)
    u.GetClassNameW.argtypes = (HWND, wintypes.LPWSTR, ctypes.c_int)
    u.GetWindowThreadProcessId.argtypes = (HWND, ctypes.POINTER(wintypes.DWORD))
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.IsWindowVisible.argtypes = (HWND,)
    u.IsWindowVisible.restype = wintypes.BOOL
    u.IsIconic.argtypes = (HWND,)
    u.IsIconic.restype = wintypes.BOOL
    u.GetForegroundWindow.restype = HWND
    u.GetWindowRect.argtypes = (HWND, LPRECT)
    u.GetWindowRect.restype = wintypes.BOOL
    u.GetClientRect.argtypes = (HWND, LPRECT)
    u.GetClientRect.restype = wintypes.BOOL
    u.ClientToScreen.argtypes = (HWND, ctypes.POINTER(wintypes.POINT))
    u.ClientToScreen.restype = wintypes.BOOL
    u.GetWindowLongW.argtypes = (HWND, ctypes.c_int)
    u.GetWindowLongW.restype = ctypes.c_long
    u.GetSystemMetrics.argtypes = (ctypes.c_int,)
    u.GetSystemMetrics.restype = ctypes.c_int
    u.GetWindowDC.argtypes = (HWND,)
    u.GetWindowDC.restype = HDC
    u.ReleaseDC.argtypes = (HWND, HDC)
    u.PrintWindow.argtypes = (HWND, HDC, wintypes.UINT)
    u.PrintWindow.restype = wintypes.BOOL
    u.ShowWindow.argtypes = (HWND, ctypes.c_int)
    u.ShowWindow.restype = wintypes.BOOL
    u.SetForegroundWindow.argtypes = (HWND,)
    u.SetForegroundWindow.restype = wintypes.BOOL
    u.MonitorFromWindow.argtypes = (HWND, wintypes.DWORD)
    u.MonitorFromWindow.restype = HMONITOR
    u.GetMonitorInfoW.argtypes = (HMONITOR, ctypes.POINTER(MONITORINFOEXW))
    u.GetMonitorInfoW.restype = wintypes.BOOL
    u.EnumDisplayMonitors.argtypes = (
        HDC,
        LPRECT,
        ctypes.WINFUNCTYPE(
            wintypes.BOOL, HMONITOR, HDC, LPRECT, wintypes.LPARAM
        ),
        wintypes.LPARAM,
    )
    u.EnumDisplayMonitors.restype = wintypes.BOOL

    g.CreateCompatibleDC.argtypes = (HDC,)
    g.CreateCompatibleDC.restype = HDC
    g.CreateCompatibleBitmap.argtypes = (HDC, ctypes.c_int, ctypes.c_int)
    g.CreateCompatibleBitmap.restype = HBITMAP
    g.SelectObject.argtypes = (HDC, HGDIOBJ)
    g.SelectObject.restype = HGDIOBJ
    g.DeleteObject.argtypes = (HGDIOBJ,)
    g.DeleteObject.restype = wintypes.BOOL
    g.DeleteDC.argtypes = (HDC,)
    g.DeleteDC.restype = wintypes.BOOL
    g.GetDIBits.argtypes = (
        HDC,
        HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.POINTER(BITMAPINFO),
        wintypes.UINT,
    )
    g.GetDIBits.restype = ctypes.c_int
    g.BitBlt.argtypes = (
        HDC,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.DWORD,
    )
    g.BitBlt.restype = wintypes.BOOL

    if dwmapi is not None:
        dwmapi.DwmGetWindowAttribute.argtypes = (
            HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        )
        dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long


_setup_prototypes()


def set_dpi_aware() -> str:
    """置 DPI 感知，保证窗口坐标 / 截图坐标都是物理像素（多屏缩放不同也能对上）。

    返回实际生效的模式名，便于日志排查。失败不抛异常（退回系统缩放坐标）。
    """
    global DPI_MODE
    if not IS_WINDOWS:
        return "n/a"
    try:
        # Per-Monitor-V2（Win10 1703+）
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            DPI_MODE = "per-monitor-v2"
            return DPI_MODE
    except Exception:
        pass
    try:
        if ctypes.WinDLL("shcore").SetProcessDpiAwareness(2) == 0:
            DPI_MODE = "per-monitor"
            return DPI_MODE
    except Exception:
        pass
    try:
        if user32.SetProcessDPIAware():
            DPI_MODE = "system"
            return DPI_MODE
    except Exception:
        pass
    DPI_MODE = "none"
    return DPI_MODE


DPI_MODE = set_dpi_aware()


# --------------------------------------------------------------------------- #
# 窗口枚举 / 信息
# --------------------------------------------------------------------------- #

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)


def _require_windows():
    if not IS_WINDOWS:
        raise WinCaptureUnsupported("窗口截图仅支持 Windows（当前平台 %s）" % os.name)


def _window_text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 2)
    user32.GetWindowTextW(hwnd, buf, n + 2)
    return buf.value


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _pid_of(hwnd) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _process_name(pid: int) -> str:
    """进程可执行文件名（取不到返回空串，如权限不足的系统进程）。"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
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


def _dwm_rect(hwnd):
    """DWM 可见边框（不含阴影/不可见调整边框），取不到返回 None。"""
    if dwmapi is None:
        return None
    rect = wintypes.RECT()
    try:
        hr = dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
    except Exception:
        return None
    if hr != 0:
        return None
    if rect.right <= rect.left or rect.bottom <= rect.top:
        return None
    return rect


def is_cloaked(hwnd) -> bool:
    """窗口是否被 DWM 隐藏（UWP 后台幽灵窗口等，不可见但 IsWindowVisible 为真）。"""
    if dwmapi is None:
        return False
    val = wintypes.DWORD(0)
    try:
        hr = dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val)
        )
    except Exception:
        return False
    return hr == 0 and val.value != 0


def window_info(hwnd) -> dict:
    """单个窗口的完整信息（坐标为物理像素的虚拟桌面坐标）。"""
    _require_windows()
    hwnd = int(hwnd)
    wr = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(wr))
    cr = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(cr))
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    pid = _pid_of(hwnd)
    proc = _process_name(pid)
    return {
        "hwnd": hwnd,
        "hwnd_hex": "0x%08X" % hwnd,
        "title": _window_text(hwnd),
        "class": _class_name(hwnd),
        "pid": pid,
        "process": proc,
        "process_name": os.path.basename(proc) if proc else "",
        "visible": bool(user32.IsWindowVisible(hwnd)),
        "minimized": bool(user32.IsIconic(hwnd)),
        "foreground": user32.GetForegroundWindow() == hwnd,
        "cloaked": is_cloaked(hwnd),
        "tool_window": bool(ex & WS_EX_TOOLWINDOW) and not bool(ex & WS_EX_APPWINDOW),
        # 整窗（含标题栏/边框）
        "window_rect": [wr.left, wr.top, wr.right - wr.left, wr.bottom - wr.top],
        # 客户区（去掉标题栏/边框）在屏幕上的 (x, y, w, h)
        "client_rect": [pt.x, pt.y, cr.right, cr.bottom],
    }


def list_windows(filter_text=None, use_regex=False, include_minimized=False,
                 include_tool_windows=False, include_cloaked=False) -> list:
    """枚举顶层窗口。

    filter_text：标题 / 进程名 / 类名任一命中即算匹配（默认不分大小写的子串匹配；
    use_regex=True 时按正则匹配标题）。
    include_minimized / include_tool_windows / include_cloaked 默认都过滤掉。
    返回按「前台优先 → 非最小化 → 标题」排序的窗口信息列表。
    """
    _require_windows()
    pat = None
    if filter_text and use_regex:
        import re as _re
        try:
            pat = _re.compile(filter_text, _re.IGNORECASE)
        except Exception as exc:
            raise WinCaptureError("--filter 正则不合法：%s" % exc)

    out = []

    def cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        if not include_minimized and user32.IsIconic(hwnd):
            return True
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if not include_tool_windows and (ex & WS_EX_TOOLWINDOW) and not (ex & WS_EX_APPWINDOW):
            return True
        if not include_cloaked and is_cloaked(hwnd):
            return True
        if filter_text:
            title = _window_text(hwnd)
            if pat is not None:
                hit = bool(pat.search(title))
            else:
                needle = filter_text.lower()
                hit = (
                    needle in title.lower()
                    or needle in _class_name(hwnd).lower()
                    or needle in (_process_name(_pid_of(hwnd)) or "").lower()
                )
            if not hit:
                return True
        out.append(window_info(hwnd))
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)

    out.sort(key=lambda w: (not w["foreground"], w["minimized"], w["title"].lower()))
    return out


def find_window(title=None, use_regex=False, include_minimized=True,
                include_tool_windows=False) -> dict:
    """按标题找窗口：先精确匹配 → 正则 → 子串 → 进程名；多个命中时优先前台/非最小化。

    找不到抛 WindowNotFound（消息里带候选窗口，便于调用方直接提示用户/模型）。
    """
    _require_windows()
    if not title:
        raise WindowNotFound("未指定窗口标题（--window）")
    needle = title.lower()
    cands = list_windows(include_minimized=include_minimized,
                         include_tool_windows=include_tool_windows)
    if not cands:
        raise WindowNotFound("当前没有任何可见窗口可匹配")

    exact = [w for w in cands if w["title"].lower() == needle]
    if not exact and use_regex:
        import re as _re
        try:
            pat = _re.compile(title, _re.IGNORECASE)
        except Exception as exc:
            raise WinCaptureError("--window 正则不合法：%s" % exc)
        exact = [w for w in cands if pat.search(w["title"])]
    if not exact:
        exact = [w for w in cands if needle in w["title"].lower()]
    if not exact:
        # 退一步：按进程名匹配（用户常说「截一下 chrome」而不是完整标题）
        exact = [w for w in cands if needle in (w.get("process_name") or "").lower()]
    if not exact:
        top = "；".join(w["title"] for w in cands[:12])
        raise WindowNotFound(
            "找不到标题含「%s」的窗口。当前可见窗口（最多 12 个）：%s" % (title, top)
        )

    exact.sort(key=lambda w: (not w["foreground"], w["minimized"]))
    return exact[0]


def foreground_window() -> dict:
    """当前前台窗口信息；没有前台窗口（锁屏等）时抛 WindowNotFound。"""
    _require_windows()
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        raise WindowNotFound("当前没有前台窗口")
    return window_info(hwnd)


def activate_window(hwnd, restore=True, wait=0.35) -> bool:
    """把窗口切到前台（最小化时先 SW_RESTORE）。返回是否成功抢到前台。

    Windows 有前台锁定（ForegroundLockTimeout），非前台进程调用可能被拒；
    只作为 --activate 的可选辅助，失败不抛异常、由调用方决定要不要继续截。
    """
    _require_windows()
    hwnd = int(hwnd)
    if restore and user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(wait)
    user32.ShowWindow(hwnd, SW_SHOW)
    try:
        user32.SetForegroundWindow(hwnd)
    except Exception:
        return False
    if wait:
        time.sleep(wait)
    return user32.GetForegroundWindow() == hwnd


# --------------------------------------------------------------------------- #
# 显示器 / 虚拟桌面
# --------------------------------------------------------------------------- #


def _system_dpi() -> int:
    """系统 DPI（96 = 100% 缩放）。取不到返回 96。"""
    if not IS_WINDOWS:
        return 96
    try:
        dpi = int(user32.GetDpiForSystem())
        if dpi > 0:
            return dpi
    except Exception:
        pass
    try:
        hdc = user32.GetDC(0)
        try:
            dpi = int(ctypes.WinDLL("gdi32").GetDeviceCaps(hdc, 88))  # LOGPIXELSX
        finally:
            user32.ReleaseDC(0, hdc)
        if dpi > 0:
            return dpi
    except Exception:
        pass
    return 96


def _monitor_dpi(hmon) -> int:
    """单台显示器的有效 DPI（Per-Monitor 感知下才是该屏真实 DPI）。"""
    if not IS_WINDOWS:
        return 96
    try:
        dpi_x = ctypes.c_uint(0)
        dpi_y = ctypes.c_uint(0)
        # MDT_EFFECTIVE_DPI = 0
        if ctypes.WinDLL("shcore").GetDpiForMonitor(
            ctypes.c_void_p(hmon), 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)
        ) == 0 and int(dpi_x.value) > 0:
            return int(dpi_x.value)
    except Exception:
        pass
    return _system_dpi()


def virtual_screen_rect() -> tuple:
    """整个虚拟桌面 (x, y, w, h)：多显示器时 x/y 可能为负。"""
    _require_windows()
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def monitors() -> list:
    """枚举显示器，按 (左上角 x, y) 排序，序号从 1 开始（和 --monitor N 对应）。

    每项含物理像素 rect / work_rect，以及该屏的 dpi / scale（Windows 缩放倍数）。
    """
    _require_windows()
    raw = []

    def cb(hmon, _hdc, _lprect, _lparam):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            return True
        dpi = _monitor_dpi(hmon)
        raw.append({
            "device": mi.szDevice,
            "primary": bool(mi.dwFlags & MONITORINFOF_PRIMARY),
            "rect": [
                mi.rcMonitor.left,
                mi.rcMonitor.top,
                mi.rcMonitor.right - mi.rcMonitor.left,
                mi.rcMonitor.bottom - mi.rcMonitor.top,
            ],
            "work_rect": [
                mi.rcWork.left,
                mi.rcWork.top,
                mi.rcWork.right - mi.rcWork.left,
                mi.rcWork.bottom - mi.rcWork.top,
            ],
            "dpi": int(dpi),
            "scale": round(int(dpi) / 96.0, 4),
        })
        return True

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL, HMONITOR, HDC, LPRECT, wintypes.LPARAM
    )
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    raw.sort(key=lambda m: (m["rect"][0], m["rect"][1]))
    for i, m in enumerate(raw, start=1):
        m["index"] = i
    return raw


# --------------------------------------------------------------------------- #
# 屏幕画像（分辨率 / 缩放 / 多显示器）—— 截图与坐标解读的统一依据
# --------------------------------------------------------------------------- #
_PROFILE_CACHE = None
_PROFILE_FINGERPRINT = None


def _screen_fingerprint():
    """屏幕指纹：分辨率 / 显示器数量 / 系统 DPI 任一变都会变（缓存自检用）。"""
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CMONITORS),
        _system_dpi(),
    )


def invalidate_screen_profile() -> None:
    """清空屏幕画像缓存（改分辨率 / 改缩放 / 插拔显示器后强制重采）。"""
    global _PROFILE_CACHE, _PROFILE_FINGERPRINT
    _PROFILE_CACHE = None
    _PROFILE_FINGERPRINT = None


def screen_profile() -> dict:
    """返回本机屏幕画像（带指纹自检，分辨率/缩放变了自动重采）。

    :returns: ``{"physical","logical","dpi","scale","scale_percent","dpi_mode",
                 "monitor_count","monitors","virtual_rect","multi_monitor"}``
              （尺寸/rect 都是**物理像素**，因为本进程已 Per-Monitor-V2 DPI 感知）。
    """
    global _PROFILE_CACHE, _PROFILE_FINGERPRINT

    _require_windows()
    fingerprint = _screen_fingerprint()
    if _PROFILE_CACHE is not None and _PROFILE_FINGERPRINT == fingerprint:
        return dict(_PROFILE_CACHE)

    mons = monitors()
    primary = next((m for m in mons if m["primary"]), mons[0])
    dpi = int(primary.get("dpi") or _system_dpi())
    scale = float(round(dpi / 96.0, 4)) or 1.0
    width, height = int(primary["rect"][2]), int(primary["rect"][3])

    profile = {
        "physical": [width, height],
        "logical": [max(1, int(round(width / scale))), max(1, int(round(height / scale)))],
        "dpi": dpi,
        "scale": scale,
        "scale_percent": int(round(scale * 100)),
        "dpi_mode": DPI_MODE,
        "monitor_count": len(mons),
        "multi_monitor": len(mons) > 1,
        "virtual_rect": list(virtual_screen_rect()),
        "monitors": mons,
    }
    _PROFILE_CACHE = profile
    _PROFILE_FINGERPRINT = fingerprint
    return dict(profile)


def format_screen_profile(profile=None) -> str:
    """屏幕画像的单行文本（日志 / 摘要用）。"""
    p = profile if isinstance(profile, dict) else screen_profile()
    return (
        "物理分辨率 %dx%d，Windows 缩放 %d%%（dpi %d），逻辑分辨率 %dx%d，"
        "显示器 %d 台%s，DPI 感知=%s"
        % (
            p["physical"][0], p["physical"][1], p["scale_percent"], p["dpi"],
            p["logical"][0], p["logical"][1], p["monitor_count"],
            "（虚拟桌面 %dx%d）" % (p["virtual_rect"][2], p["virtual_rect"][3])
            if p["multi_monitor"] else "",
            p["dpi_mode"],
        )
    )


# --------------------------------------------------------------------------- #
# 截图
# --------------------------------------------------------------------------- #


def _is_blank(img) -> bool:
    """几乎单色（全黑/全白）视为「没截到内容」——PrintWindow 对部分程序会返回黑图。"""
    try:
        lo, hi = img.convert("L").getextrema()
    except Exception:
        return False
    return (hi - lo) <= 2


def grab_region(bbox) -> "Image.Image":
    """按屏幕坐标 (left, top, right, bottom) 截图（多屏时坐标可为负）。"""
    _require_windows()
    left, top, right, bottom = [int(v) for v in bbox]
    try:
        return ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True)
    except TypeError:
        # 老 Pillow 不支持 all_screens → 退回单屏
        return ImageGrab.grab(bbox=(left, top, right, bottom))
    except Exception as exc:
        raise WinCaptureError("屏幕区域截图失败：%s" % exc)


def _bitmap_to_image(mem_dc, bmp, width, height) -> "Image.Image":
    """把 GDI 位图取成 PIL Image（32 位 BGRX，自顶向下）。"""
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = int(width)
    bmi.bmiHeader.biHeight = -int(height)   # 负高度 = 自顶向下，省一次翻转
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = BI_RGB
    buf = ctypes.create_string_buffer(int(width) * int(height) * 4)
    got = gdi32.GetDIBits(
        mem_dc, bmp, 0, int(height), buf, ctypes.byref(bmi), DIB_RGB_COLORS
    )
    if got == 0:
        raise WinCaptureError("GetDIBits 取位图数据失败")
    return Image.frombytes(
        "RGB", (int(width), int(height)), bytes(buf), "raw", "BGRX", 0, 1
    )


def printwindow_image(hwnd, width, height, client_only=False) -> "Image.Image":
    """用 PrintWindow 把窗口渲染到位图（窗口被遮挡也能截到；最小化窗口通常截不到）。

    client_only=True 时带 PW_CLIENTONLY（部分程序不认，失败会抛 WinCaptureError）。
    """
    _require_windows()
    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        raise WinCaptureError("GetWindowDC 失败（窗口可能已关闭）")
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    if not mem_dc:
        user32.ReleaseDC(hwnd, hwnd_dc)
        raise WinCaptureError("CreateCompatibleDC 失败")
    bmp = None
    old = None
    try:
        bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, int(width), int(height))
        if not bmp:
            raise WinCaptureError("CreateCompatibleBitmap 失败")
        old = gdi32.SelectObject(mem_dc, bmp)
        flags = PW_RENDERFULLCONTENT | (PW_CLIENTONLY if client_only else 0)
        ok = user32.PrintWindow(hwnd, mem_dc, flags)
        if not ok and not client_only:
            ok = user32.PrintWindow(hwnd, mem_dc, 0)   # 老程序只认 flags=0
        if not ok:
            raise WinCaptureError("PrintWindow 返回失败")
        return _bitmap_to_image(mem_dc, bmp, width, height)
    finally:
        try:
            if old:
                gdi32.SelectObject(mem_dc, old)
            if bmp:
                gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem_dc)
        finally:
            user32.ReleaseDC(hwnd, hwnd_dc)


def _crop_by_rect(img, outer, inner):
    """把 outer 矩形拍到的那张图，按 inner 矩形在 outer 里的相对位置裁一刀。"""
    left = max(0, inner[0] - outer[0])
    top = max(0, inner[1] - outer[1])
    right = min(img.width, left + inner[2])
    bottom = min(img.height, top + inner[3])
    if right - left <= 0 or bottom - top <= 0:
        return img
    return img.crop((left, top, right, bottom))


def capture_window(info, client_area=False, prefer_printwindow=True) -> dict:
    """截一个窗口，返回 {"image", "how", "rect", "notes"}。

    默认优先 PrintWindow（被遮挡也截得到）；拿到空白/失败时退回屏幕区域截取
    （此时截到的是「该矩形屏幕上当前显示的内容」，被遮挡就是遮挡物）。
    """
    _require_windows()
    hwnd = int(info["hwnd"])
    win_rect = list(info["window_rect"])
    if win_rect[2] <= 0 or win_rect[3] <= 0:
        raise WinCaptureError("窗口尺寸为 0（可能已最小化或正在关闭）：%s" % info.get("title"))

    notes = []
    img = None
    how = ""
    if prefer_printwindow:
        try:
            img = printwindow_image(hwnd, win_rect[2], win_rect[3])
            if _is_blank(img):
                notes.append("PrintWindow 取到单色空白图")
                img = None
            else:
                how = "PrintWindow（窗口被遮挡也能截到）"
        except WinCaptureError as exc:
            notes.append("PrintWindow 失败：%s" % exc)
            img = None
    if img is None:
        if info.get("minimized"):
            raise WinCaptureError(
                "窗口「%s」已最小化，截不到内容。请先还原窗口，或改用 target=all 截全屏。"
                % info.get("title")
            )
        img = grab_region([win_rect[0], win_rect[1],
                           win_rect[0] + win_rect[2], win_rect[1] + win_rect[3]])
        how = "屏幕区域截取（窗口被遮挡时截到的是遮挡物）"
        notes.append("改用屏幕区域截取")

    rect = win_rect
    tight = _dwm_rect(hwnd)
    if tight is not None:
        tight_rect = [tight.left, tight.top,
                      tight.right - tight.left, tight.bottom - tight.top]
        if tight_rect[2] < win_rect[2] or tight_rect[3] < win_rect[3]:
            img = _crop_by_rect(img, rect, tight_rect)
            rect = tight_rect
            notes.append("已按 DWM 可见边框裁掉不可见的外圈")

    if client_area:
        img = _crop_by_rect(img, rect, list(info["client_rect"]))
        rect = _intersect_rect(rect, list(info["client_rect"]))
        notes.append("已裁到客户区（去掉标题栏/边框）")

    return {"image": img, "how": how, "rect": rect, "notes": notes}


def _intersect_rect(a, b) -> list:
    """两个 (x, y, w, h) 矩形的交集；无交集时返回 a。"""
    left = max(a[0], b[0])
    top = max(a[1], b[1])
    right = min(a[0] + a[2], b[0] + b[2])
    bottom = min(a[1] + a[3], b[1] + b[3])
    if right - left <= 0 or bottom - top <= 0:
        return list(a)
    return [left, top, right - left, bottom - top]


def clamp_region(x, y, w, h):
    """把 (x, y, w, h) 区域夹进虚拟桌面。

    返回 (bbox, clipped, outside)：bbox 为 (left, top, right, bottom)，
    clipped 表示有裁剪，outside=True 表示整个区域都在屏幕外（bbox 为 None）。
    """
    vx, vy, vw, vh = virtual_screen_rect()
    left, top, right, bottom = int(x), int(y), int(x) + int(w), int(y) + int(h)
    clipped = left < vx or top < vy or right > vx + vw or bottom > vy + vh
    cl = (max(left, vx), max(top, vy),
          min(right, vx + vw), min(bottom, vy + vh))
    if cl[2] - cl[0] <= 0 or cl[3] - cl[1] <= 0:
        return None, True, True
    return cl, clipped, False


def parse_region(text) -> list:
    """解析区域参数 "x,y,w,h"（也接受空格/中文逗号分隔）。"""
    import re as _re
    parts = [p for p in _re.split(r"[,，\s]+", str(text or "").strip()) if p]
    if len(parts) != 4:
        raise WinCaptureError("区域格式应为 x,y,w,h（起点 x,y + 宽 高），当前为 %r" % text)
    try:
        vals = [int(round(float(p))) for p in parts]
    except ValueError:
        raise WinCaptureError("区域里有非数字：%r" % text)
    if vals[2] <= 0 or vals[3] <= 0:
        raise WinCaptureError("区域的宽/高必须大于 0，当前为 %d x %d" % (vals[2], vals[3]))
    return vals


def capture_region(x, y, w, h) -> dict:
    """截虚拟桌面上的一个矩形区域（坐标为物理像素）。"""
    bbox, clipped, outside = clamp_region(x, y, w, h)
    if outside:
        vx, vy, vw, vh = virtual_screen_rect()
        raise WinCaptureError(
            "区域 (%d,%d,%d,%d) 完全在屏幕外。虚拟桌面范围：x %d~%d, y %d~%d"
            % (x, y, w, h, vx, vx + vw, vy, vy + vh)
        )
    notes = []
    if clipped:
        notes.append("区域部分超出屏幕，已自动裁剪到屏幕内")
    return {
        "image": grab_region(bbox),
        "how": "屏幕区域 (x,y,w,h)=(%d,%d,%d,%d)" % (bbox[0], bbox[1],
                                                  bbox[2] - bbox[0], bbox[3] - bbox[1]),
        "rect": [bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1]],
        "notes": notes,
    }


def capture_monitor(index=1) -> dict:
    """截指定显示器（序号从 1 开始，对应 --list-monitors 里的 index）。"""
    mons = monitors()
    try:
        idx = int(index)
    except (TypeError, ValueError):
        raise WinCaptureError("显示器序号必须是整数，当前为 %r" % index)
    if idx < 1 or idx > len(mons):
        raise WinCaptureError(
            "显示器序号 %d 越界：本机共 %d 台（%s）"
            % (idx, len(mons), "、".join(m["device"] for m in mons))
        )
    m = mons[idx - 1]
    x, y, w, h = m["rect"]
    return {
        "image": grab_region([x, y, x + w, y + h]),
        "how": "显示器 %d %s%s" % (m["index"], m["device"], "（主屏）" if m["primary"] else ""),
        "rect": m["rect"],
        "notes": [],
    }


def capture_all_screens() -> dict:
    """截整个虚拟桌面（多显示器拼成一张；Pillow 不支持时退回主屏）。"""
    notes = []
    try:
        img = ImageGrab.grab(all_screens=True)
    except Exception as exc:
        notes.append("all_screens 截图失败（%s），退回单屏" % exc)
        img = ImageGrab.grab()
    x, y, w, h = virtual_screen_rect()
    return {"image": img, "how": "全部显示器（虚拟桌面）", "rect": [x, y, w, h],
            "notes": notes}


def resize_image(img, scale=1.0, max_width=None, max_height=None) -> "Image.Image":
    """按比例缩放：先乘 scale，再按 max_width / max_height 兜底压小（只缩不放）。"""
    import re as _re

    def _num(v):
        if v in (None, ""):
            return None
        m = _re.match(r"^\s*([0-9]*\.?[0-9]+)\s*$", str(v))
        if not m:
            raise WinCaptureError("缩放参数不是数字：%r" % v)
        return float(m.group(1))

    s = _num(scale) if scale not in (None, "") else 1.0
    if s is None or s <= 0:
        raise WinCaptureError("缩放倍数必须大于 0，当前为 %r" % scale)
    mw, mh = _num(max_width), _num(max_height)

    ratio = s
    w, h = img.size
    if mw and w * ratio > mw:
        ratio = mw / float(w)
    if mh and h * ratio > mh:
        ratio = mh / float(h)
    if abs(ratio - 1.0) < 1e-6:
        return img
    new_size = (max(1, int(round(w * ratio))), max(1, int(round(h * ratio))))
    return img.resize(new_size, Image.LANCZOS)


# --------------------------------------------------------------------------- #
# 给人 / 给AI 看的文本
# --------------------------------------------------------------------------- #


def window_line(w) -> str:
    """单个窗口的单行描述。"""
    flags = []
    if w["foreground"]:
        flags.append("前台")
    if w["minimized"]:
        flags.append("最小化")
    label = "[%s] " % "/".join(flags) if flags else ""
    proc = w.get("process_name") or ("pid %d" % w["pid"])
    return ("%s%s hwnd=%s pid=%d 进程=%s 位置=(%d,%d) 尺寸=%dx%d"
            % (label, w["title"], w["hwnd_hex"], w["pid"], proc,
               w["window_rect"][0], w["window_rect"][1],
               w["window_rect"][2], w["window_rect"][3]))


def format_window_list(windows) -> str:
    """窗口列表文本（每行一个窗口，带序号）。"""
    if not windows:
        return "（没有匹配的窗口）"
    lines = ["共 %d 个窗口（序号只是列表编号，调用时用标题或 hwnd）：" % len(windows)]
    for i, w in enumerate(windows, start=1):
        lines.append("%d. %s" % (i, window_line(w)))
    return "\n".join(lines)


def format_monitor_list(mons) -> str:
    """显示器列表文本（头部带屏幕画像，每台含分辨率与缩放）。"""
    if not mons:
        return "（没有枚举到显示器）"
    lines = [format_screen_profile(), "共 %d 台显示器：" % len(mons)]
    for m in mons:
        r, wk = m["rect"], m["work_rect"]
        dpi = int(m.get("dpi") or 0)
        scale = float(m.get("scale") or 0) or (dpi / 96.0 if dpi else 1.0)
        lines.append(
            "%d. %s%s 位置=(%d,%d) 尺寸=%dx%d 工作区=%dx%d 缩放=%d%%（dpi %d）"
            % (m["index"], m["device"], "（主屏）" if m["primary"] else "",
               r[0], r[1], r[2], r[3], wk[2], wk[3],
               int(round(scale * 100)), dpi)
        )
    return "\n".join(lines)


def safe_filename(text, fallback="capture", max_len=40) -> str:
    """把窗口标题之类的东西变成安全的文件名片段。"""
    import re as _re
    s = _re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(text or "")).strip(" ._")
    s = _re.sub(r"\s+", "_", s)
    return (s or fallback)[:max_len]


__all__ = [
    "IS_WINDOWS", "DPI_MODE", "WinCaptureError", "WinCaptureUnsupported",
    "WindowNotFound", "window_info", "list_windows", "find_window",
    "foreground_window", "activate_window", "monitors", "virtual_screen_rect",
    "screen_profile", "format_screen_profile", "invalidate_screen_profile",
    "grab_region", "printwindow_image", "capture_window", "capture_region",
    "capture_monitor", "capture_all_screens", "resize_image", "parse_region",
    "clamp_region", "format_window_list", "format_monitor_list", "window_line",
    "safe_filename", "set_dpi_aware",
]
