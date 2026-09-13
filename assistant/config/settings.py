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


# 火山方舟（豆包）可选的模型清单，供助手AI 派活时选用
DOUBAO_MODELS = [
    "doubao-seed-2-1-turbo-260628",
    "doubao-seed-2-0-mini-260428",
    "doubao-seed-2-1-pro-260628",
]


@dataclass
class Settings:
    # --- DeepSeek ---
    deepseek_base_url: str = "https://api.deepseek.com"
    decision_model: str = "deepseek-v4-flash"
    monitor_model: str = "deepseek-v4-flash"
    coder_model: str = "deepseek-v4-flash"
    api_key_decision: Optional[str] = None
    api_key_monitor: Optional[str] = None
    api_key_coder: Optional[str] = None

    # --- 豆包（火山方舟 Ark）---
    doubao_api_key: str = ""
    doubao_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"

    # --- 微信（WeChatFerry）---
    wcf_host: Optional[str] = None
    wcf_port: int = 10086
    wcf_debug: bool = False
    wcf_block: bool = True

    # --- 渠道选择 ---
    channel: str = "wechat"  # wechat / wecom

    # --- 企业微信（自建应用 + 回调）---
    wecom_corp_id: str = ""
    wecom_agent_id: str = ""
    wecom_secret: str = ""
    wecom_token: str = ""
    wecom_aes_key: str = ""
    wecom_port: int = 8000

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
            decision_model=_env("DECISION_MODEL", "deepseek-v4-flash"),
            monitor_model=_env("MONITOR_MODEL", "deepseek-v4-flash"),
            coder_model=_env("CODER_MODEL", "deepseek-v4-flash"),
            api_key_decision=_env("DEEPSEEK_API_KEY_DECISION") or None,
            api_key_monitor=_env("DEEPSEEK_API_KEY_MONITOR") or None,
            api_key_coder=_env("DEEPSEEK_API_KEY_CODER") or None,
            doubao_api_key=_env("DOUBAO_API_KEY", ""),
            doubao_base_url=_env("DOUBAO_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
            wcf_host=_env("WCF_HOST") or None,
            wcf_port=int(_env("WCF_PORT", "10086") or "10086"),
            wcf_debug=_env_bool("WCF_DEBUG", False),
            wcf_block=_env_bool("WCF_BLOCK", True),
            channel=_env("CHANNEL", "wechat"),
            wecom_corp_id=_env("WECOM_CORP_ID"),
            wecom_agent_id=_env("WECOM_AGENT_ID"),
            wecom_secret=_env("WECOM_SECRET"),
            wecom_token=_env("WECOM_TOKEN"),
            wecom_aes_key=_env("WECOM_AES_KEY"),
            wecom_port=int(_env("WECOM_PORT", "8000") or "8000"),
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
        非空 → 只要任意一个标识（QQ 号 / 微信号 / 解析后的 user_id）在名单里就放行。
        """
        if not self.allowed_users:
            return True
        allow = {str(x).strip() for x in self.allowed_users if str(x).strip()}
        return any(str(i).strip() in allow for i in identifiers if i)

    def llm_credentials(self, model: str, role: str = "coder") -> tuple:
        """根据模型名返回 (api_key, base_url)。

        豆包模型（doubao-*）走火山方舟 Ark；其余走 DeepSeek，并按角色选 key。
        """
        if model.startswith("doubao"):
            return self.doubao_api_key, self.doubao_base_url
        if role == "decision":
            return self.api_key_decision or "", self.deepseek_base_url
        if role == "monitor":
            return self.api_key_monitor or "", self.deepseek_base_url
        return self.api_key_coder or "", self.deepseek_base_url
