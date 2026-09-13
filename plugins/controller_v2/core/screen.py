"""Windows DPI 感知 + 物理像素屏幕尺寸 + 截图。

本模块是全项目物理坐标的唯一时钟源：
    - ensure_dpi_awareness()      把进程设为 Per-Monitor DPI Aware，避免高 DPI 下逻辑/物理像素混淆；
    - get_physical_screen_size()  返回主屏物理像素 (width, height)（本机实测 1920x1080，125% DPI）；
    - capture_screenshot(path)    用 pyautogui 截图并保存为 PNG，返回绝对路径。

物理像素语义必须与 0~1000 千分比归一化坐标严格区分（见 core.coord）。
"""

from __future__ import annotations

import ctypes
import sys
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional, Tuple

# Windows 常量
_SM_CXSCREEN = 0
_SM_CYSCREEN = 1
_MONITORINFOF_PRIMARY = 0x00000001

# DPI 感知常量
_PROCESS_PER_MONITOR_DPI_AWARE_V2 = -4  # SetProcessDpiAwarenessContext
_PROCESS_PER_MONITOR_DPI_AWARE = 2      # SetProcessDpiAwareness
_PROCESS_SYSTEM_DPI_AWARE = 1           # SetProcessDpiAwareness


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def _is_windows() -> bool:
    return sys.platform == "win32"


def ensure_dpi_awareness() -> None:
    """确保当前进程为 DPI-aware（Windows）。

    优先级：
    1. SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)；
    2. shcore.SetProcessDpiAwareness(PER_MONITOR_DPI_AWARE)；
    3. 旧接口 user32.SetProcessDPIAware()。

    非 Windows 平台直接返回。重复调用安全（每个接口仅尝试一次，失败静默降级）。
    """
    if not _is_windows():
        return

    user32 = ctypes.windll.user32

    # 1) 优先 Per-Monitor V2（返回值非 0 表示成功）。
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(_PROCESS_PER_MONITOR_DPI_AWARE_V2)):
            return
    except (AttributeError, OSError, ctypes.ArgumentError):
        pass

    # 2) 次选 shcore.SetProcessDpiAwareness（返回 S_OK=0 表示成功）。
    try:
        shcore = ctypes.windll.shcore
        if shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return
    except (AttributeError, OSError, ctypes.ArgumentError):
        pass

    # 3) 旧接口兜底。
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _enum_primary_monitor_size() -> Optional[Tuple[int, int]]:
    """通过 EnumDisplayMonitors + GetMonitorInfo 取主屏 rcMonitor 物理宽高。"""
    if not _is_windows():
        return None

    user32 = ctypes.windll.user32
    found: list = []

    # EnumDisplayMonitors 回调：BOOL CALLBACK(HMONITOR, HDC, LPRECT, LPARAM)
    _MONITOR_ENUM_PROC = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(_RECT),
        ctypes.c_void_p,
    )

    def _callback(h_monitor, _hdc, _lprc, _data) -> int:
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(h_monitor, ctypes.byref(info)):
            if info.dwFlags & _MONITORINFOF_PRIMARY:
                width = info.rcMonitor.right - info.rcMonitor.left
                height = info.rcMonitor.bottom - info.rcMonitor.top
                if width > 0 and height > 0:
                    found.append((int(width), int(height)))
        return 1  # 继续枚举

    try:
        # 必须持有回调引用，避免枚举过程中被 GC。
        callback = _MONITOR_ENUM_PROC(_callback)
        user32.EnumDisplayMonitors(None, None, callback, 0)
    except (AttributeError, OSError):
        return None

    return found[0] if found else None


@lru_cache(maxsize=1)
def get_physical_screen_size() -> Tuple[int, int]:
    """返回主屏物理像素分辨率 ``(width, height)``。

    顺序：
    1. Windows：EnumDisplayMonitors + GetMonitorInfo 取主屏 rcMonitor；
    2. 回退 GetSystemMetrics(SM_CXSCREEN / SM_CYSCREEN)；
    3. 非 Windows / 全部失败：pyautogui.size()。

    结果会缓存（同一进程内 DPI/屏幕通常不变）。如发生显示器热切换，可调用
    ``get_physical_screen_size.cache_clear()`` 后重取。
    """
    ensure_dpi_awareness()

    if _is_windows():
        size = _enum_primary_monitor_size()
        if size:
            return size

        try:
            user32 = ctypes.windll.user32
            width = int(user32.GetSystemMetrics(_SM_CXSCREEN))
            height = int(user32.GetSystemMetrics(_SM_CYSCREEN))
            if width > 0 and height > 0:
                return width, height
        except (AttributeError, OSError):
            pass

    # 兜底：pyautogui（DPI-aware 之后通常即物理像素）。
    try:
        import pyautogui

        width, height = pyautogui.size()
        width, height = int(width), int(height)
        if width > 0 and height > 0:
            return width, height
    except Exception:
        pass

    # 最终回退：本机已知物理值。仅用于极端异常环境，避免上层拿到 0。
    return 1920, 1200


def capture_screenshot(path: Optional[str] = None) -> str:
    """使用 pyautogui 截图并保存为 PNG。

    :param path: 目标 PNG 路径；为 None 时自动生成临时文件路径。
    :returns: 保存后的绝对路径字符串。
    """
    import pyautogui

    image = pyautogui.screenshot()

    if path is None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = Path(tempfile.gettempdir()) / f"controller_v2_screen_{stamp}.png"
    else:
        target = Path(path).expanduser()

    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    # pyautogui 截图对象是 PIL.Image，统一转 RGB 再存 PNG。
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.save(str(target), format="PNG")
    return str(target)


__all__ = [
    "ensure_dpi_awareness",
    "get_physical_screen_size",
    "capture_screenshot",
]
