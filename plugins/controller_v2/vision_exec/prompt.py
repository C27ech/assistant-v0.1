"""视觉决策 / verify 用的 prompt 模板与响应解析（有状态 agent loop 版）。

本模块只做「文本进、结构化 dict/三态出」的纯函数工作，不发起任何网络请求。
决策 status 在原有「完成 / 确定 / 不确定」之外增加「卡死」，让模型能自判
「卡死无法完成」并终止，避免无意义烧钱。

动作 schema 与 ``action.action`` 层保持一致：坐标一律 0~1000 千分比整数。
prompt 里会注入一段由 core.screen.describe_screen() 生成的「屏幕几何信息」
（物理/逻辑分辨率、Windows 缩放、显示器数量、坐标参照系），
这样模型在不同分辨率 / 缩放的机器上都能把坐标落在正确位置。
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Dict, Optional

from core.guard import ALLOWED_ACTIONS

# 没有屏幕几何信息时的兜底说明（保证旧调用方 prompt 结构完整）。
_DEFAULT_SCREEN_INFO = (
    "（未提供具体屏幕几何：请把坐标理解为相对整张截图的 0~1000 归一化比例）"
)


def _screen_info_text(screen_info: Optional[str]) -> str:
    """规整屏幕几何描述文本；空值用兜底说明。"""
    text = str(screen_info or "").strip()
    return text or _DEFAULT_SCREEN_INFO

# ---------------------------------------------------------------------------
# prompt 模板
# ---------------------------------------------------------------------------

_DECISION_PROMPT = """你是 Windows 桌面自动化控制器。请观察当前屏幕截图，根据任务决定「下一步要执行的单个键鼠动作」。

任务：__TASK__
当前是第 __ROUND__ 轮尝试（最多 __MAX_ROUNDS__ 轮）。

【屏幕几何信息（由程序按本机实际分辨率 / Windows 缩放 / 显示器布局生成）】
__SCREEN_INFO__
坐标一律相对这张截图归一化：截图左上角=(0,0)，右下角=(1000,1000)，与截图像素尺寸、屏幕分辨率、缩放比例无关。

必须只输出一个 JSON 对象（不要 Markdown 代码块、不要任何额外文字），格式如下：
{
  "status": "完成|确定|不确定|卡死",
  "action": null,
  "expectation": "任务完成后截图应明确显示的可证伪状态",
  "reason": "简短理由"
}

status 取值规则：
- 若当前截图已经明确显示「任务整体已完成」，status 填 "完成"，action 填 null。
- 若你能明确判断下一步要执行的单个动作，status 填 "确定"，并在 action 里给出该动作。
- 若找不到目标、存在歧义、需要更多信息，status 填 "不确定"，action 填 null，expectation 填 null。
- 若已经多次尝试仍无法推进，或界面状态表明继续执行没有意义（例如目标根本不存在且无替代路径），status 填 "卡死"，action 填 null。

action 只允许以下 type，字段名请严格照抄（坐标一律为 0~1000 千分比整数，左上角 (0,0)、右下角 (1000,1000)）：
1. mouse_move         {"type":"mouse_move","x":500,"y":500,"duration":0.3}
2. mouse_click        {"type":"mouse_click","x":500,"y":500,"button":"left","clicks":1,"interval":0.0}
3. mouse_double_click {"type":"mouse_double_click","x":500,"y":500}
4. mouse_right_click  {"type":"mouse_right_click","x":500,"y":500}
5. mouse_drag         {"type":"mouse_drag","from_x":100,"from_y":100,"to_x":500,"to_y":500,"button":"left","duration":0.5}
6. scroll             {"type":"scroll","amount":3,"x":500,"y":500}
7. key_input          {"type":"key_input","key":"enter"}
8. hotkey             {"type":"hotkey","keys":"ctrl+s"}
9. type               {"type":"type","text":"要输入的文本","interval":0.02}

