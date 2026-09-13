"""视觉客户端（deepseek 主路径，豆包 Ark 兼容）：多模态决策 + 预期验证。

对外接口：
    - multimodal_decide(screenshot_path, task, model_tier="pro") -> dict
        输出契约：
        {
            "status": "确定" | "不确定",
            "expectation": str | None,
            "candidates": [{"name", "x", "y", "confidence"}],
            "reason": str,
        }
        x/y 为 0~1000 整数；「不确定」时 candidates 必为空、expectation 必为 None。

    - verify_expectation(screenshot_path, expectation) -> True | False | None
        三态：True=明确实现，False=明确反证，None=证据不足。

实现要点：
    - API Key 解析复用 core.config.resolve_vision_api_key（provider 自动切换）；
    - endpoint = {base_url}/chat/completions，POST，Bearer 鉴权，OpenAI ChatCompletions 兼容；
    - deepseek 请求 content 为数组：[{"type":"text",...},{"type":"image_url",...}]；
    - 图片采样统一短边 640、JPEG 质量 80；
    - 超时 (connect=15, read=180) 秒（可被 config.json 覆盖），使用 requests 库；
    - 响应 content 兼容 str 与 list 两种形态。
"""

from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import requests

from .config import load_config, resolve_vision_api_key

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_CONNECT_TIMEOUT = 15
DEFAULT_READ_TIMEOUT = 180
DEFAULT_MODEL_TIER = "pro"
DEFAULT_IMAGE_SHORT_SIDE = 640
DEFAULT_JPEG_QUALITY = 80

# deepseek v4.1 无档位分级：为兼容升级/档位选择逻辑，三档统一指向同一视觉模型。
DEFAULT_MODEL_TIERS = {
    "mini": "deepseek-v4.1-flash-expires-on-0910",
    "turbo": "deepseek-v4.1-flash-expires-on-0910",
    "pro": "deepseek-v4.1-flash-expires-on-0910",
}

# 档位别名归一化：min -> mini, t/fast -> turbo, p/precise -> pro。
_TIER_ALIASES = {
    "min": "mini",
    "mini": "mini",
    "t": "turbo",
    "fast": "turbo",
    "turbo": "turbo",
    "p": "pro",
    "precise": "pro",
    "pro": "pro",
}

_MULTIMODAL_DECIDE_PROMPT = """你是 Windows 桌面自动化控制器。请观察屏幕截图，根据任务判断下一步要执行的单个动作。

任务：{task}

必须只输出一个 JSON 对象（不要 Markdown 代码块、不要任何额外文字），格式如下：
{{"status":"确定|不确定","expectation":null,"candidates":[{{"name":"控件或区域名称","x":500,"y":500,"confidence":0.9}}],"reason":"简短理由"}}

规则：
1. 坐标采用 0~1000 千分比归一化：整张截图左上角为 (0,0)，右下角为 (1000,1000)；x/y 必须为 0~1000 的整数。
2. 若能明确判断下一步的点击/移动/滚动/输入目标，status 填 "确定"：
   - expectation 写一条可证伪的预期（例如「点击后会出现登录窗口」）；
   - candidates 给出 1~3 个候选目标，confidence 在 0~1 之间。
3. 若看不清、找不到目标、存在歧义或需要更多信息，status 填 "不确定"：
   - 此时 expectation 必须为 null，candidates 必须为 []。
4. 不要输出除该 JSON 之外的任何内容。"""

_VERIFY_PROMPT = """你是 Windows 桌面自动化验证器。请根据当前截图，判断下面这条预期是否已经实现。

预期：{expectation}

必须只输出一个 JSON 对象（不要 Markdown 代码块、不要任何额外文字）：
{{"fulfilled":true,"reason":"简短理由"}}

fulfilled 取值规则：
- true：截图明确显示预期已经实现；
- false：截图明确显示预期未实现（出现了明确的反证）；
- null：证据不足，无法判定（此时 reason 说明缺什么证据）。

只输出 JSON。"""


