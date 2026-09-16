"""Windows DPI 感知 + 屏幕画像（分辨率 / 缩放 / 多显示器）+ 自适应截图。

本模块是全项目物理坐标的唯一时钟源：

    - ensure_dpi_awareness()      把进程设为 Per-Monitor V2 DPI Aware（失败逐级降级），
                                  避免高 DPI 缩放下逻辑/物理像素混淆；
    - get_screen_profile()        返回屏幕画像：物理/逻辑分辨率、Windows 缩放倍数、
                                  每台显示器的 rect+dpi+scale、虚拟桌面 rect、是否多屏。
                                  分辨率 / 缩放 / 显示器数量变化时自动失效重建；
    - get_physical_screen_size()  兼容入口：主屏物理像素 (width, height)；
    - compute_frame_scale()       按「逻辑分辨率」自适应算模型帧缩放比例：
                                  target = clamp(0.667 * 逻辑长边, 768, 2048)。
                                  本机 1920x1200@125%（逻辑 1536x960）-> 1024x640；
                                  4K、小屏、其他缩放比例下自动放大 / 缩小；
    - capture_frame()             截图并返回几何信息 dict（path / size / rect / origin /
                                  capture_scale / all_screens / profile），供执行层把
                                  归一化坐标落到正确屏幕；
    - capture_screenshot()        兼容入口：截图保存为 PNG，返回绝对路径。

物理像素语义必须与 0~1000 千分比归一化坐标严格区分（见 core.coord）：
归一化坐标的参照系 = 本轮截图覆盖的那块物理矩形（单屏=主屏；多屏=整块虚拟桌面），
由 ``core.coord.set_active_frame()`` 设定，原点允许为负（显示器在主屏左/上方时）。
"""

from __future__ import annotations

import ctypes
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Windows 常量
# --------------------------------------------------------------------------- #
_SM_CXSCREEN = 0
_SM_CYSCREEN = 1
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79
_SM_CMONITORS = 80
_MONITORINFOF_PRIMARY = 0x00000001

# DPI 感知常量
_PROCESS_PER_MONITOR_DPI_AWARE_V2 = -4  # SetProcessDpiAwarenessContext
_PROCESS_PER_MONITOR_DPI_AWARE = 2      # SetProcessDpiAwareness
_PROCESS_SYSTEM_DPI_AWARE = 1           # SetProcessDpiAwareness

# 每显示器有效 DPI（GetDpiForMonitor 的 MDT_EFFECTIVE_DPI）
_MDT_EFFECTIVE_DPI = 0

# 无配置时的兜底采样参数（与 config.json 的 screen.frame 默认值一致）。
_DEFAULT_FRAME_SAMPLING: Dict[str, Any] = {
    "long_side_ratio": 0.6667,  # ≈2/3：本机 1536 逻辑长边 -> 1024 模型帧（与旧版一致）
    "min_long_side": 768,
    "max_long_side": 2048,
    "jpeg_quality": 85,
}


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


def _clamp(value: float, low: float, high: float) -> float:
    """把 value 钳制到 [low, high]。"""
    if value < low:
        return low
    if value > high:
        return high
    return value


def ensure_dpi_awareness() -> str:
    """确保当前进程为 DPI-aware（Windows），返回实际生效的模式名。

    优先级：
    1. SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2) -> "per-monitor-v2"；
    2. shcore.SetProcessDpiAwareness(PER_MONITOR_DPI_AWARE) -> "per-monitor"；
    3. 旧接口 user32.SetProcessDPIAware() -> "system"；
    4. 全部失败（含「python.exe manifest 已声明感知」这种无需再设的情况）-> "unknown"。

    非 Windows 平台返回 "n/a"。重复调用安全（失败静默降级，不抛异常）。
    """
    if not _is_windows():
        return "n/a"

    user32 = ctypes.windll.user32

    # 1) 优先 Per-Monitor V2（返回值非 0 表示成功）。
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(_PROCESS_PER_MONITOR_DPI_AWARE_V2)):
            return "per-monitor-v2"
    except (AttributeError, OSError, ctypes.ArgumentError):
        pass

    # 2) 次选 shcore.SetProcessDpiAwareness（返回 S_OK=0 表示成功）。
    try:
        shcore = ctypes.windll.shcore
        if shcore.SetProcessDpiAwareness(_PROCESS_PER_MONITOR_DPI_AWARE) == 0:
            return "per-monitor"
    except (AttributeError, OSError, ctypes.ArgumentError):
        pass

    # 3) 旧接口兜底。
    try:
        if user32.SetProcessDPIAware():
            return "system"
    except (AttributeError, OSError):
        pass

    return "unknown"