注意：
1. 每次只给一个动作；坐标必须是 0~1000 的整数。
2. 禁止输出 command/shell/file/network/api 等任何非键鼠动作，违反会被安全护栏拦截。
3. expectation 写「任务整体完成」后的可验证状态（例如「记事本窗口已打开且包含文本 hello」），不要写仅限该动作的中间状态。
4. 不确定或卡死时不要编造动作。
只输出 JSON。"""

_AGENT_DECISION_PROMPT = """你是 Windows 桌面自动化控制器。请观察屏幕截图，根据任务与历史记忆决定「下一步要执行的单个键鼠动作」。

任务：__TASK__
当前是第 __ROUND__ 轮（硬上限 __MAX_ROUNDS__ 轮）。

【最近对话上下文】
__RECENT_CONTEXT__

【历史记忆（BM25 检索的相似条目，仅作参考）】
__RETRIEVED_HISTORY__

本轮消息中附带多张图片：越靠后的越接近当前状态，最后一张是当前屏幕截图。

【屏幕几何信息（由程序按本机实际分辨率 / Windows 缩放 / 显示器布局生成）】
__SCREEN_INFO__
坐标一律相对「最后一张当前截图」归一化：截图左上角=(0,0)，右下角=(1000,1000)，与截图像素尺寸、屏幕分辨率、缩放比例无关。多显示器时这张截图是整块虚拟桌面拼接图，坐标同样按整张图归一化。

必须只输出一个 JSON 对象（不要 Markdown 代码块、不要任何额外文字），格式如下：
{
  "status": "完成|确定|不确定|卡死",
  "action": null,
  "expectation": "任务完成后截图应明确显示的可证伪状态",
  "reason": "简短理由"
}

status 取值规则：
- 若当前截图已经明确显示「任务整体已完成」，status 填 "完成"，action 填 null。
- 若你能明确判断下一步要执行的单个动作，status 填 "确定"，并在 action 里给出该动作。
- 若找不到目标、存在歧义、需要更多信息，status 填 "不确定"，action 填 null，expectation 填 null。
- 若已经多次尝试仍无法推进，或界面状态表明继续执行没有意义，status 填 "卡死"，action 填 null。

action 只允许以下 type，字段名请严格照抄（坐标一律为 0~1000 千分比整数，左上角 (0,0)、右下角 (1000,1000)）：
1. mouse_move         {"type":"mouse_move","x":500,"y":500,"duration":0.3}
2. mouse_click        {"type":"mouse_click","x":500,"y":500,"button":"left","clicks":1,"interval":0.0}
3. mouse_double_click {"type":"mouse_double_click","x":500,"y":500}
4. mouse_right_click  {"type":"mouse_right_click","x":500,"y":500}
5. mouse_drag         {"type":"mouse_drag","from_x":100,"from_y":100,"to_x":500,"to_y":500,"button":"left","duration":0.5}
6. scroll             {"type":"scroll","amount":3,"x":500,"y":500}
7. key_input          {"type":"key_input","key":"enter"}
8. hotkey             {"type":"hotkey","keys":"ctrl+s"}
9. type               {"type":"type","text":"要输入的文本","interval":0.02}

注意：
1. 每次只给一个动作；坐标必须是 0~1000 的整数。
2. 禁止输出 command/shell/file/network/api 等任何非键鼠动作，违反会被安全护栏拦截。
3. expectation 写「任务整体完成」后的可验证状态，不要写仅限该动作的中间状态。
4. 不确定或卡死时不要编造动作。
只输出 JSON。"""

_VERIFY_PROMPT = """你是 Windows 桌面自动化验证器。请根据当前截图，判断下面这条「任务完成预期」是否已经实现。

预期：__EXPECTATION__

【截图对应的屏幕信息】
__SCREEN_INFO__

必须只输出一个 JSON 对象（不要 Markdown 代码块、不要任何额外文字）：
{"fulfilled": true, "reason": "简短理由"}