def _read_vision_config(cfg: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """返回 (完整配置, vision 子配置)。cfg 为 None 时自动加载。"""
    loaded = cfg if cfg is not None else load_config()
    vision = loaded.get("vision")
    if not isinstance(vision, dict):
        vision = {}
    return loaded, vision


def _resolve_model_name(
    model_tier: str,
    cfg: Optional[Dict[str, Any]] = None,
    vision: Optional[Dict[str, Any]] = None,
) -> str:
    """按档位解析视觉模型名，支持别名；缺省档位 pro。"""
    tier = _TIER_ALIASES.get(str(model_tier).strip().lower(), DEFAULT_MODEL_TIER)

    if vision is None:
        _, vision = _read_vision_config(cfg)

    tiers = vision.get("model_tiers") or DEFAULT_MODEL_TIERS
    if cfg:
        top_tiers = cfg.get("model_tiers")
        if isinstance(top_tiers, dict):
            tiers = top_tiers

    if isinstance(tiers, dict) and tier in tiers:
        return str(tiers[tier])

    return DEFAULT_MODEL_TIERS.get(tier, DEFAULT_MODEL_TIERS[DEFAULT_MODEL_TIER])


def _resolve_timeouts(vision: Dict[str, Any]) -> Tuple[int, int]:
    connect = vision.get("connect_timeout", DEFAULT_CONNECT_TIMEOUT)
    read = vision.get("request_timeout", DEFAULT_READ_TIMEOUT)
    try:
        connect = int(connect)
    except (TypeError, ValueError):
        connect = DEFAULT_CONNECT_TIMEOUT
    try:
        read = int(read)
    except (TypeError, ValueError):
        read = DEFAULT_READ_TIMEOUT
    return connect, read


def _sample_image_bytes(
    image_path: Union[str, Path],
    short_side: int = DEFAULT_IMAGE_SHORT_SIDE,
    jpeg_quality: int = DEFAULT_JPEG_QUALITY,
) -> bytes:
    """加载图片 -> RGB -> 短边采样（仅缩小，不放大）-> JPEG 编码为 bytes。"""
    from PIL import Image

    with Image.open(image_path) as img:
        image = img.convert("RGB")
        width, height = image.size
        min_side = min(width, height)
        if min_side > short_side > 0:
            scale = short_side / float(min_side)
            new_width = max(1, int(round(width * scale)))
            new_height = max(1, int(round(height * scale)))
            image = image.resize((new_width, new_height), Image.LANCZOS)

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=jpeg_quality)
        return buffer.getvalue()


