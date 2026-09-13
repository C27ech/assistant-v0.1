"""渠道抽象：定义统一的收/发消息接口，方便替换不同微信接入方式。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable

# 微信消息类型常量
MSG_TYPE_TEXT = 1
MSG_TYPE_IMAGE = 3
MSG_TYPE_VOICE = 34
MSG_TYPE_FRIEND_REQ = 37
MSG_TYPE_CARD = 42
MSG_TYPE_VIDEO = 43
MSG_TYPE_EMOJI = 47
MSG_TYPE_LINK = 49
MSG_TYPE_SYSTEM = 10000


@dataclass
class IncomingMessage:
    sender_wxid: str
    content: str
    msg_type: int
    is_group: bool = False
    roomid: str = ""
    is_self: bool = False
    raw: object = None
    images: list = field(default_factory=list)  # 图片 data URL 列表（供多模态模型看图）


@dataclass
class Contact:
    wxid: str
    remark: str
    name: str


class Channel(ABC):
    """微信渠道抽象。接入不同微信方式时，实现本接口即可。"""

    @abstractmethod
    def send_text(self, receiver: str, text: str) -> bool:
        """发送文本给 receiver（wxid），返回是否成功。"""

    @abstractmethod
    def get_contacts(self) -> list[Contact]:
        """获取联系人（含备注），用于建立 wxid -> 备注 的映射。"""

    @abstractmethod
    def run(self, on_message: Callable[[IncomingMessage], None]) -> None:
        """阻塞运行，循环接收消息并回调 on_message。"""