def get_dpi_awareness_state() -> str:
    """查询进程当前实际的 DPI 感知状态（诊断用）。

    :returns: "per-monitor" / "system" / "unaware" / "unknown" / "n/a"。
    """
    if not _is_windows():
        return "n/a"

    try:
        shcore = ctypes.windll.shcore
        value = ctypes.c_int(0)
        # GetProcessDpiAwareness(None, &value)：0=unaware 1=system 2=per-monitor
        if shcore.GetProcessDpiAwareness(None, ctypes.byref(value)) == 0:
            return {0: "unaware", 1: "system", 2: "per-monitor"}.get(int(value.value), "unknown")
    except (AttributeError, OSError, ctypes.ArgumentError):
        pass

    return "unknown"


def _system_dpi() -> int:
    """系统 DPI（96 = 100% 缩放）。取不到返回 96。"""
    if not _is_windows():
        return 96

    user32 = ctypes.windll.user32
    try:
        dpi = int(user32.GetDpiForSystem())
        if dpi > 0:
            return dpi
    except (AttributeError, OSError):
        pass

    try:
        gdi32 = ctypes.windll.gdi32
        hdc = user32.GetDC(0)
        try:
            dpi = int(gdi32.GetDeviceCaps(hdc, 88))  # LOGPIXELSX
        finally:
            user32.ReleaseDC(0, hdc)
        if dpi > 0:
            return dpi
    except (AttributeError, OSError):
        pass

    return 96


def _monitor_dpi(hmonitor: Any) -> int:
    """单台显示器的有效 DPI（Per-Monitor 感知下才是该屏真实 DPI）。"""
    if not _is_windows():
        return 96

    try:
        shcore = ctypes.windll.shcore
        dpi_x = ctypes.c_uint(0)
        dpi_y = ctypes.c_uint(0)
        if shcore.GetDpiForMonitor(
            ctypes.c_void_p(hmonitor),
            _MDT_EFFECTIVE_DPI,
            ctypes.byref(dpi_x),
            ctypes.byref(dpi_y),
        ) == 0 and int(dpi_x.value) > 0:
            return int(dpi_x.value)
    except (AttributeError, OSError, ctypes.ArgumentError):
        pass

    return _system_dpi()


def _screen_fingerprint() -> Tuple[int, ...]:
    """屏幕指纹：分辨率 / 显示器数量 / 系统 DPI 任一变都会变（缓存自检用）。

    注意：必须先置 DPI 感知再读度量，否则未感知进程读到的是「逻辑（虚拟化）」尺寸
    （例如 1920x1200@125% 会被读成 1536x960 / dpi 96），指纹会在感知后突变。
    """
    ensure_dpi_awareness()

    if not _is_windows():
        return (0, 0, 0, 0, 0, _system_dpi())

    user32 = ctypes.windll.user32
    try:
        return (
            int(user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)),
            int(user32.GetSystemMetrics(_SM_CMONITORS)),
            _system_dpi(),
        )
    except (AttributeError, OSError):
        return (0, 0, 0, 0, 0, _system_dpi())


# --------------------------------------------------------------------------- #
# 显示器枚举 / 屏幕画像
# --------------------------------------------------------------------------- #

