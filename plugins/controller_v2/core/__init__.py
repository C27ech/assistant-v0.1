"""controller_v2 核心库。

本包提供配置、屏幕（DPI 感知 + 截图）、坐标换算、豆包视觉客户端与安全护栏，
供 router C 层与 vision_exec A 层直接调用。

对外公开接口：
    - config:   load_config / resolve_ark_api_key
    - screen:   ensure_dpi_awareness / get_physical_screen_size / capture_screenshot
    - coord:    norm_to_physical / physical_to_norm
    - vision_client: multimodal_decide / verify_expectation
    - guard:    check_action / ALLOWED_ACTIONS / FORBIDDEN_ACTIONS
"""

from . import config  # noqa: F401
from . import screen  # noqa: F401
from . import coord  # noqa: F401
from . import vision_client  # noqa: F401
from . import guard  # noqa: F401

__all__ = [
    "config",
    "screen",
    "coord",
    "vision_client",
    "guard",
]

__version__ = "0.1.0"
