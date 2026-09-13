"""企业微信渠道：自建应用 + 回调，实现双向收发。

消息流：
- 用户在企业微信里给应用发消息 → 企业微信服务器 POST 到回调 URL
- 本渠道解密消息 → 异步回调 on_message → 通过 API 主动回发

注意：回调 URL 需要公网可达（内网穿透或云服务器）。
"""
from __future__ import annotations

import threading
import time
import xml.etree.ElementTree as ET
from typing import Callable, Optional

import requests

from config.settings import Settings

from .base import Channel, Contact, IncomingMessage, MSG_TYPE_TEXT
from .wecom_crypto import WXBizMsgCrypt

API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"


class WeComChannel(Channel):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.corp_id = settings.wecom_corp_id
        self.agent_id = settings.wecom_agent_id
        self.secret = settings.wecom_secret
        self.port = settings.wecom_port
        self.crypt = WXBizMsgCrypt(settings.wecom_token, settings.wecom_aes_key, self.corp_id)
        self._token: Optional[str] = None
        self._token_expire = 0.0

    # ---------- 企业微信 API ----------
    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expire - 60:
            return self._token
        resp = requests.get(
            f"{API_BASE}/gettoken",
            params={"corpid": self.corp_id, "corpsecret": self.secret},
            timeout=10,
        ).json()
        if resp.get("errcode", 0) != 0:
            raise RuntimeError(f"获取 access_token 失败: {resp}")
        self._token = resp["access_token"]
        self._token_expire = time.time() + resp.get("expires_in", 7200)
        return self._token

    def send_text(self, receiver: str, text: str) -> bool:
        try:
            token = self._access_token()
            payload = {
                "touser": receiver,
                "msgtype": "text",
                "agentid": self.agent_id,
                "text": {"content": text},
            }
            resp = requests.post(
                f"{API_BASE}/message/send",
                params={"access_token": token},
                json=payload,
                timeout=10,
            ).json()
            return resp.get("errcode", -1) == 0
        except Exception:
            return False

    def get_contacts(self) -> list[Contact]:
        try:
            token = self._access_token()
            resp = requests.get(
                f"{API_BASE}/user/list",
                params={"access_token": token, "department_id": 1, "fetch_child": 1},
                timeout=10,
            ).json()
            users = resp.get("userlist", [])
            return [
                Contact(wxid=u.get("userid", ""), remark=u.get("name", ""), name=u.get("name", ""))
                for u in users
            ]
        except Exception:
            return []

    # ---------- 回调 ----------
    def run(self, on_message: Callable[[IncomingMessage], None]) -> None:
        from flask import Flask, request  # 延迟导入，非 WeCom 模式不依赖 Flask

        app = Flask(__name__)

        @app.route("/callback", methods=["GET", "POST"])
        def callback():
            msg_signature = request.args.get("msg_signature", "")
            timestamp = request.args.get("timestamp", "")
            nonce = request.args.get("nonce", "")

            if request.method == "GET":
                echostr = request.args.get("echostr", "")
                plain = self.crypt.verify_url(msg_signature, timestamp, nonce, echostr)
                return plain if plain is not None else "invalid"

            # POST：解密后异步处理，立即返回 success（企业微信要求 5 秒内响应）
            body = request.data.decode("utf-8")
            encrypt = self._extract_encrypt(body)
            plain = self.crypt.decrypt(encrypt, msg_signature, timestamp, nonce)
            if plain is None:
                return "success"
            msg = self._parse_message(plain)
            if msg is not None:
                threading.Thread(target=on_message, args=(msg,), daemon=True).start()
            return "success"

        app.run(host="0.0.0.0", port=self.port)

    # ---------- XML ----------
    @staticmethod
    def _extract_encrypt(xml: str) -> str:
        root = ET.fromstring(xml)
        return root.findtext("Encrypt", "")

    @staticmethod
    def _parse_message(xml: str) -> Optional[IncomingMessage]:
        root = ET.fromstring(xml)
        msg_type = root.findtext("MsgType", "")
        if msg_type != "text":  # MVP 只处理文本
            return None
        sender = root.findtext("FromUserName", "")
        content = root.findtext("Content", "")
        return IncomingMessage(sender_wxid=sender, content=content, msg_type=MSG_TYPE_TEXT)