def get_monitors() -> List[Dict[str, Any]]:
    """枚举显示器，按 (左上角 x, y) 排序，序号从 1 开始。

    :returns: ``[{"index","primary","rect":(x,y,w,h),"work_rect":(x,y,w,h),
                 "dpi","scale"}, ...]``；枚举失败时退回「单台主屏」合成项。
    """
    ensure_dpi_awareness()

    monitors: List[Dict[str, Any]] = []

    if _is_windows():
        user32 = ctypes.windll.user32

        # EnumDisplayMonitors 回调：BOOL CALLBACK(HMONITOR, HDC, LPRECT, LPARAM)
        proc_type = ctypes.WINFUNCTYPE(
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
                dpi = _monitor_dpi(h_monitor)
                monitors.append(
                    {
                        "primary": bool(info.dwFlags & _MONITORINFOF_PRIMARY),
                        "rect": (
                            int(info.rcMonitor.left),
                            int(info.rcMonitor.top),
                            int(info.rcMonitor.right - info.rcMonitor.left),
                            int(info.rcMonitor.bottom - info.rcMonitor.top),
                        ),
                        "work_rect": (
                            int(info.rcWork.left),
                            int(info.rcWork.top),
                            int(info.rcWork.right - info.rcWork.left),
                            int(info.rcWork.bottom - info.rcWork.top),
                        ),
                        "dpi": int(dpi),
                        "scale": round(int(dpi) / 96.0, 4),
                    }
                )
            return 1  # 继续枚举

        try:
            callback = proc_type(_callback)  # 持有引用，避免枚举中被 GC
            user32.EnumDisplayMonitors(None, None, callback, 0)
        except (AttributeError, OSError, ctypes.ArgumentError):
            monitors = []

    if not monitors:
        # 兜底：pyautogui 主屏尺寸 + 系统 DPI。
        width, height = 0, 0
        try:
            import pyautogui

            width, height = (int(v) for v in pyautogui.size())
        except Exception:  # noqa: BLE001 - 兜底路径，尽力而为
            pass

        dpi = _system_dpi()
        if width <= 0 or height <= 0:
            width, height = 1920, 1200
        monitors = [
            {
                "primary": True,
                "rect": (0, 0, width, height),
                "work_rect": (0, 0, width, height),
                "dpi": dpi,
                "scale": round(dpi / 96.0, 4),
            }
        ]

    monitors.sort(key=lambda item: (item["rect"][0], item["rect"][1]))
    for index, item in enumerate(monitors, start=1):
        item["index"] = index
    return monitors


def get_virtual_rect(monitors: Optional[List[Dict[str, Any]]] = None) -> Tuple[int, int, int, int]:
    """整个虚拟桌面 (x, y, w, h)：多显示器时 x/y 可能为负。"""
    if _is_windows():
        user32 = ctypes.windll.user32
        try:
            x = int(user32.GetSystemMetrics(_SM_XVIRTUALSCREEN))
            y = int(user32.GetSystemMetrics(_SM_YVIRTUALSCREEN))
            width = int(user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN))
            height = int(user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN))
            if width > 0 and height > 0:
                return x, y, width, height
        except (AttributeError, OSError):
            pass

    mons = monitors if monitors is not None else get_monitors()
    left = min(item["rect"][0] for item in mons)
    top = min(item["rect"][1] for item in mons)
    right = max(item["rect"][0] + item["rect"][2] for item in mons)
    bottom = max(item["rect"][1] + item["rect"][3] for item in mons)
    return left, top, max(1, right - left), max(1, bottom - top)


def _build_profile() -> Dict[str, Any]:
    """实时采集一次屏幕画像（不读缓存）。"""
    ensure_dpi_awareness()

    monitors = get_monitors()
    primary = next((item for item in monitors if item["primary"]), monitors[0])
    rect = primary["rect"]
    dpi = int(primary["dpi"])
    scale = round(dpi / 96.0, 4) or 1.0

    physical = (int(rect[2]), int(rect[3]))
    logical = (max(1, int(round(physical[0] / scale))), max(1, int(round(physical[1] / scale))))

    return {
        "physical_size": physical,
        "logical_size": logical,
        "dpi": dpi,
        "scale": scale,
        "max_scale": max([float(item["scale"]) for item in monitors] or [scale]),
        "monitors": monitors,
        "monitor_count": len(monitors),
        "multi_monitor": len(monitors) > 1,
        "primary_rect": (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])),
        "virtual_rect": get_virtual_rect(monitors),
        "dpi_awareness": get_dpi_awareness_state(),
        "platform": sys.platform,
    }


