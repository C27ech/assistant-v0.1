"""企业微信加解密冒烟测试：加解密自洽 + 签名校验。

运行：`python tests/test_wecom_crypto.py`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from channel.wecom_crypto import WXBizMsgCrypt  # noqa: E402

# 43 字符的合法 EncodingAESKey
AES_KEY = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFG"
TOKEN = "test_token"
RECEIVER_ID = "ww1234567890"


def test_roundtrip() -> None:
    crypt = WXBizMsgCrypt(TOKEN, AES_KEY, RECEIVER_ID)

    msg = "<xml><ToUserName><![CDATA[ww123]]></ToUserName><Content><![CDATA[你好]]></Content></xml>"
    timestamp = "1700000000"
    nonce = "abcdef"

    encrypt = crypt.encrypt(msg)
    sig = crypt._signature(timestamp, nonce, encrypt)

    plain = crypt.decrypt(encrypt, sig, timestamp, nonce)
    assert plain == msg, f"解密结果不一致: {plain!r}"

    # 错误签名应返回 None
    assert crypt.decrypt(encrypt, "bad_signature", timestamp, nonce) is None

    # verify_url 也应能解出原文
    assert crypt.verify_url(sig, timestamp, nonce, encrypt) == msg

    print("wecom crypto roundtrip OK")


if __name__ == "__main__":
    test_roundtrip()
