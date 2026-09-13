"""QQ 渠道：通过 OneBot v11 协议连接 NapCat 等 QQ 机器人框架。

NapCat 配置为「反向 WebSocket」（本应用作为客户端连入），
QQ 消息通过 WebSocket 收发，无需公网/域名/备案。
"""
from __future__ import annotations

import json
import threading
import time
from typing import Callable

from config.settings import Settings

from .base import Channel, Contact, IncomingMessage, MSG_TYPE_IMAGE, MSG_TYPE_TEXT


class QQOneBotChannel(Channel):
    def __init__(self, settings: Settings):
        self.url = settings.qq_onebot_url  # 如 ws://127.0.0.1:3001
        self._ws = None

    def _send_payload(self, payload: dict) -> bool:
        import websocket  # 延迟导入

        try:
            ws = websocket.create_connection(self.url, timeout=10)
            ws.send(json.dumps(payload, ensure_ascii=False))
            ws.settimeout(10)
            ws.recv()  # 读响应
            ws.close()
            return True
        except Exception as e:
            print(f"[qq] 发送消息失败：{type(e).__name__}: {e}")
            return False

    @staticmethod
    def _split_text(text: str, limit: int = 2000) -> list:
        """把长文本按换行/长度切分，避免单条消息过长导致 QQ 渲染失败。"""
        if len(text) <= limit:
            return [text]
        chunks = []
        rest = text
        while rest:
            if len(rest) <= limit:
                chunks.append(rest)
                break
            cut = rest.rfind("\n", 0, limit)
            if cut <= 0:
                cut = limit
            chunks.append(rest[:cut])
            rest = rest[cut:].lstrip("\n")
        return chunks

    def send_text(self, receiver: str, text: str) -> bool:
        ok = True
        for chunk in self._split_text(text):
            payload = {
                "action": "send_msg",
                "params": {
                    "message_type": "private",
                    "user_id": int(receiver),
                    "message": [{"type": "text", "data": {"text": chunk}}],
                },
            }
            if not self._send_payload(payload):
                ok = False
        return ok

    def get_contacts(self) -> list[Contact]:
        return []  # QQ 好友列表需额外 API，MVP 暂不实现

    @staticmethod
    def _extract_text(event: dict) -> str:
        parts = []
        for seg in event.get("message", []):
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append(seg.get("data", {}).get("text", ""))
        return "".join(parts)

    @staticmethod
    def _fetch_image_data_url(seg_data: dict) -> str | None:
        """把 OneBot 的 image 段转成 data:image/...;base64,...（供多模态模型直接看图）。

        优先按 url 下载；失败则退回本地 file 路径。超过 1.5MB 用 Pillow 压到 1600px。
        """
        import base64
        import io
        import mimetypes
        import os
        import urllib.request

        url = str(seg_data.get("url") or "").strip()
        path = str(seg_data.get("file") or "").strip()
        raw = None

        if url.startswith(("http://", "https://")):
            try:
                with urllib.request.urlopen(url, timeout=15) as resp:
                    raw = resp.read()
            except Exception as e:  # noqa: BLE001
                print(f"[qq] 下载图片失败：{type(e).__name__}: {e}")

        if raw is None and path:
            p = path[7:] if path.startswith("file://") else path
            try:
                if os.path.isfile(p):
                    with open(p, "rb") as f:
                        raw = f.read()
            except Exception as e:  # noqa: BLE001
                print(f"[qq] 读取本地图片失败：{type(e).__name__}: {e}")

        if not raw:
            return None

        mime = mimetypes.guess_type(url or path)[0] or "image/png"

        if len(raw) > 1_500_000:
            try:
                from PIL import Image

                img = Image.open(io.BytesIO(raw))
                img.thumbnail((1600, 1600))
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=85)
                raw = buf.getvalue()
                mime = "image/jpeg"
            except Exception as e:  # noqa: BLE001
                print(f"[qq] 图片压缩失败（用原图）：{type(e).__name__}: {e}")

        return f"data:{mime};base64," + base64.b64encode(raw).decode()

    def _extract_images(self, event: dict) -> list:
        """提取消息里的图片段，转成 data URL 列表（失败的跳过）。"""
        out = []
        for seg in event.get("message", []):
            if not isinstance(seg, dict) or seg.get("type") != "image":
                continue
            data_url = self._fetch_image_data_url(seg.get("data") or {})
            if data_url:
                out.append(data_url)
        return out

    def run(self, on_message: Callable[[IncomingMessage], None]) -> None:
        import websocket  # 延迟导入

        while True:
            try:
                ws = websocket.create_connection(self.url, timeout=60)
                self._ws = ws
                while True:
                    data = ws.recv()
                    if not data:
                        continue
                    event = json.loads(data)
                    if event.get("post_type") != "message":
                        continue
                    if event.get("message_type") != "private":  # MVP 只处理私聊
                        continue
                    text = self._extract_text(event)
                    images = self._extract_images(event)
                    if not text and not images:
                        continue
                    sender = str(event.get("user_id", ""))
                    msg_type = MSG_TYPE_TEXT if text else MSG_TYPE_IMAGE
                    threading.Thread(
                        target=on_message,
                        args=(
                            IncomingMessage(
                                sender_wxid=sender,
                                content=text,
                                msg_type=msg_type,
                                images=images,
                            ),
                        ),
                        daemon=True,
                    ).start()
            except Exception:
                time.sleep(3)  # 断线重连