# --------------------------------------------------------------------------- #
# 画像缓存（带分辨率/缩放自检）
# --------------------------------------------------------------------------- #
_PROFILE_CACHE: Optional[Dict[str, Any]] = None
_PROFILE_FINGERPRINT: Optional[Tuple[int, ...]] = None
_CONFIG_CACHE: Optional[Dict[str, Any]] = None


def invalidate_screen_cache() -> None:
    """清空屏幕画像缓存（分辨率 / 缩放 / 显示器变化后立即重采）。"""
    global _PROFILE_CACHE, _PROFILE_FINGERPRINT
    _PROFILE_CACHE = None
    _PROFILE_FINGERPRINT = None


def _load_config_cached() -> Dict[str, Any]:
    """读取项目 config.json（只读一次，失败返回空 dict）。"""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        try:
            from .config import load_config

            data = load_config()
            _CONFIG_CACHE = data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 - 配置不可用时用内置默认值
            _CONFIG_CACHE = {}
    return _CONFIG_CACHE or {}


def get_screen_profile(force: bool = False) -> Dict[str, Any]:
    """返回屏幕画像（带缓存 + 变化自检）。

    :param force: True 时强制重采（忽略缓存）。
    :returns: 画像 dict，键包括 physical_size / logical_size / dpi / scale / max_scale /
              monitors / monitor_count / multi_monitor / primary_rect / virtual_rect /
              dpi_awareness / platform / fingerprint。

    每次调用都会用一次极廉价的指纹（虚拟桌面 rect + 显示器数 + 系统 DPI）比对缓存，
    因此改分辨率、改缩放、插拔显示器后无需重启进程即可自动换到新几何。
    """
    global _PROFILE_CACHE, _PROFILE_FINGERPRINT

    fingerprint = _screen_fingerprint()
    if (not force) and _PROFILE_CACHE is not None and _PROFILE_FINGERPRINT == fingerprint:
        return dict(_PROFILE_CACHE)

    profile = _build_profile()
    profile["fingerprint"] = fingerprint
    _PROFILE_CACHE = profile
    _PROFILE_FINGERPRINT = fingerprint
    return dict(profile)


def get_physical_screen_size() -> Tuple[int, int]:
    """返回主屏**物理像素**分辨率 ``(width, height)``（DPI-aware）。

    顺序：EnumDisplayMonitors + GetMonitorInfo 取主屏 rcMonitor -> GetSystemMetrics
    -> pyautogui.size() -> 已知兜底值。分辨率变化会自动重新探测（内部走画像缓存自检）。

    兼容旧调用方：``get_physical_screen_size.cache_clear()`` 等价于
    ``invalidate_screen_cache()``。
    """
    try:
        return tuple(get_screen_profile()["physical_size"])  # type: ignore[return-value]
    except Exception:  # noqa: BLE001 - 极端环境下回退到系统度量
        if _is_windows():
            try:
                user32 = ctypes.windll.user32
                width = int(user32.GetSystemMetrics(_SM_CXSCREEN))
                height = int(user32.GetSystemMetrics(_SM_CYSCREEN))
                if width > 0 and height > 0:
                    return width, height
            except (AttributeError, OSError):
                pass
        return 1920, 1200


# 兼容旧文档里提到的 cache_clear()（历史代码可能调用过）。
get_physical_screen_size.cache_clear = invalidate_screen_cache  # type: ignore[attr-defined]


