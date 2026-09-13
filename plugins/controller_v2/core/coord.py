"""归一化坐标 <-> 物理像素坐标换算。

全项目统一使用「0~1000 千分比」归一化坐标作为 AI/决策/经验层的坐标语义；
仅在执行前换算为物理像素：

    px = round(norm_x / 1000 * physical_width)
    py = round(norm_y / 1000 * physical_height)

换算后 clamp 到 ``[0, width-1]`` / ``[0, height-1]``。
反向换算（像素 -> 千分比）用于 OCR/截图 bbox 等场景：

    norm_x = round(px / physical_width * 1000)
    norm_y = round(py / physical_height * 1000)

物理分辨率统一来自 core.screen.get_physical_screen_size()（DPI-aware）。
严禁把千分比坐标当作物理像素直接放大使用。
"""

from __future__ import annotations

from typing import Optional, Tuple

NORM_MAX = 1000


def clamp(value: float, low: float, high: float) -> float:
    """把 value 钳制到 [low, high] 区间（兼容 int/float 入参）。"""
    if value < low:
        return low
    if value > high:
        return high
    return value


def norm_to_physical(
    norm_x: float,
    norm_y: float,
    physical_width: Optional[int] = None,
    physical_height: Optional[int] = None,
) -> Tuple[int, int]:
    """千分比归一化坐标 -> 物理像素坐标（round + clamp）。

    :param norm_x: 0~1000 归一化横坐标（允许有限数值，越界自动 clamp）。
    :param norm_y: 0~1000 归一化纵坐标。
    :param physical_width: 物理屏宽；缺省自动获取（DPI-aware）。
    :param physical_height: 物理屏高；缺省自动获取。
    :returns: ``(px, py)`` 物理像素，clamp 到 ``[0, w-1]`` / ``[0, h-1]``。
    """
    from .screen import get_physical_screen_size

    if physical_width is None or physical_height is None:
        physical_width, physical_height = get_physical_screen_size()

    width = int(physical_width)
    height = int(physical_height)

    # 屏幕尺寸非法时返回原点，避免除零/负边界。
    if width <= 0 or height <= 0:
        return 0, 0

    px = int(round(clamp(float(norm_x), 0.0, float(NORM_MAX)) / NORM_MAX * width))
    py = int(round(clamp(float(norm_y), 0.0, float(NORM_MAX)) / NORM_MAX * height))

    px = int(clamp(px, 0, width - 1))
    py = int(clamp(py, 0, height - 1))
    return px, py


def physical_to_norm(
    px: float,
    py: float,
    physical_width: Optional[int] = None,
    physical_height: Optional[int] = None,
) -> Tuple[int, int]:
    """物理像素坐标 -> 0~1000 千分比归一化坐标（round + clamp）。

    :returns: ``(norm_x, norm_y)``，clamp 到 ``[0, 1000]``。
    """
    from .screen import get_physical_screen_size

    if physical_width is None or physical_height is None:
        physical_width, physical_height = get_physical_screen_size()

    width = int(physical_width)
    height = int(physical_height)

    if width <= 0 or height <= 0:
        return 0, 0

    norm_x = int(round(clamp(float(px), 0.0, float(width)) / width * NORM_MAX))
    norm_y = int(round(clamp(float(py), 0.0, float(height)) / height * NORM_MAX))

    norm_x = int(clamp(norm_x, 0, NORM_MAX))
    norm_y = int(clamp(norm_y, 0, NORM_MAX))
    return norm_x, norm_y


__all__ = [
    "NORM_MAX",
    "clamp",
    "norm_to_physical",
    "physical_to_norm",
]
