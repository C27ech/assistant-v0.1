"""统一配置加载与视觉 API Key 解析（deepseek 主路径 + 豆包 Ark 兼容）。

职责：
1. 以 ``utf-8-sig`` 读取 JSON 配置文件，读取失败时容错返回空配置；
2. 递归移除所有以 ``_`` 开头的「注释键」；
3. 提供 ``resolve_vision_api_key``，按 provider 自动解析视觉 API Key：
   - deepseek（主路径）：环境变量 DEEPSEEK_API_KEY_DECISION / DEEPSEEK_API_KEY /
     DEEPSEEK_API_KEY_CODER / DEEPSEEK_API_KEY_MONITOR（真实 key 校验：``sk-`` 前缀
     且长度 >= 20） -> 项目根 secrets.json -> config.json 的 vision.vision_api_key；
   - ark（兼容路径，保留豆包逻辑）：环境变量 DOUBAO_API_KEY -> 兼容别名
     ARK_VISION_API_KEY / VISION_API_KEY / ARK_API_KEY -> secrets.json ->
     config.json 的 vision.vision_api_key（``ark-`` 前缀且长度 >= 20）；
4. ``resolve_ark_api_key`` 保留为兼容入口，内部委托 ``resolve_vision_api_key``，
   供 diag.py / smoke_test.py / vision_exec.executor 等旧调用方无痛切换。

provider 判定顺序：显式 provider 参数 -> config.json 的 vision.base_url ->
环境变量是否存在合法 deepseek key -> 默认 deepseek。

只使用标准库，禁止依赖老 controller。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Union

# 豆包 Ark 环境变量名（按优先级）。
_ARK_ENV_KEY_NAMES = (
    "DOUBAO_API_KEY",
    "ARK_VISION_API_KEY",
    "VISION_API_KEY",
    "ARK_API_KEY",
)

# deepseek 环境变量名（按优先级）：decision 专用 key 优先。
_DEEPSEEK_ENV_KEY_NAMES = (
    "DEEPSEEK_API_KEY_DECISION",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_API_KEY_CODER",
    "DEEPSEEK_API_KEY_MONITOR",
)

# secrets.json 中的候选字段。
_ARK_SECRET_KEY_FIELDS = ("vision_api_key", "doubao_api_key", "ark_api_key")
_DEEPSEEK_SECRET_KEY_FIELDS = (
    "deepseek_api_key_decision",
    "deepseek_api_key",
    "vision_api_key",
)

# 可选：环境变量覆盖 secrets.json 路径（兼容老实现，非必填）。
_SECRETS_FILE_ENV = "CONTROLLER_SECRETS_FILE"


def find_project_root() -> Path:
    """返回项目根目录（core 目录的上一级）。"""
    return Path(__file__).resolve().parent.parent


def default_config_path() -> Path:
    """返回默认配置文件路径：项目根目录 config.json。"""
    return find_project_root() / "config.json"


def _strip_underscore_keys(obj: Any) -> Any:
    """递归移除字典中所有以 ``_`` 开头的键（视为注释键，不参与运行时配置）。"""
    if isinstance(obj, dict):
        return {
            key: _strip_underscore_keys(value)
            for key, value in obj.items()
            if not (isinstance(key, str) and key.startswith("_"))
        }
    if isinstance(obj, list):
        return [_strip_underscore_keys(item) for item in obj]
    if isinstance(obj, tuple):
        return tuple(_strip_underscore_keys(item) for item in obj)
    return obj


def load_config(path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """加载 JSON 配置，容错返回 dict。

    :param path: 配置文件路径；缺省使用项目根 config.json。
    :returns: 解析后的配置字典。文件不存在/JSON 非法/读取异常时返回 ``{}``。
    """
    cfg_path = Path(path) if path else default_config_path()

    try:
        with open(cfg_path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    return _strip_underscore_keys(data)  # type: ignore[return-value]


def _looks_like_real_ark_key(value: Any) -> bool:
    """判断字符串是否形如真实豆包 Ark key：``ark-`` 前缀且长度 >= 20。"""
    return (
        isinstance(value, str)
        and value.startswith("ark-")
        and len(value) >= 20
    )


def _looks_like_real_deepseek_key(value: Any) -> bool:
    """判断字符串是否形如真实 deepseek key：``sk-`` 前缀且长度 >= 20。"""
    return (
        isinstance(value, str)
        and value.startswith("sk-")
        and len(value) >= 20
    )


def _first_real_key(
    values: Any,
    validator: Callable[[Any], bool],
) -> Optional[str]:
    """从（可能为 None/字符串/列表）的取值中返回第一个通过校验的字符串。"""
    if values is None:
        return None
    if isinstance(values, str):
        return values if validator(values) else None
    if isinstance(values, (list, tuple)):
        for item in values:
            if validator(item):
                return item
        return None
    return None


def _secrets_path(project_root: Optional[Path] = None) -> Optional[Path]:
    """解析 secrets.json 路径。

    优先使用环境变量 ``CONTROLLER_SECRETS_FILE`` 指定的路径；
    否则使用 ``<project_root>/secrets.json``。
    """
    env_path = os.environ.get(_SECRETS_FILE_ENV, "").strip()
    if env_path:
        return Path(env_path).expanduser()

    root = Path(project_root) if project_root else find_project_root()
    return root / "secrets.json"


def _load_secrets_key_value(
    project_root: Optional[Path],
    fields: tuple,
    validator: Callable[[Any], bool],
) -> Optional[str]:
    """从 secrets.json 的指定字段中读取第一个通过校验的 key。"""
    secrets_path = _secrets_path(project_root)
    try:
        with open(secrets_path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    for field in fields:
        key = _first_real_key(data.get(field), validator)
        if key:
            return key
    return None


def _resolve_provider(
    provider: Optional[str],
    cfg: Optional[Dict[str, Any]],
) -> str:
    """解析视觉 provider 名称：deepseek 或 ark。

    优先显式参数；否则看 config.json 的 vision.base_url；再否则看环境变量里
    是否存在合法 deepseek key；最后默认 deepseek（当前主路径）。
    """
    if provider is not None:
        p = str(provider).strip().lower()
        if p in ("deepseek", "ds", "deepseek-v4", "deepseek-v4.1"):
            return "deepseek"
        if p in ("ark", "doubao", "volces"):
            return "ark"
        return p

    loaded: Dict[str, Any] = cfg if cfg is not None else load_config()
    vision = loaded.get("vision")
    base_url = ""
    if isinstance(vision, dict):
        base_url = str(vision.get("base_url") or "").strip().lower()

    if "deepseek" in base_url:
        return "deepseek"
    if "ark" in base_url or "volces" in base_url:
        return "ark"

    # 环境变量兜底：有合法 deepseek key 则视为 deepseek。
    for env_name in _DEEPSEEK_ENV_KEY_NAMES:
        if _looks_like_real_deepseek_key(os.environ.get(env_name, "")):
            return "deepseek"

    return "deepseek"


def _resolve_deepseek_key(
    cfg: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path] = None,
) -> Optional[str]:
    """解析 deepseek 视觉 API Key（三级）。

    1. 环境变量：DEEPSEEK_API_KEY_DECISION -> DEEPSEEK_API_KEY ->
       DEEPSEEK_API_KEY_CODER -> DEEPSEEK_API_KEY_MONITOR（``sk-`` 前缀且长度 >= 20）；
    2. 项目根 secrets.json：deepseek_api_key_decision / deepseek_api_key / vision_api_key；
    3. config.json 的 vision.vision_api_key（``sk-`` 前缀校验）。
    """
    for env_name in _DEEPSEEK_ENV_KEY_NAMES:
        candidate = os.environ.get(env_name, "").strip()
        if _looks_like_real_deepseek_key(candidate):
            return candidate

    secret_key = _load_secrets_key_value(
        project_root, _DEEPSEEK_SECRET_KEY_FIELDS, _looks_like_real_deepseek_key
    )
    if secret_key:
        return secret_key

    loaded: Dict[str, Any] = cfg if cfg is not None else load_config()
    vision = loaded.get("vision")
    if isinstance(vision, dict):
        key = _first_real_key(
            vision.get("vision_api_key"), _looks_like_real_deepseek_key
        )
        if key:
            return key

    return None


def _resolve_ark_key(
    cfg: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path] = None,
) -> Optional[str]:
    """解析豆包 Ark 视觉 API Key（三级，保留原逻辑）。"""
    for env_name in _ARK_ENV_KEY_NAMES:
        candidate = os.environ.get(env_name, "").strip()
        if _looks_like_real_ark_key(candidate):
            return candidate

    secret_key = _load_secrets_key_value(
        project_root, _ARK_SECRET_KEY_FIELDS, _looks_like_real_ark_key
    )
    if secret_key:
        return secret_key

    loaded: Dict[str, Any] = cfg if cfg is not None else load_config()
    vision = loaded.get("vision")
    if isinstance(vision, dict):
        key = _first_real_key(
            vision.get("vision_api_key"), _looks_like_real_ark_key
        )
        if key:
            return key

    return None


def resolve_vision_api_key(
    cfg: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path] = None,
    provider: Optional[str] = None,
) -> Optional[str]:
    """按 provider 解析视觉 API Key（新主入口）。

    :param cfg: 已加载的配置字典；为 None 时自动调用 load_config()。
    :param project_root: 项目根目录；为 None 时自动推导。
    :param provider: "deepseek" 或 "ark"；为 None 时按 config/env 自动判定。
    :returns: 解析到的 API Key；无有效 key 时返回 None。
    """
    resolved_provider = _resolve_provider(provider, cfg)
    if resolved_provider == "deepseek":
        return _resolve_deepseek_key(cfg, project_root)
    if resolved_provider == "ark":
        return _resolve_ark_key(cfg, project_root)
    return None


def resolve_ark_api_key(
    cfg: Optional[Dict[str, Any]] = None,
    project_root: Optional[Path] = None,
) -> Optional[str]:
    """兼容入口：按当前 provider 解析视觉 API Key。

    历史代码（diag.py / smoke_test.py / vision_exec.executor 等）调用此函数；
    现在它会自动切换到 deepseek 主路径，无需调用方改动。

    :param cfg: 已加载的配置字典。
    :param project_root: 项目根目录。
    :returns: 解析到的 API Key。
    """
    return resolve_vision_api_key(cfg=cfg, project_root=project_root, provider=None)


__all__ = [
    "find_project_root",
    "default_config_path",
    "load_config",
    "resolve_vision_api_key",
    "resolve_ark_api_key",
]