def describe_screen(
    profile: Optional[Dict[str, Any]] = None,
    rect: Optional[Tuple[int, int, int, int]] = None,
    image_size: Optional[Tuple[int, int]] = None,
) -> str:
    """把屏幕画像 + 本轮截图参照系写成一段中文说明（日志 / prompt 用）。"""
    info = profile if profile is not None else get_screen_profile()
    phys = tuple(info.get("physical_size") or (0, 0))
    logical = tuple(info.get("logical_size") or (0, 0))
    scale = float(info.get("scale") or 1.0)
    monitors = info.get("monitors") or []
    count = int(info.get("monitor_count") or len(monitors) or 1)

    lines = [
        f"物理分辨率 {phys[0]}x{phys[1]}（主屏），Windows 缩放 {round(scale * 100)}%"
        f"（dpi {info.get('dpi')}），逻辑分辨率 {logical[0]}x{logical[1]}；显示器 {count} 台。",
    ]

    if rect:
        x, y, width, height = (int(v) for v in rect)
        kind = "整块虚拟桌面（多显示器拼接）" if count > 1 and (width, height) != phys else "主屏"
        lines.append(f"本轮截图覆盖：{kind}，物理矩形 起点({x},{y}) 尺寸 {width}x{height}。")
    else:
        rect_values = info.get("primary_rect") or (0, 0, phys[0], phys[1])
        x, y, width, height = (int(v) for v in rect_values)
        lines.append(f"坐标参照系：主屏，物理矩形 起点({x},{y}) 尺寸 {width}x{height}。")

    if image_size:
        lines.append(f"模型看到的截图尺寸：{int(image_size[0])}x{int(image_size[1])}（已按屏幕自适应缩放）。")

    right = x + width - 1
    bottom = y + height - 1
    lines.append(
        f"换算：归一化坐标 0~1000 相对本矩形，(0,0)={x},{y}，(1000,1000)={right},{bottom}；"
        f"物理像素 = {x} + nx/1000*{width}, {y} + ny/1000*{height}。"
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 分辨率 / 缩放自适应：帧矩形 + 采样比例
# --------------------------------------------------------------------------- #

def _screen_section(cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """取 config.json 的 screen 段（cfg 为 None 时读项目配置）。"""
    data = cfg if isinstance(cfg, dict) else _load_config_cached()
    section = data.get("screen") if isinstance(data, dict) else None
    return section if isinstance(section, dict) else {}


def get_frame_sampling(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """帧采样参数（config.screen.frame，缺项用内置默认值）。

    :returns: ``{"long_side_ratio","min_long_side","max_long_side","jpeg_quality"}``。
    """
    merged: Dict[str, Any] = dict(_DEFAULT_FRAME_SAMPLING)
    section = _screen_section(cfg).get("frame")
    if isinstance(section, dict):
        for key, value in section.items():
            if key in merged and value is not None:
                merged[key] = value

    try:
        merged["long_side_ratio"] = float(merged["long_side_ratio"])
        merged["min_long_side"] = int(merged["min_long_side"])
        merged["max_long_side"] = int(merged["max_long_side"])
        merged["jpeg_quality"] = int(merged["jpeg_quality"])
    except (TypeError, ValueError):
        merged = dict(_DEFAULT_FRAME_SAMPLING)
    return merged


def resolve_all_screens(
    profile: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    all_screens: Optional[bool] = None,
) -> bool:
    """本轮是否把多显示器拼成一整张（config.screen.all_screens = auto/true/false）。

    auto（默认）：多显示器时 true（否则只看得到主屏，其它屏上的窗口不在画面里），单屏时 false。
    """
    if all_screens is not None:
        return bool(all_screens)

    info = profile if profile is not None else get_screen_profile()
    mode = _screen_section(cfg).get("all_screens", "auto")
    if isinstance(mode, bool):
        return mode

    text = str(mode).strip().lower()
    if text in ("", "auto"):
        return bool(info.get("multi_monitor"))
    return text in ("1", "true", "yes", "on", "all", "全部", "全屏")


def get_frame_rect(
    profile: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    all_screens: Optional[bool] = None,
) -> Tuple[int, int, int, int]:
    """本轮截图 / 归一化坐标参照系的物理矩形 ``(x, y, w, h)``。"""
    info = profile if profile is not None else get_screen_profile()
    rect = info.get("virtual_rect") if resolve_all_screens(info, cfg, all_screens) else info.get("primary_rect")
    if not rect:
        width, height = get_physical_screen_size()
        return 0, 0, width, height

    x, y, width, height = (int(v) for v in rect)
    return x, y, max(1, width), max(1, height)


def compute_frame_scale(
    profile: Optional[Dict[str, Any]] = None,
    cfg: Optional[Dict[str, Any]] = None,
    all_screens: Optional[bool] = None,
) -> float:
    """按「逻辑分辨率」算自适应帧缩放比例（只缩不放，1.0 表示原尺寸）。

    target = clamp(long_side_ratio * (物理长边 / 缩放), min_long_side, max_long_side)，
    scale = min(1.0, target / 物理长边)。这样不同分辨率/缩放下送进模型的图
    在「每逻辑像素多少像素」这一维度保持等效，字不会糊也不会白烧 token。
    """
    info = profile if profile is not None else get_screen_profile()
    sampling = get_frame_sampling(cfg)
    rect = get_frame_rect(info, cfg, all_screens)

    long_side = max(int(rect[2]), int(rect[3]))
    if long_side <= 0:
        return 1.0

    scale_ref = float(info.get("max_scale") or info.get("scale") or 1.0)
    if scale_ref <= 0:
        scale_ref = 1.0

    logical_long = long_side / scale_ref
    target = int(round(float(sampling["long_side_ratio"]) * logical_long))
    target = int(_clamp(target, int(sampling["min_long_side"]), int(sampling["max_long_side"])))
    return float(min(1.0, target / float(long_side)))


# --------------------------------------------------------------------------- #
# 截图
# --------------------------------------------------------------------------- #

def _grab_image(rect: Tuple[int, int, int, int], all_screens: bool):
    """按物理矩形抓图；主屏整屏时走 pyautogui。

    多显示器 / 非主屏原点 / 自定义矩形时走 ``PIL.ImageGrab.grab(bbox=..., all_screens=True)``，
    bbox 用物理像素（进程已 DPI 感知），负原点也能正确裁剪。
    """
    x, y, width, height = (int(v) for v in rect)
    primary_width, primary_height = get_physical_screen_size()

    if all_screens or (x, y, width, height) != (0, 0, primary_width, primary_height):
        try:
            from PIL import ImageGrab

            image = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
            if image.size == (width, height):
                return image
        except Exception:  # noqa: BLE001 - 抓取失败退回 pyautogui 主屏
            pass

    import pyautogui

    return pyautogui.screenshot()


def capture_frame(
    path: Optional[str] = None,
    all_screens: Optional[bool] = None,
    cfg: Optional[Dict[str, Any]] = None,
    scale: Optional[float] = None,
    quality: Optional[int] = None,
    profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """截图 -> 按屏幕自适应缩放 -> 落盘，返回帧几何信息（执行层据此换算坐标）。

    :param path: 目标文件；None 时写临时文件。后缀 .png 存 PNG，否则存 JPEG。
    :param all_screens: 是否拼整块虚拟桌面；None 时按 config.screen.all_screens（默认 auto）。
    :param cfg: 已加载的配置 dict（None 时内部读 config.json）。
    :param scale: 强制缩放比例；None 时用 compute_frame_scale() 自适应。
    :param quality: JPEG 质量；None 时用 config.screen.frame.jpeg_quality。
    :returns: ``{"path","size","source_size","rect","origin","capture_scale",
                "all_screens","format","quality","profile"}``。

    ``rect`` 就是本轮归一化坐标的参照系（物理像素），务必传给 core.coord.set_active_frame()。
    """
    info = profile if profile is not None else get_screen_profile()
    use_all = resolve_all_screens(info, cfg, all_screens)
    rect = get_frame_rect(info, cfg, use_all)

    if scale is None:
        scale = compute_frame_scale(info, cfg, use_all)
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        scale = 1.0
    if not (scale > 0):
        scale = 1.0

    sampling = get_frame_sampling(cfg)
    if quality is None:
        quality = int(sampling["jpeg_quality"])
    quality = int(_clamp(int(quality), 1, 95))

    ensure_dpi_awareness()
    image = _grab_image(rect, use_all)
    if image.mode != "RGB":
        image = image.convert("RGB")

    source_size = (int(image.width), int(image.height))
    if 0 < scale < 1.0:
        from PIL import Image

        new_size = (
            max(1, int(round(image.width * scale))),
            max(1, int(round(image.height * scale))),
        )
        image = image.resize(new_size, Image.LANCZOS)

    if path is None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        target = Path(tempfile.gettempdir()) / f"controller_v2_frame_{stamp}.jpg"
    else:
        target = Path(path).expanduser()

    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    if target.suffix.lower() == ".png":
        image.save(str(target), format="PNG")
        fmt = "PNG"
    else:
        image.save(str(target), format="JPEG", quality=quality)
        fmt = "JPEG"

    return {
        "path": str(target),
        "size": (int(image.width), int(image.height)),
        "source_size": source_size,
        "rect": (int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])),
        "origin": (int(rect[0]), int(rect[1])),
        "capture_scale": float(scale),
        "all_screens": bool(use_all),
        "format": fmt,
        "quality": quality,
        "profile": info,
    }


def capture_screenshot(
    path: Optional[str] = None,
    all_screens: Optional[bool] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    """兼容入口：截图并保存为 PNG（原尺寸，默认主屏；多屏按配置拼整块），返回绝对路径。

    PNG + 物理像素原尺寸 + 默认主屏；当 ``all_screens`` 配置为 auto 且确实是多显示器时，
    改为拼整块虚拟桌面。需要自适应缩放 / 帧几何时用 ``capture_frame()``。
    """
    if path is None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = str(Path(tempfile.gettempdir()) / f"controller_v2_screen_{stamp}.png")

    frame = capture_frame(path=path, all_screens=all_screens, cfg=cfg, scale=1.0)
    return str(frame["path"])


__all__ = [
    "ensure_dpi_awareness",
    "get_dpi_awareness_state",
    "get_screen_profile",
    "get_monitors",
    "get_virtual_rect",
    "get_physical_screen_size",
    "get_frame_rect",
    "get_frame_sampling",
    "compute_frame_scale",
    "resolve_all_screens",
    "describe_screen",
    "invalidate_screen_cache",
    "capture_frame",
    "capture_screenshot",
]


def _self_check() -> int:
    """``python -m core.screen``：打印屏幕画像与自适应帧尺寸（自检用）。"""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    ensure_dpi_awareness()
    profile = get_screen_profile(force=True)
    print("DPI 感知:", profile["dpi_awareness"], "| 平台:", profile["platform"])
    for monitor in profile["monitors"]:
        print(
            f"显示器 {monitor['index']}: rect={monitor['rect']} "
            f"工作区={monitor['work_rect']} dpi={monitor['dpi']} "
            f"缩放={round(monitor['scale'] * 100)}%{' [主屏]' if monitor['primary'] else ''}"
        )
    print("虚拟桌面 rect:", profile["virtual_rect"], "| 显示器数:", profile["monitor_count"])
    print("帧采样参数:", get_frame_sampling())

    for use_all in (False, True):
        rect = get_frame_rect(profile, None, use_all)
        scale = compute_frame_scale(profile, None, use_all)
        size = (max(1, int(round(rect[2] * scale))), max(1, int(round(rect[3] * scale))))
        print(
            f"all_screens={use_all}: 帧矩形={rect} 缩放={round(scale, 4)} "
            f"-> 模型帧 {size[0]}x{size[1]}"
        )

    frame = capture_frame()
    print("实拍一帧:", frame["path"], frame["size"], "来源", frame["source_size"], frame["format"])
    print(describe_screen(frame["profile"], frame["rect"], frame["size"]))

    try:
        Path(frame["path"]).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(_self_check())
