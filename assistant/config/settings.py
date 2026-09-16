"""配置加载：从项目根目录的 .env 读取，提供默认值。"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    return value if value is not None and value != "" else default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_user_map(raw: str) -> Dict[str, str]:
    """解析 '备注:user_id,备注2:user_id2' 形式的映射。"""
    result: Dict[str, str] = {}
    if not raw:
        return result
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            remark, user_id = item.split(":", 1)
            result[remark.strip()] = user_id.strip()
        else:
            result[item] = item
    return result


def _parse_list(raw: str) -> list:
    """解析逗号/空格/分号分隔的列表，如 '123456789, 987654321'。"""
    if not raw:
        return []
    parts = re.split(r"[,，;；\s]+", raw.strip())
    return [p.strip() for p in parts if p.strip()]


# 模型名一律「通配」：代码不校验、不列举、不做前缀判断，你填什么就用什么。
#   "*"（或留空）= 通配：不指定具体型号 —— 请求里干脆不带 model 字段，
#                        由你的端点/网关用自己的默认模型；
#   其他任意字符串   = 具体型号，原样透传给端点。
ANY_MODEL = "*"

# 供应商路由（通配模式，逗号分隔）：模型名命中这些模式的走豆包（火山方舟 Ark），
# 其余（含通配）走 DEEPSEEK_*。想全走 DeepSeek 就把它留空。
DOUBAO_MODEL_PATTERNS = ["doubao-*"]


def is_wildcard(model: str) -> bool:
    """模型名是否通配（留空、或含 `*`）→ 表示不指定具体型号。"""
    return not model or "*" in model


def model_matches(pattern: str, model: str) -> bool:
    """把 pattern 当通配符（`*` 匹配任意串）跟模型名比对，忽略大小写。

    例：model_matches("doubao-*", "doubao-seed-2-1-pro") → True
    """
    if not pattern or not model:
        return False
    if pattern == "*":
        return True
    regex = re.escape(pattern).replace(r"\*", ".*")
    return re.fullmatch(regex, model, flags=re.IGNORECASE) is not None


@dataclass
class Settings:
    # --- DeepSeek（OpenAI 兼容端点，可换成任何兼容服务）---
    deepseek_base_url: str = "https://api.deepseek.com"
    decision_model: str = ANY_MODEL   # 通配：不指定型号，由端点决定
    monitor_model: str = ANY_MODEL
    coder_model: str = ANY_MODEL
    api_key_decision: Optional[str] = None
    api_key_monitor: Optional[str] = None
    api_key_coder: Optional[str] = None

    # --- 豆包（火山方舟 Ark）---
    doubao_api_key: str = ""
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    # 哪些模型名算「豆包」（通配模式）；空 = 不启用豆包路由
    doubao_model_patterns: list = field(default_factory=lambda: list(DOUBAO_MODEL_PATTERNS))

    # --- 渠道选择：只保留 QQ（OneBot / NapCat）---
    channel: str = "qq"

    # --- QQ（OneBot / NapCat）---
    qq_onebot_url: str = "ws://127.0.0.1:3001"

    # --- 桌面控制器（可选，供助手AI 调用）---
    desktop_controller_dir: str = ""

    # --- 用户识别（备注 -> 用户ID）---
    user_map: Dict[str, str] = field(default_factory=dict)

    # --- 使用者白名单：只有名单内的人能指挥助手；留空 = 不限制（任何人私聊都能使唤她）---
    allowed_users: list = field(default_factory=list)

    # --- 存储 ---
    db_path: str = "assistant.db"

    # --- 记忆（语义检索）---
    memory_retrieval_k: int = 30  # 每次对话召回的相关历史消息条数

    # --- 决策AI ---
    decision_max_iter: int = 200  # 每轮最多工具调用轮数（工具多了可调大）

    # --- 主动汇报策略 ---
    notify_on_start: bool = True
    notify_on_stuck: bool = True
    notify_on_done: bool = True

    @property
    def db_file(self) -> Path:
        return BASE_DIR / self.db_path

    @classmethod
    def load(cls) -> "Settings":
        load_dotenv(BASE_DIR / ".env")
        return cls(
            deepseek_base_url=_env("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            decision_model=_env("DECISION_MODEL", ANY_MODEL),
            monitor_model=_env("MONITOR_MODEL", ANY_MODEL),
            coder_model=_env("CODER_MODEL", ANY_MODEL),
            api_key_decision=_env("DEEPSEEK_API_KEY_DECISION") or None,
            api_key_monitor=_env("DEEPSEEK_API_KEY_MONITOR") or None,
            api_key_coder=_env("DEEPSEEK_API_KEY_CODER") or None,
            doubao_api_key=_env("DOUBAO_API_KEY", ""),
            doubao_base_url=_env("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            doubao_model_patterns=_parse_list(_env("DOUBAO_MODEL_PATTERNS")) or list(DOUBAO_MODEL_PATTERNS),
            channel=_env("CHANNEL", "qq"),
            qq_onebot_url=_env("QQ_ONEBOT_URL", "ws://127.0.0.1:3001"),
            desktop_controller_dir=_env("DESKTOP_CONTROLLER_DIR", "") or str(Path.home() / "Desktop" / "controller"),
            user_map=_parse_user_map(_env("USER_MAP")),
            allowed_users=_parse_list(_env("ALLOWED_USERS")),
            db_path=_env("DB_PATH", "assistant.db"),
            memory_retrieval_k=int(_env("MEMORY_RETRIEVAL_K", "30") or "30"),
            decision_max_iter=int(_env("DECISION_MAX_ITER", "200") or "200"),
            notify_on_start=_env_bool("NOTIFY_ON_START", True),
            notify_on_stuck=_env_bool("NOTIFY_ON_STUCK", True),
            notify_on_done=_env_bool("NOTIFY_ON_DONE", True),
        )

    def resolve_user(self, remark: str) -> str:
        """根据备注名解析用户ID；未配置时用备注本身作为用户ID。"""
        return self.user_map.get(remark, remark)

    def is_allowed(self, *identifiers: str) -> bool:
        """使用者白名单校验。

        白名单为空 → 不限制（返回 True，保持向后兼容）。
        非空 → 只要任意一个标识（QQ 号 / 解析后的 user_id）在名单里就放行。
        """
        if not self.allowed_users:
            return True
        allow = {str(x).strip() for x in self.allowed_users if str(x).strip()}
        return any(str(i).strip() in allow for i in identifiers if i)

    def llm_credentials(self, model: str, role: str = "coder") -> tuple:
        """根据模型名返回 (api_key, base_url)。

        模型名只用来「选端点」：命中 doubao_model_patterns（默认 `doubao-*`）走火山方舟 Ark，
        其余（含通配 `*` / 留空）走 DeepSeek，并按角色选 key。
        型号本身不做任何校验 —— 你填什么就透传什么，代码不限定具体模型。
        """
        for pattern in self.doubao_model_patterns or []:
            if model_matches(pattern, model):
                return self.doubao_api_key, self.doubao_base_url
        if role == "decision":
            return self.api_key_decision or "", self.deepseek_base_url
        if role == "monitor":
            return self.api_key_monitor or "", self.deepseek_base_url
        return self.api_key_coder or "", self.deepseek_base_url
