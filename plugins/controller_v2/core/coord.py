"""归一化坐标 <-> 物理像素坐标换算（支持「坐标帧」= 本轮截图覆盖的屏幕矩形）。

全项目统一使用「0~1000 千分比」归一化坐标作为 AI/决策/经验层的坐标语义；
仅在执行前换算为物理像素：

    px = frame.x + round(norm_x / 1000 * frame.w)
    py = frame.y + round(norm_y / 1000 * frame.h)

换算后 clamp 到 ``[frame.x, frame.x+frame.w-1]`` / ``[frame.y, frame.y+frame.h-1]``。

坐标帧（frame）语义 —— 这是分辨率 / 缩放 / 多显示器自适应的基础：

1. 显式传入 ``frame=(x, y, w, h)``（物理像素，原点可为负）；
2. 否则若调用过 ``set_active_frame(rect)``，用当前激活的帧
   （视觉执行器每轮截图后都会设置：单屏=主屏；多屏=整块虚拟桌面）；
3. 否则若显式传入 physical_width/physical_height，等价于 frame=(0, 0, w, h)（旧行为）；
4. 否则退回主屏 ``(0, 0, 物理宽, 物理高)``（旧行为，DPI 感知）。

反向换算（像素 -> 千分比）用于 OCR / 截图 bbox 等场景，规则与上面一一对应。

物理分辨率统一来自 core.screen.get_screen_profile()（分辨率/缩放变化自动重采）。
严禁把千分比坐标当作物理像素直接放大使用。
"""

from __future__ import annotations

from typing import Optional, Tuple

NORM_MAX = 1000

# 当前激活的坐标帧 (x, y, w, h)：物理像素，原点允许为负。
_ACTIVE_FRAME: Optional[Tuple[int, int, int, int]] = None


def clamp(value: float, low: float, high: float) -> float:
    """把 value 钳制到 [low, high] 区间（兼容 int/float 入参）。"""
    if value < low:
        return low
    if value > high:
        return high
    return value


def set_active_frame(frame: Optional[Tuple[int, int, int, int]]) -> Optional[Tuple[int, int, int, int]]:
    """设置当前坐标帧（物理像素 ``(x, y, w, h)``；None / 非法值 = 清除）。

    :returns: 生效后的帧（清除时返回 None）。

    视觉执行器每轮截图后调用 ``set_active_frame(capture["rect"])``，
    这样后续 action 层的 ``norm_to_physical`` 会把归一化坐标落到本轮截图覆盖的那块屏幕
    （多显示器时为整块虚拟桌面，原点可能是负数）。
    """
    global _ACTIVE_FRAME

    if frame is None:
        _ACTIVE_FRAME = None
        return None

    try:
        x, y, width, height = (int(v) for v in frame)
    except (TypeError, ValueError):
        _ACTIVE_FRAME = None
        return None

    if width <= 0 or height <= 0:
        _ACTIVE_FRAME = None
        return None

    _ACTIVE_FRAME = (x, y, width, height)
    return _ACTIVE_FRAME


def get_active_frame() -> Optional[Tuple[int, int, int, int]]:
    """返回当前坐标帧；未设置时为 None。"""
    return _ACTIVE_FRAME


def clear_active_frame() -> None:
    """清除坐标帧（回到主屏语义）。"""
    set_active_frame(None)


def _resolve_frame(
    frame: Optional[Tuple[int, int, int, int]] = None,
    physical_width: Optional[int] = None,
    physical_height: Optional[int] = None,
) -> Tuple[int, int, int, int]:
    """按优先级解析出实际使用的坐标帧 (x, y, w, h)。"""
    if frame is not None:
        try:
            x, y, width, height = (int(v) for v in frame)
            if width > 0 and height > 0:
                return x, y, width, height
        except (TypeError, ValueError):
            pass

    if physical_width is not None and physical_height is not None:
        width, height = int(physical_width), int(physical_height)
        if width > 0 and height > 0:
            return 0, 0, width, height

    active = _ACTIVE_FRAME
    if active is not None:
        return active

    try:
        from .screen import get_screen_profile

        rect = get_screen_profile().get("primary_rect")
        if rect:
            x, y, width, height = (int(v) for v in rect)
            if width > 0 and height > 0:
                return x, y, width, height
    except Exception:  # noqa: BLE001 - 画像不可用时退回已知兜底
        pass

    return 0, 0, 1920, 1200


def norm_to_physical(
    norm_x: float,
    norm_y: float,
    physical_width: Optional[int] = None,
    physical_height: Optional[int] = None,
    frame: Optional[Tuple[int, int, int, int]] = None,
) -> Tuple[int, int]:
    """千分比归一化坐标 -> 物理像素坐标（round + clamp）。

    :param norm_x: 0~1000 归一化横坐标（允许有限数值，越界自动 clamp）。
    :param norm_y: 0~1000 归一化纵坐标。
    :param physical_width: 兼容参数：显式物理屏宽（等价 frame=(0, 0, w, h)）。
    :param physical_height: 兼容参数：显式物理屏高。
    :param frame: 坐标帧 ``(x, y, w, h)``（物理像素，原点可为负）；缺省用激活帧/主屏。
    :returns: ``(px, py)`` 物理像素，clamp 到帧内。
    """
    x, y, width, height = _resolve_frame(frame, physical_width, physical_height)

    px = x + int(round(clamp(float(norm_x), 0.0, float(NORM_MAX)) / NORM_MAX * width))
    py = y + int(round(clamp(float(norm_y), 0.0, float(NORM_MAX)) / NORM_MAX * height))

    px = int(clamp(px, x, x + width - 1))
    py = int(clamp(py, y, y + height - 1))
    return px, py


def physical_to_norm(
    px: float,
    py: float,
    physical_width: Optional[int] = None,
    physical_height: Optional[int] = None,
    frame: Optional[Tuple[int, int, int, int]] = None,
) -> Tuple[int, int]:
    """物理像素坐标 -> 0~1000 千分比归一化坐标（round + clamp）。

    :returns: ``(norm_x, norm_y)``，clamp 到 ``[0, 1000]``。
    """
    x, y, width, height = _resolve_frame(frame, physical_width, physical_height)

    norm_x = int(round(clamp(float(px) - x, 0.0, float(width)) / width * NORM_MAX))
    norm_y = int(round(clamp(float(py) - y, 0.0, float(height)) / height * NORM_MAX))

    norm_x = int(clamp(norm_x, 0, NORM_MAX))
    norm_y = int(clamp(norm_y, 0, NORM_MAX))
    return norm_x, norm_y


def describe_frame(
    frame: Optional[Tuple[int, int, int, int]] = None,
    physical_width: Optional[int] = None,
    physical_height: Optional[int] = None,
) -> str:
    """把当前生效的坐标帧写成一行中文（日志用）。"""
    x, y, width, height = _resolve_frame(frame, physical_width, physical_height)
    source = "显式" if frame is not None else ("激活帧" if _ACTIVE_FRAME is not None else "主屏缺省")
    return f"坐标帧[{source}] 起点({x},{y}) 尺寸 {width}x{height}（物理像素）"


__all__ = [
    "NORM_MAX",
    "clamp",
    "set_active_frame",
    "get_active_frame",
    "clear_active_frame",
    "norm_to_physical",
    "physical_to_norm",
    "describe_frame",
]
