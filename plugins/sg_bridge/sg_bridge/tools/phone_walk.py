"""sg_bridge.tools.phone_walk — 自动逐封打开邮件并读出内容（OCR）

用途：找出"可回复"的邮件（正文界面会出现回复选项），并生成收件箱内容清单。

流程（全部用键盘，OCR 负责"读"）：
    1. Z 开手机 → ENTER 进收件箱
    2. 循环：ENTER 打开当前邮件 → OCR 记录正文 → ENTER 返回列表 → ↓ 下一封
    3. 把每封的 OCR 文本写入 UTF-8 日志

用法::

    python -m sg_bridge.tools.phone_walk --game SG --count 12
    python -m sg_bridge.tools.phone_walk --game SG --count 30 --out out/phone/inbox_dump.txt
"""
from __future__ import annotations

import difflib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "out", "phone"))

from .. import api                                   # noqa: E402
from ..core import ocr as OCR                         # noqa: E402

PANEL_CROP = "0.60,0.02,0.40,0.96"


def _read(game: str, tag: str) -> list[str]:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"walk_{tag}.png")
    res = api.call("screenshot", {"path": path, "target": game, "client_only": True})
    if not res.get("ok"):
        raise RuntimeError(f"截图失败: {res.get('error')}")
    return OCR.read_text(path, crop=PANEL_CROP, scale=2.2)["plain_lines"]


def _is_inbox(lines: list[str]) -> bool:
    return any(difflib.SequenceMatcher(None, "收件箱", s).ratio() > 0.5 for s in lines)


def _is_body(lines: list[str]) -> bool:
    return any("已接收短信" in s or difflib.SequenceMatcher(None, "已接收短信", s).ratio() > 0.55
               for s in lines)


def walk(game: str, count: int = 10, out: "str | None" = None) -> dict:
    api.call("attach", {"target": game, "focus": True})
    log = []

    def emit(text: str) -> None:
        print(text)
        log.append(text)

    # 确保在收件箱
    if not _is_inbox(_read(game, "probe0")):
        api.call("game_action", {"action": "phone", "game": game})     # Z
        time.sleep(1.2)
        if not _is_inbox(_read(game, "probe1")):
            api.call("game_action", {"action": "confirm", "game": game})  # ENTER 进信箱
            time.sleep(1.5)

    mails = []
    for i in range(count):
        api.call("game_action", {"action": "confirm", "game": game})   # 打开
        time.sleep(1.3)
        body = _read(game, f"body{i:02d}")
        if not _is_body(body):
            emit(f"[{i:>2}] （未进入正文，OCR：{body[:3]}）")
            break
        sender = ""
        for s in body:
            if "已接收短信" in s or "：" in s or ":" in s:
                continue
            if difflib.SequenceMatcher(None, s, "已接收短信").ratio() > 0.5:
                continue
            sender = sender or s
        text = " / ".join(s for s in body if s.strip())
        mails.append({"index": i, "sender": sender, "lines": body})
        emit(f"[{i:>2}] {text}")
        api.call("game_action", {"action": "confirm", "game": game})   # 关闭（返回列表）
        time.sleep(1.2)
        api.call("key_press", {"key": "down", "ms": 40})               # 下一封
        time.sleep(1.0)

    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(log))
        print(f"\n已写入 {out}")
    return {"count": len(mails), "mails": mails}


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    game, count, out = "SG", 10, os.path.join(OUT_DIR, "inbox_dump.txt")
    i = 0
    while i < len(argv):
        if argv[i] == "--game":
            game = argv[i + 1]; i += 2
        elif argv[i] == "--count":
            count = int(argv[i + 1]); i += 2
        elif argv[i] == "--out":
            out = argv[i + 1]; i += 2
        else:
            i += 1
    print(f"== 逐封打开邮件：{game} 共 {count} 封")
    walk(game, count, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