fulfilled 取值：
- true：截图明确显示该预期已经实现；
- false：截图明确显示该预期尚未实现（出现明确反证）；
- null：证据不足，无法判定（此时 reason 说明缺什么证据）。
只输出 JSON。"""


def build_decision_prompt(
    task: str,
    round_no: int = 1,
    max_rounds: int = 5,
    screen_info: str = "",
) -> str:
    """构造单图决策 prompt（兼容旧接口）。

    :param screen_info: 屏幕几何描述（core.screen.describe_screen 的输出）；
                        空串时用兜底说明，保证旧调用方行为不变。
    """
    return (
        _DECISION_PROMPT.replace("__TASK__", str(task))
        .replace("__ROUND__", str(int(round_no)))
        .replace("__MAX_ROUNDS__", str(int(max_rounds)))
        .replace("__SCREEN_INFO__", _screen_info_text(screen_info))
    )


def build_agent_decision_prompt(
    task: str,
    round_no: int,
    max_rounds: int,
    recent_context: str = "",
    retrieved_history: str = "",
    screen_info: str = "",
) -> str:
    """构造有状态 agent loop 决策 prompt（含屏幕几何 + 最近上下文 + BM25 检索历史）。

    :param screen_info: 屏幕几何描述（core.screen.describe_screen 的输出），
                        让模型知道真实分辨率 / 缩放 / 显示器数 / 坐标参照系。
    """
    recent = str(recent_context or "").strip() or "（暂无）"
    history = str(retrieved_history or "").strip() or "（暂无）"
    return (
        _AGENT_DECISION_PROMPT.replace("__TASK__", str(task))
        .replace("__ROUND__", str(int(round_no)))
        .replace("__MAX_ROUNDS__", str(int(max_rounds)))
        .replace("__RECENT_CONTEXT__", recent)
        .replace("__RETRIEVED_HISTORY__", history)
        .replace("__SCREEN_INFO__", _screen_info_text(screen_info))
    )


def build_verify_prompt(expectation: str, screen_info: str = "") -> str:
    """构造 verify prompt（可带屏幕几何描述）。"""
    return (
        _VERIFY_PROMPT.replace("__EXPECTATION__", str(expectation))
        .replace("__SCREEN_INFO__", _screen_info_text(screen_info))
    )



# ---------------------------------------------------------------------------
# JSON 提取（容错）
# ---------------------------------------------------------------------------

def _try_json_loads(text: str) -> Optional[Any]:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _strip_trailing_commas(text: str) -> str:
    """去掉对象/数组内末尾逗号（模型常见 JSON 错误），尽力而为。"""
    return re.sub(r",\s*([}\]])", r"\1", text)


def extract_json_object(text: Optional[str]) -> Optional[Dict[str, Any]]:
    """从模型输出中稳健提取第一个 JSON 对象。"""
    if not isinstance(text, str) or not text.strip():
        return None

    cleaned = text.strip()

    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()

    parsed = _try_json_loads(cleaned)
    if isinstance(parsed, dict):
        return parsed

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    sliced = cleaned[start:end + 1]

    parsed = _try_json_loads(sliced)
    if isinstance(parsed, dict):
        return parsed

    repaired = _strip_trailing_commas(sliced)
    parsed = _try_json_loads(repaired)
    if isinstance(parsed, dict):
        return parsed

    try:
        value = ast.literal_eval(sliced)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        value = None
    if isinstance(value, dict):
        return value

    return None


# ---------------------------------------------------------------------------
# 决策响应解析
# ---------------------------------------------------------------------------

_ACTION_ALIASES = {
    "click": "mouse_click",
    "left_click": "mouse_click",
    "leftclick": "mouse_click",
    "mouseclick": "mouse_click",
    "move": "mouse_move",
    "mousemove": "mouse_move",
    "double_click": "mouse_double_click",
    "doubleclick": "mouse_double_click",
    "mouse_doubleclick": "mouse_double_click",
    "right_click": "mouse_right_click",
    "rightclick": "mouse_right_click",
    "mouse_rightclick": "mouse_right_click",
    "drag": "mouse_drag",
    "mousedrag": "mouse_drag",
    "scroll": "scroll",
    "wheel": "scroll",
    "key": "key_input",
    "key_input": "key_input",
    "keyinput": "key_input",
    "press": "key_input",
    "hotkey": "hotkey",
    "shortcut": "hotkey",
    "type": "type",
    "input": "type",
    "write": "type",
    "type_text": "type",
}

_ACTION_FIELDS: Dict[str, set] = {
    "mouse_move": {"type", "x", "y", "duration"},
    "mouse_click": {"type", "x", "y", "button", "clicks", "interval"},
    "mouse_double_click": {"type", "x", "y"},
    "mouse_right_click": {"type", "x", "y"},
    "mouse_drag": {"type", "from_x", "from_y", "to_x", "to_y", "button", "duration"},
    "scroll": {"type", "amount", "x", "y"},
    "key_input": {"type", "key"},
    "hotkey": {"type", "keys"},
    "type": {"type", "text", "interval"},
}

_POSITION_FIELDS: Dict[str, set] = {
    "mouse_move": {"x", "y"},
    "mouse_click": {"x", "y"},
    "mouse_double_click": {"x", "y"},
    "mouse_right_click": {"x", "y"},
    "mouse_drag": {"from_x", "from_y", "to_x", "to_y"},
    "scroll": {"x", "y"},
}

_INT_FIELDS = {"clicks", "amount"}
_FLOAT_FIELDS = {"duration", "interval"}


def _normalize_status(raw: Any) -> str:
    """把模型 status 归一化为 done / action / unsure / stuck。"""
    s = str(raw or "").strip().lower()
    if s in {
        "完成", "done", "finished", "finish", "success", "complete", "completed",
        "already_done", "already done", "task_done", "task done", "任务完成",
    }:
        return "done"
    if s in {
        "确定", "action", "act", "go", "continue", "ready", "proceed", "下一步",
    }:
        return "action"
    if s in {
        "卡死", "stuck", "deadlock", "dead_end", "dead end", "无法完成",
        "无法推进", "卡住", "gives_up", "give_up", "give up", "hopeless",
    }:
        return "stuck"
    return "unsure"


def _decision(
    status: str,
    action: Optional[Dict[str, Any]],
    expectation: Optional[str],
    reason: str,
    error: Optional[str],
) -> Dict[str, Any]:
    return {
        "status": status,
        "action": action,
        "expectation": expectation,
        "reason": reason,
        "error": error,
    }


def _coerce_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_action(raw_action: Dict[str, Any]) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
    """规整模型给出的动作 dict，返回 (action, error)。"""
    if not isinstance(raw_action, dict):
        return None, "action 必须是对象"

    raw_type = raw_action.get("type")
    if raw_type is None:
        return None, "action 缺少 type 字段"

    action_type = _ACTION_ALIASES.get(
        str(raw_type).strip().lower(), str(raw_type).strip().lower()
    )
    if action_type not in ALLOWED_ACTIONS:
        return None, f"动作类型不在键鼠白名单内: {action_type}"

    allowed_fields = _ACTION_FIELDS.get(action_type)
    if allowed_fields is None:
        return None, f"动作类型缺少字段定义: {action_type}"

    position_fields = _POSITION_FIELDS.get(action_type, set())
    action: Dict[str, Any] = {"type": action_type}

    for field in allowed_fields:
        if field == "type" or field not in raw_action or raw_action[field] is None:
            continue

        value = raw_action[field]

        if field in position_fields:
            num = _coerce_number(value)
            if num is None:
                return None, f"坐标字段 {field} 必须是有限数值，收到: {value!r}"
            action[field] = max(0, min(1000, int(round(num))))
        elif field in _INT_FIELDS:
            num = _coerce_number(value)
            if num is None:
                return None, f"数值字段 {field} 必须是有限数值，收到: {value!r}"
            action[field] = int(round(num))
        elif field in _FLOAT_FIELDS:
            num = _coerce_number(value)
            if num is None:
                return None, f"数值字段 {field} 必须是有限数值，收到: {value!r}"
            action[field] = float(num)
        elif field == "keys":
            if isinstance(value, (list, tuple)):
                action[field] = [str(item) for item in value if str(item).strip()]
            else:
                action[field] = str(value)
        else:
            action[field] = value

    for field in position_fields:
        if action_type == "scroll":
            has_x = "x" in action
            has_y = "y" in action
            if has_x != has_y:
                action.pop("x", None)
                action.pop("y", None)
            continue
        if field not in action:
            return None, f"动作 {action_type} 缺少坐标字段 {field}"

    if action_type == "scroll" and "amount" not in action:
        return None, "scroll 动作缺少 amount 字段"
    if action_type == "type" and not str(action.get("text", "")).strip():
        return None, "type 动作缺少 text 字段"
    if action_type == "key_input" and not str(action.get("key", "")).strip():
        return None, "key_input 动作缺少 key 字段"
    if action_type == "hotkey" and not action.get("keys"):
        return None, "hotkey 动作缺少 keys 字段"

    return action, None


def _candidates_to_action(raw: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    candidates = raw.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    for item in candidates:
        if not isinstance(item, dict):
            continue
        x = _coerce_number(item.get("x"))
        y = _coerce_number(item.get("y"))
        if x is None or y is None:
            continue
        return {
            "type": "mouse_click",
            "x": max(0, min(1000, int(round(x)))),
            "y": max(0, min(1000, int(round(y)))),
        }
    return None


def parse_decision_response(text: Optional[str], task: str = "") -> Dict[str, Any]:
    """把模型决策响应解析为结构化决策。

    :returns:
        {
          "status": "done" | "action" | "unsure" | "stuck",
          "action": dict | None,
          "expectation": str | None,
          "reason": str,
          "error": str | None,
        }
    """
    raw = extract_json_object(text)
    if raw is None:
        return _decision(
            "unsure", None, None,
            "模型未返回可解析的 JSON 决策", "unparseable_response",
        )

    status = _normalize_status(raw.get("status"))
    reason = str(raw.get("reason", "")).strip()

    expectation = raw.get("expectation")
    if isinstance(expectation, str) and expectation.strip():
        expectation = expectation.strip()
    else:
        expectation = None

    if status == "done":
        return _decision("done", None, expectation, reason or "模型判定任务已完成", None)

    if status == "stuck":
        return _decision("stuck", None, None, reason or "模型判定卡死无法完成", None)

    action_obj = raw.get("action")

    if not isinstance(action_obj, dict) and raw.get("type"):
        action_obj = raw

    if not isinstance(action_obj, dict):
        action_obj = _candidates_to_action(raw)

    if status == "action":
        if not isinstance(action_obj, dict):
            return _decision(
                "unsure", None, None,
                reason or "模型判定需要动作，但未给出有效动作", "missing_action",
            )
        action, error = _clean_action(action_obj)
        if error:
            return _decision(
                "unsure", None, None,
                f"动作解析失败: {error}", "invalid_action",
            )
        return _decision("action", action, expectation, reason or "模型给出下一步动作", None)

    return _decision(
        "unsure", None, None,
        reason or "模型无法确定下一步动作", None,
    )


# ---------------------------------------------------------------------------
# verify 响应解析
# ---------------------------------------------------------------------------

def parse_verify_response(text: Optional[str]) -> Optional[bool]:
    """解析 verify 响应为三态 True / False / None。"""
    raw = extract_json_object(text)
    if raw is None:
        return None

    fulfilled = raw.get("fulfilled")
    if fulfilled is True:
        return True
    if fulfilled is False:
        return False
    if fulfilled is None:
        return None

    s = str(fulfilled).strip().lower()
    if s in {"true", "yes", "y", "1", "满足", "是", "已实现", "实现", "完成"}:
        return True
    if s in {"false", "no", "n", "0", "不满足", "否", "未实现", "没实现", "未完成"}:
        return False
    return None


__all__ = [
    "build_decision_prompt",
    "build_agent_decision_prompt",
    "build_verify_prompt",
    "extract_json_object",
    "parse_decision_response",
    "parse_verify_response",
]
