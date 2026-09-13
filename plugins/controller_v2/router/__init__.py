"""router C 层：接口优先、键鼠兜底。

对外暴露：
- RouteDecision / route：核心路由判定契约，供 main.py 使用。
- channels：接口通道注册表，可扩展。
"""

from __future__ import annotations

from .router import RouteDecision, route
from . import channels

__all__ = [
    "RouteDecision",
    "route",
    "channels",
]
