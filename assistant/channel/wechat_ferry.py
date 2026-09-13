"""基于 WeChatFerry(wcferry) 的个人微信渠道。"""
from __future__ import annotations

import time
from typing import Callable

from config.settings import Settings

from .base import (
    Channel,
    Contact,
    IncomingMessage,
    MSG_TYPE_TEXT,
    MSG_TYPE_SYSTEM,
)


class WeChatFerryChannel(Channel):
    """个人微信渠道。

    注意：
    - wcferry 仅支持 Windows，且需本地已登录指定版本的微信 PC 客户端（3.9.x）。
    - 需关闭微信自动更新，否则微信升级会导致 hook 失效。
    - 个人微信自动化存在封号风险，建议使用小号。
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._wcf = None

    def _ensure(self):
        if self._wcf is None:
            try:
                from wcferry import Wcf  # 延迟导入：非 Windows / 未安装时不阻塞启动
            except ImportError as e:
                raise RuntimeError(
                    "缺少 wcferry 依赖，请先在 Windows 上执行 `pip install wcferry` 并登录微信 PC 客户端"
                ) from e

            self._wcf = Wcf(
                host=self.settings.wcf_host,
                port=self.settings.wcf_port,
                debug=self.settings.wcf_debug,
                block=self.settings.wcf_block,
            )
            self._wcf.enable_receiving_msg()
        return self._wcf

    def send_text(self, receiver: str, text: str) -> bool:
        try:
            return self._ensure().send_text(text, receiver) == 0
        except Exception:
            return False

    def get_contacts(self) -> list[Contact]:
        try:
            raw = self._ensure().get_contacts()
        except Exception:
            return []
        result = []
        for c in raw or []:
            result.append(
                Contact(
                    wxid=getattr(c, "wxid", "") or "",
                    remark=getattr(c, "remark", "") or "",
                    name=getattr(c, "name", "") or "",
                )
            )
        return result

    def run(self, on_message: Callable[[IncomingMessage], None]) -> None:
        wcf = self._ensure()
        while True:
            try:
                msgs = wcf.get_msg()
            except Exception:
                time.sleep(1)
                continue

            for m in msgs or []:
                msg_type = int(getattr(m, "type", 0) or 0)
                # MVP 只处理文本消息，跳过系统消息
                if msg_type == MSG_TYPE_SYSTEM or msg_type != MSG_TYPE_TEXT:
                    continue

                on_message(
                    IncomingMessage(
                        sender_wxid=getattr(m, "sender", "") or "",
                        content=getattr(m, "content", "") or "",
                        msg_type=msg_type,
                        is_group=bool(getattr(m, "is_group", False)),
                        roomid=getattr(m, "roomid", "") or "",
                        is_self=bool(getattr(m, "is_self", False)),
                        raw=m,
                    )
                )

            time.sleep(0.3)