def _image_data_uri(image_bytes: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode("ascii")


def _content_to_text(content: Any) -> str:
    """把 OpenAI 响应 content 统一为字符串（兼容 str 与 list 两种形态）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = item.get("type")
                if item_type == "text":
                    parts.append(str(item.get("text", "")))
                # image_url 等非文本片段跳过。
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出中稳健地提取第一个 JSON 对象。

    依次尝试：直接解析 -> 剥离 Markdown 代码围栏 -> 截取首个 '{' 到末个 '}' 再解析。
    """
    if not text:
        return None

    cleaned = text.strip()

    # 去除 ```json ... ``` 围栏。
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()

    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            data = json.loads(cleaned[start:end + 1])
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return None


def _post_chat(
    image_path: Union[str, Path],
    prompt: str,
    model: str,
    api_key: str,
    cfg: Optional[Dict[str, Any]] = None,
    vision: Optional[Dict[str, Any]] = None,
) -> str:
    """发送一次非流式 ChatCompletions 请求，返回文本化 content。"""
    if vision is None:
        _, vision = _read_vision_config(cfg)

    base_url = str(vision.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    endpoint = f"{base_url}/chat/completions"

    image_short_side = int(
        (vision.get("image_sample") or {}).get("short_side", DEFAULT_IMAGE_SHORT_SIDE)
        if isinstance(vision.get("image_sample"), dict)
        else DEFAULT_IMAGE_SHORT_SIDE
    )
    jpeg_quality = int(
        (vision.get("image_sample") or {}).get("jpeg_quality", DEFAULT_JPEG_QUALITY)
        if isinstance(vision.get("image_sample"), dict)
        else DEFAULT_JPEG_QUALITY
    )

    image_bytes = _sample_image_bytes(image_path, image_short_side, jpeg_quality)
    data_uri = _image_data_uri(image_bytes)

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    connect_timeout, read_timeout = _resolve_timeouts(vision)

    response = requests.post(
        endpoint,
        headers=headers,
        json=payload,
        timeout=(connect_timeout, read_timeout),
    )
    response.raise_for_status()

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return _content_to_text(message.get("content", ""))


def _normalize_decide_result(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把模型 JSON 规整为多模态决策输出契约。"""
    empty: Dict[str, Any] = {
        "status": "不确定",
        "expectation": None,
        "candidates": [],
        "reason": "",
    }

    if not raw:
        return {**empty, "reason": "模型未返回可解析的 JSON 决策"}

    status = str(raw.get("status", "")).strip()
    reason = str(raw.get("reason", "")).strip()
    expectation = raw.get("expectation")

    # 非「确定」一律按不确定处理。
    if status != "确定":
        return {
            "status": "不确定",
            "expectation": None,
            "candidates": [],
            "reason": reason or "模型判定不确定",
        }

    candidates: List[Dict[str, Any]] = []
    raw_candidates = raw.get("candidates")
    if isinstance(raw_candidates, list):
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            try:
                x = int(round(float(item.get("x"))))
                y = int(round(float(item.get("y"))))
                confidence = float(item.get("confidence"))
            except (TypeError, ValueError):
                continue

            x = max(0, min(1000, x))
            y = max(0, min(1000, y))
            confidence = max(0.0, min(1.0, confidence))

            name = str(item.get("name", "")).strip() or "未命名目标"
            candidates.append(
                {"name": name, "x": x, "y": y, "confidence": confidence}
            )

    if not candidates:
        return {
            "status": "不确定",
            "expectation": None,
            "candidates": [],
            "reason": reason or "模型判定确定，但未给出有效候选坐标",
        }

    expectation_text = expectation if isinstance(expectation, str) else None
    if expectation_text is not None and not expectation_text.strip():
        expectation_text = None

    return {
        "status": "确定",
        "expectation": expectation_text,
        "candidates": candidates,
        "reason": reason,
    }


def multimodal_decide(
    screenshot_path: Union[str, Path],
    task: str,
    model_tier: str = DEFAULT_MODEL_TIER,
    cfg: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """视觉多模态决策：根据截图与任务输出下一步动作。

    :param screenshot_path: 截图 PNG 路径。
    :param task: 自然语言任务描述。
    :param model_tier: 模型档位 mini/turbo/pro（支持别名），缺省 pro。
    :param cfg: 已加载配置；None 时自动加载。
    :returns: 决策契约 dict（见模块 docstring）。所有失败路径均返回「不确定」，
              并通过 reason 与可选 error 字段描述原因，保证调用方拿到稳定结构。
    """
    empty_reason = "多模态决策未执行"

    loaded, vision = _read_vision_config(cfg)
    api_key = resolve_vision_api_key(loaded)
    if not api_key:
        return {
            "status": "不确定",
            "expectation": None,
            "candidates": [],
            "reason": "缺少视觉 API Key（已按 env -> secrets.json -> config.json 三级解析均失败）",
            "error": "missing_vision_api_key",
        }

    model = _resolve_model_name(model_tier, loaded, vision)
    prompt = _MULTIMODAL_DECIDE_PROMPT.format(task=task)

    try:
        text = _post_chat(screenshot_path, prompt, model, api_key, loaded, vision)
        raw = _extract_json_object(text)
        return _normalize_decide_result(raw)
    except requests.exceptions.Timeout:
        empty_reason = "视觉请求超时"
    except requests.exceptions.RequestException as exc:
        empty_reason = f"视觉请求失败: {exc}"
    except Exception as exc:  # noqa: BLE001 - 兜底保证结构稳定。
        empty_reason = f"多模态决策异常: {exc}"

    return {
        "status": "不确定",
        "expectation": None,
        "candidates": [],
        "reason": empty_reason,
        "error": empty_reason,
    }


def verify_expectation(
    screenshot_path: Union[str, Path],
    expectation: Optional[str],
    model_tier: str = DEFAULT_MODEL_TIER,
    cfg: Optional[Dict[str, Any]] = None,
) -> Union[bool, None]:
    """视觉验证预期是否实现，返回三态 True/False/None。

    :param expectation: 待验证的预期；为空时直接返回 None（证据不足）。
    :returns: True=明确实现，False=明确反证，None=证据不足/验证失败。
    """
    if not expectation or not str(expectation).strip():
        return None

    loaded, vision = _read_vision_config(cfg)
    api_key = resolve_vision_api_key(loaded)
    if not api_key:
        return None

    model = _resolve_model_name(model_tier, loaded, vision)
    prompt = _VERIFY_PROMPT.format(expectation=str(expectation).strip())

    try:
        text = _post_chat(screenshot_path, prompt, model, api_key, loaded, vision)
        raw = _extract_json_object(text)
        if not raw:
            return None

        fulfilled = raw.get("fulfilled")
        if fulfilled is True:
            return True
        if fulfilled is False:
            return False
        if fulfilled is None:
            return None

        # 兼容字符串输出。
        normalized = str(fulfilled).strip().lower()
        if normalized in {"true", "yes", "满足", "是"}:
            return True
        if normalized in {"false", "no", "不满足", "否"}:
            return False
        return None
    except Exception:  # noqa: BLE001 - 验证失败一律视为证据不足。
        return None



# ---------------------------------------------------------------------------
# 文本语义路由（AI 路由器复用同一 deepseek 调用链）
# ---------------------------------------------------------------------------

def chat_text(
    prompt: str,
    model_tier: str = DEFAULT_MODEL_TIER,
    model: Optional[str] = None,
    cfg: Optional[Dict[str, Any]] = None,
) -> str:
    """发送一次纯文本 ChatCompletions 请求，返回文本化 content。

    与视觉调用共用同一套 config/API Key/endpoint/超时解析逻辑；仅消息内容
    不含图片。供 router.ai_router 做语义路由判断使用。

    :param prompt: 用户消息文本。
    :param model_tier: 模型档位 mini/turbo/pro（支持别名），缺省 pro。
    :param model: 显式指定模型名；为 None 时按 model_tier 解析。
    :param cfg: 已加载配置；None 时自动加载。
    :returns: 模型文本输出。
    :raises RuntimeError: 缺少 API Key。
    """
    loaded, vision = _read_vision_config(cfg)
    api_key = resolve_vision_api_key(loaded)
    if not api_key:
        raise RuntimeError(
            "缺少视觉 API Key（已按 env -> secrets.json -> config.json 三级解析均失败）"
        )

    resolved_model = model or _resolve_model_name(model_tier, loaded, vision)
    base_url = str(vision.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    endpoint = f"{base_url}/chat/completions"

    payload = {
        "model": resolved_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是 Windows 桌面任务路由器。你只输出一个结构化 JSON 对象，"
                    "不要输出 Markdown 代码块，也不要输出任何额外文字。"
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    connect_timeout, read_timeout = _resolve_timeouts(vision)

    response = requests.post(
        endpoint,
        headers=headers,
        json=payload,
        timeout=(connect_timeout, read_timeout),
    )
    response.raise_for_status()

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return _content_to_text(message.get("content", ""))


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """从模型输出中稳健地提取第一个 JSON 对象（对外公开封装）。"""
    return _extract_json_object(text)

__all__ = [
    "multimodal_decide",
    "verify_expectation",
    "chat_text",
    "extract_json_object",
    "DEFAULT_MODEL_TIERS",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL_TIER",
]


# ---------------------------------------------------------------------------
# 多消息视觉调用（有状态 agent loop 使用）：支持一条消息里多张图片与多段文本。
# ---------------------------------------------------------------------------

def _coerce_message_content(
    message: Dict[str, Any],
    vision: Dict[str, Any],
) -> Dict[str, Any]:
    """把 executor 传进来的宽松 message 规整为 OpenAI ChatCompletions 结构。

    支持：
    - content 为 str：直接作为文本；
    - content 为 list：每项可为 {"type":"text","text":...} 或
      {"type":"image_url","image_url":{"url": <path | data URI>}}。
      图片 url 为本地路径时，会按 vision 配置采样为 data URI。
    """
    role = str(message.get("role") or "user")
    content = message.get("content", "")

    if isinstance(content, str):
        return {"role": role, "content": content}

    if not isinstance(content, list):
        return {"role": role, "content": str(content)}

    image_short_side = DEFAULT_IMAGE_SHORT_SIDE
    jpeg_quality = DEFAULT_JPEG_QUALITY
    image_sample = vision.get("image_sample")
    if isinstance(image_sample, dict):
        try:
            image_short_side = int(image_sample.get("short_side", DEFAULT_IMAGE_SHORT_SIDE))
        except (TypeError, ValueError):
            image_short_side = DEFAULT_IMAGE_SHORT_SIDE
        try:
            jpeg_quality = int(image_sample.get("jpeg_quality", DEFAULT_JPEG_QUALITY))
        except (TypeError, ValueError):
            jpeg_quality = DEFAULT_JPEG_QUALITY

    parts: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            parts.append({"type": "text", "text": str(part)})
            continue

        part_type = part.get("type")
        if part_type == "text":
            parts.append({"type": "text", "text": str(part.get("text", ""))})
            continue

        if part_type == "image_url":
            image_url = part.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url")
            else:
                url = image_url

            if isinstance(url, str) and url.startswith("data:"):
                parts.append(
                    {"type": "image_url", "image_url": {"url": url}}
                )
                continue

            if isinstance(url, str) and url.strip():
                try:
                    image_bytes = _sample_image_bytes(
                        url.strip(), image_short_side, jpeg_quality
                    )
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": _image_data_uri(image_bytes)},
                        }
                    )
                except Exception:  # noqa: BLE001 - 单张坏图不阻断整轮。
                    parts.append(
                        {
                            "type": "text",
                            "text": f"[图片无法读取，已忽略: {url}]",
                        }
                    )
            continue

        # 未知类型：降级为文本，避免直接丢掉信息。
        parts.append({"type": "text", "text": str(part.get("text", part))})

    return {"role": role, "content": parts}


def post_chat_messages(
    messages: List[Dict[str, Any]],
    model: str,
    api_key: str,
    cfg: Optional[Dict[str, Any]] = None,
    vision: Optional[Dict[str, Any]] = None,
) -> str:
    """发送一次多消息（可含多图）的非流式 ChatCompletions 请求。

    ``messages`` 中的 content 可为 str 或 list（见 ``_coerce_message_content``）。
    与单图 ``_post_chat`` 共用同一套 config / endpoint / 超时 / 采样逻辑，
    仅消息构造更灵活，供有状态 agent loop 注入多张关键帧。
    """
    if vision is None:
        _, vision = _read_vision_config(cfg)

    base_url = str(vision.get("base_url") or DEFAULT_BASE_URL).rstrip("/")
    endpoint = f"{base_url}/chat/completions"

    payload = {
        "model": model,
        "messages": [_coerce_message_content(m, vision) for m in messages],
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    connect_timeout, read_timeout = _resolve_timeouts(vision)

    response = requests.post(
        endpoint,
        headers=headers,
        json=payload,
        timeout=(connect_timeout, read_timeout),
    )
    response.raise_for_status()

    data = response.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return _content_to_text(message.get("content", ""))
