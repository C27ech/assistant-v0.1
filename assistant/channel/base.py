"""渠道抽象：定义统一的收/发消息接口，方便替换不同接入方式。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

# 消息类型（沿用 OneBot 消息段语义）
MSG_TYPE_TEXT = 1
MSG_TYPE_IMAGE = 3


@dataclass
class IncomingMessage:
    sender_id: str          # 渠道内的用户标识（QQ 渠道即 QQ 号）
    content: str
    msg_type: int
    images: list = field(default_factory=list)  # 图片 data URL 列表（供多模态模型看图）


class Channel(ABC):
    """聊天渠道抽象。接入新渠道（如换一个机器人框架）时实现本接口即可。"""

    @abstractmethod
    def send_text(self, receiver: str, text: str) -> bool:
        """发送文本给 receiver（渠道内的用户标识），返回是否成功。"""

    @abstractmethod
    def run(self, on_message: Callable[[IncomingMessage], None]) -> None:
        """阻塞运行，循环接收消息并回调 on_message。"""
