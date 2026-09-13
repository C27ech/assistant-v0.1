"""sg_bridge.tools.phone_ocr — 用 OCR 驱动手机：读列表 → 定位 → 打开 → 读正文 → 回复

思路（OCR 提供"内容"，键盘提供"操作"）：
    1. Z 打开手机（必要时 ENTER 进入收件箱）
    2. OCR 读出邮件列表（每行有坐标与 y 值）→ 得到"第几封"
    3. 用 ↓×N + ENTER 精确打开目标邮件（手机是键盘操作的，鼠标点不动）
    4. OCR 读出邮件正文
    5. 在正文界面按 ENTER（若该邮件可回复）→ OCR 读出回复选项

用法::

    python -m sg_bridge.tools.phone_ocr --game SG --list
    python -m sg_bridge.tools.phone_ocr --game SG --open 真由理
    python -m sg_bridge.tools.phone_ocr --game SG --open 0        # 直接按行号（0 起）
    python -m sg_bridge.tools.phone_ocr --game SG --reply 真由理
"""
from __future__ import annotations

import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "out", "phone"))

from .. import api                                    # noqa: E402
from ..core import ocr as OCR                          # noqa: E402

PANEL_CROP = "0.62,0.05,0.38,0.90"      # 手机面板区域（比例）
PANEL_X0 = 0.62 * 1920


def _shot(tag: str, game: str) -> dict:
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"ocr_{tag}.png")
    res = api.call("screenshot", {"path": path, "target": game, "client_only": True})
    if not res.get("ok"):
        raise RuntimeError(f"截图失败: {res.get('error')}")
    return OCR.read_text(path, crop=PANEL_CROP, scale=2.0)


def list_rows(game: str) -> dict:
    """读出手机面板里的行（含 y 坐标），并标出邮件行序号。"""
    res = _shot("list", game)
    rows = []
    idx = -1
    for ln in res["lines"]:
        text = ln["text"]
        if "收件箱" in text:
            rows.append({"kind": "header", "index": None, "y": ln["bbox"][1], "text": text})
            continue
        idx += 1
        rows.append({"kind": "mail", "index": idx, "y": ln["bbox"][1],
                     "bbox": ln["bbox"], "text": text})
    return {"rows": rows, "raw_lines": res["plain_lines"]}


def _on_phone(game: str) -> bool:
    """当前是否已在收件箱界面（用 OCR 判断）。"""
    try:
        import difflib
        res = _shot("probe", game)
        for ln in res["lines"]:
            if difflib.SequenceMatcher(None, "收件箱", ln["text"]).ratio() > 0.5:
                return True
    except Exception:
        pass
    return False


def open_phone(game: str) -> dict:
    """确保手机打开并停在收件箱。"""
    api.call("attach", {"target": game, "focus": True})
    if not _on_phone(game):
        api.call("game_action", {"action": "phone", "game": game})   # Z
        time.sleep(1.2)
    if not _on_phone(game):
        api.call("game_action", {"action": "confirm", "game": game})  # ENTER 进信箱
        time.sleep(1.5)
    return {"on_phone": _on_phone(game)}


def open_mail(game: str, target: "str | int") -> dict:
    """按"文字"或"行号"打开邮件，返回正文 OCR 结果。"""
    open_phone(game)
    data = list_rows(game)
    mails = [r for r in data["rows"] if r["kind"] == "mail"]
    if not mails:
        raise RuntimeError("没读到邮件行（可能不在收件箱界面）")

    if isinstance(target, int) or str(target).isdigit():
        row = next((r for r in mails if r["index"] == int(target)), None)
    else:
        import difflib

        def key(r: dict) -> float:
            return difflib.SequenceMatcher(None, str(target), r["text"]).ratio()

        row = max(mails, key=key)
    if row is None:
        raise RuntimeError(f"找不到目标邮件: {target!r}")

    # 列表顶部那封是"当前选中项"；要打开第 k 封就先按 k 次 ↓
    for _ in range(int(row["index"])):
        api.call("key_press", {"key": "down", "ms": 40})
        time.sleep(0.35)
    api.call("game_action", {"action": "confirm", "game": game})      # ENTER 打开
    time.sleep(1.5)
    body = _shot("body", game)
    return {"mail": row, "body_lines": body["plain_lines"]}


def reply_probe(game: str) -> dict:
    """在正文界面按 ENTER（若可回复，会进入回复选项），并读出该界面文字。"""
    api.call("game_action", {"action": "confirm", "game": game})
    time.sleep(1.5)
    res = _shot("reply", game)
    return {"lines": res["plain_lines"]}


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    game, mode, arg = "SG", "--list", None
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--game":
            game = argv[i + 1]; i += 2
        elif a in ("--list", "--reply", "--open"):
            mode = a
            if a == "--reply" and i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                arg = argv[i + 1]; i += 2
            elif a == "--open":
                arg = argv[i + 1] if i + 1 < len(argv) else "0"; i += 2
            else:
                i += 1
        else:
            i += 1

    print(f"== 手机 OCR：{game} 模式={mode} 参数={arg}")
    if mode == "--list":
        open_phone(game)
        data = list_rows(game)
        print(f"共 {len([r for r in data['rows'] if r['kind'] == 'mail'])} 封邮件：")
        for r in data["rows"]:
            tag = "  " if r["kind"] == "mail" else "# "
            idx = "-" if r["index"] is None else r["index"]
            print(f"  {tag}[{idx:>2}] y={r['y']:>4}  {r['text']}")
    elif mode == "--open":
        res = open_mail(game, arg if arg is not None else 0)
        print(f"\n打开邮件: {res['mail']['text']}（列表第 {res['mail']['index']} 封）")
        print("—— 正文 OCR ——")
        for ln in res["body_lines"]:
            print("   " + ln)
    else:
        if arg:
            open_mail(game, arg)
        res = reply_probe(game)
        print("—— 按 ENTER 后的界面 OCR ——")
        for ln in res["lines"]:
            print("   " + ln)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
