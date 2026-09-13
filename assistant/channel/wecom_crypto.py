"""企业微信回调消息加解密（WXBizMsgCrypt）。

实现企业微信自建应用回调的：签名校验、消息解密、消息加密。
算法遵循企业微信官方文档（AES-256-CBC + SHA1 签名）。
"""
from __future__ import annotations

import base64
import hashlib
import os
import struct
from typing import Optional

from Crypto.Cipher import AES


class WXBizMsgCrypt:
    def __init__(self, token: str, encoding_aes_key: str, receiver_id: str):
        if not token or not encoding_aes_key or not receiver_id:
            raise ValueError("token / EncodingAESKey / 企业ID 均不能为空")
        self.token = token
        self.receiver_id = receiver_id  # 企业ID corp_id
        # EncodingAESKey 为 43 字符，补一个 "=" 得到 32 字节密钥
        self.key = base64.b64decode(encoding_aes_key + "=")
        if len(self.key) != 32:
            raise ValueError("EncodingAESKey 非法，应为 43 字符")

    # ---- 签名 ----
    def _sha1(self, text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def _signature(self, timestamp: str, nonce: str, encrypt: str) -> str:
        tmp = "".join(sorted([self.token, timestamp, nonce, encrypt]))
        return self._sha1(tmp)

    def verify_signature(self, msg_signature: str, timestamp: str, nonce: str, encrypt: str) -> bool:
        return self._signature(timestamp, nonce, encrypt) == msg_signature

    # ---- 加解密 ----
    def _pkcs7_pad(self, data: bytes, block_size: int = 32) -> bytes:
        pad = block_size - (len(data) % block_size)
        return data + bytes([pad]) * pad

    def _pkcs7_unpad(self, data: bytes) -> bytes:
        return data[:-data[-1]]

    def encrypt(self, reply_msg: str) -> str:
        """加密明文消息，返回 base64 字符串。"""
        rand = os.urandom(16)
        text = reply_msg.encode("utf-8")
        buf = rand + struct.pack(">I", len(text)) + text + self.receiver_id.encode("utf-8")
        buf = self._pkcs7_pad(buf)
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
        return base64.b64encode(cipher.encrypt(buf)).decode("utf-8")

    def decrypt(self, encrypt: str, msg_signature: str, timestamp: str, nonce: str) -> Optional[str]:
        """解密回调消息，返回明文字符串；校验失败返回 None。"""
        if not self.verify_signature(msg_signature, timestamp, nonce, encrypt):
            return None
        cipher = AES.new(self.key, AES.MODE_CBC, self.key[:16])
        plain = self._pkcs7_unpad(cipher.decrypt(base64.b64decode(encrypt)))
        msg_len = struct.unpack(">I", plain[16:20])[0]
        msg = plain[20:20 + msg_len].decode("utf-8")
        receiver_id = plain[20 + msg_len:].decode("utf-8")
        if receiver_id != self.receiver_id:
            return None
        return msg

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> Optional[str]:
        """URL 验证（GET 请求）：校验并解密 echostr，返回明文。"""
        return self.decrypt(echostr, msg_signature, timestamp, nonce)
