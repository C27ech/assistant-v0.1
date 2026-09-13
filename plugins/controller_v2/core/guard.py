"""动作安全护栏（纯函数）。

check_action(action) -> (ok, reason) 只做校验，不执行任何 I/O / 系统调用。

校验项：
1. 动作白名单：只允许键鼠物理事件（mouse_move/mouse_click/mouse_double_click/
   mouse_right_click/mouse_drag/scroll/key_input/hotkey/type）。
   禁止 command/shell/file/network/api/exec/subprocess 等「作弊通道」被当成动作执行。
2. 坐标合法性：坐标字段必须是有限数值（int/float，非 bool、非 NaN/Inf）；
   越界坐标由 core.coord 在执行前统一 clamp，本层不拒绝有限数值的越界。
3. 危险文本 / 系统组合键拦截：拦截 ctrl+alt+del 等系统组合键，以及 type/key_input
   文本中明显危险的 shell 命令。
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Tuple

# 动作白名单：只允许键鼠物理事件。
ALLOWED_ACTIONS = {
    "mouse_move",
    "mouse_click",
    "mouse_double_click",
    "mouse_right_click",
    "mouse_drag",
    "scroll",
    "key_input",
    "hotkey",
    "type",
}

# 禁止被当成动作执行的「作弊通道」类型。
FORBIDDEN_ACTIONS = {
    "command",
    "shell",
    "file",
    "network",
    "api",
    "exec",
    "subprocess",
    "process",
    "socket",
    "http",
    "script",
}

# 动作对象中一旦携带这些字段且值非空，视为作弊通道注入，直接拒绝。
FORBIDDEN_FIELDS = {
    "command",
    "cmd",
    "shell",
    "code",
    "script",
    "exec",
    "executable",
    "subprocess",
    "process",
    "url",
    "network",
    "api",
    "socket",
    "http",
    "file_path",
    "path",
}

# 系统危险组合键（归一化小写、无空格后比较）。
_DANGEROUS_HOTKEYS = {
    "ctrl+alt+del",
    "ctrl+alt+delete",
    "control+alt+delete",
    "ctrl+alt+esc",
    "ctrl+shift+esc",
    "win+l",
    "windows+l",
    "super+l",
    "alt+f4",
}

# 危险文本模式：type/key_input 的文本若命中则拦截（明显 shell 命令）。
_DANGEROUS_TEXT_PATTERNS = [
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f", re.IGNORECASE),
    re.compile(r"\bformat\s+[a-z]:", re.IGNORECASE),
    re.compile(r"\bdel(ete)?\s+/[fsq]", re.IGNORECASE),
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\breboot\b", re.IGNORECASE),
    re.compile(r"\bnet\s+user\b", re.IGNORECASE),
    re.compile(r"\breg\s+(add|delete)\b", re.IGNORECASE),
    re.compile(r"\bcmd(\.exe)?\s*/[ck]", re.IGNORECASE),
    re.compile(r"\bpowershell(\.exe)?\s", re.IGNORECASE),
    re.compile(r"\bwmic\b", re.IGNORECASE),
]

# 需要做「有限数值」校验的坐标字段（位置类，0~1000 千分比）。
_POSITION_FIELDS = {
    "x",
    "y",
    "nx",
    "ny",
    "norm_x",
    "norm_y",
    "from_x",
    "from_y",
    "to_x",
    "to_y",
    "start_x",
    "start_y",
    "end_x",
    "end_y",
}

# 允许负数的位移/滚轮字段（只要求有限数值，不做 0~1000 范围限制）。
_DELTA_FIELDS = {
    "dx",
    "dy",
    "amount",
    "clicks",
    "steps",
}


def _is_finite_number(value: Any) -> bool:
    """是否为有限数值（int/float，排除 bool/NaN/Inf）。"""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False
    return False


def _normalize_keys(keys: Any) -> str:
    """把 hotkey 的 keys 归一化为小写无空格字符串，用于危险组合比较。"""
    if isinstance(keys, str):
        parts = [keys]
    elif isinstance(keys, (list, tuple)):
        parts = [str(item) for item in keys]
    else:
        return ""

    return "+".join(part.strip().lower() for part in parts if str(part).strip())


def _contains_dangerous_text(text: Any) -> bool:
    if not isinstance(text, str) or not text:
        return False
    return any(pattern.search(text) for pattern in _DANGEROUS_TEXT_PATTERNS)


def check_action(action: Dict[str, Any]) -> Tuple[bool, str]:
    """校验单个动作是否安全。

    :param action: 动作字典，必须包含字符串 ``type`` 字段。
    :returns: ``(ok, reason)``。ok=False 表示被拦截，reason 说明原因。
    """
    if not isinstance(action, dict):
        return False, "动作必须是 dict"

    action_type = action.get("type")
    if not isinstance(action_type, str) or not action_type.strip():
        return False, "动作缺少有效的字符串 type 字段"

    action_type = action_type.strip().lower()

    # 1) 白名单 + 作弊通道拦截。
    if action_type in FORBIDDEN_ACTIONS:
        return False, f"动作类型被禁止（作弊通道）: {action_type}"

    if action_type not in ALLOWED_ACTIONS:
        return False, f"动作类型不在键鼠白名单内: {action_type}"

    # 2) 作弊字段注入拦截。
    for field, value in action.items():
        if field in FORBIDDEN_FIELDS and value not in (None, "", [], {}):
            return False, f"动作携带被禁止的作弊通道字段: {field}"

    # 3) 系统组合键拦截。
    if action_type == "hotkey":
        keys = action.get("keys", action.get("key", ""))
        normalized = _normalize_keys(keys)
        if normalized in _DANGEROUS_HOTKEYS:
            return False, f"系统危险组合键被拦截: {normalized}"

    # key_input 也可能传入组合键字符串，一并校验。
    if action_type == "key_input":
        key = str(action.get("key", "")).strip()
        normalized = "+".join(
            part.strip().lower()
            for part in key.replace(" ", "").split("+")
            if part.strip()
        )
        if normalized in _DANGEROUS_HOTKEYS:
            return False, f"系统危险组合键被拦截: {normalized}"

    # 4) 坐标合法性：位置字段必须有限数值。
    for field in _POSITION_FIELDS:
        if field in action:
            value = action[field]
            if value is None:
                continue
            if not _is_finite_number(value):
                return False, f"坐标字段 {field} 必须是有限数值，收到: {value!r}"

    # 5) 位移/滚轮字段：只要求有限数值。
    for field in _DELTA_FIELDS:
        if field in action:
            value = action[field]
            if value is None:
                continue
            if not _is_finite_number(value):
                return False, f"数值字段 {field} 必须是有限数值，收到: {value!r}"

    # 6) 危险文本拦截。
    if action_type in {"type", "key_input"}:
        text = action.get("text", action.get("key", ""))
        if _contains_dangerous_text(text):
            return False, "文本内容命中危险 shell 命令特征，已拦截"

    return True, "ok"


__all__ = [
    "ALLOWED_ACTIONS",
    "FORBIDDEN_ACTIONS",
    "check_action",
]
