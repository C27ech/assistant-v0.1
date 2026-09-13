"""controller_v2 动作层。

对外公开：
    - execute(action)            执行单个已校验的键鼠物理动作（返回结果 dict）
    - execute_interface(action)  「接口优先」执行器骨架（win32/cli/http 注册表 + 分发）
    - move/click/double_click/right_click/drag/scroll/key/hotkey/type_text
      键鼠物理执行原语（入参为 0~1000 千分比归一化坐标，执行前自动换算物理像素）

本层只做键鼠物理事件与接口通道分发，不包含任何经验规则/记忆匹配逻辑。
"""

from .action import (
    click,
    double_click,
    drag,
    execute,
    execute_interface,
    hotkey,
    key,
    move,
    register_interface_handler,
    right_click,
    scroll,
    type_text,
)

__all__ = [
    "click",
    "double_click",
    "drag",
    "execute",
    "execute_interface",
    "hotkey",
    "key",
    "move",
    "register_interface_handler",
    "right_click",
    "scroll",
    "type_text",
]
