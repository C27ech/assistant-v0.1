"""sg_bridge.tools.look — 「看一眼屏幕」：OCR 读出画面文字（带坐标），可选按文字点击

用法::

    python -m sg_bridge.tools.look --game SG
    python -m sg_bridge.tools.look --game SG --crop 0.62,0.05,0.38,0.9
    python -m sg_bridge.tools.look --game SG --click 闪光指压师
    python -m sg_bridge.tools.look --game SG --click 收件箱 --index 0
"""
from __future__ import annotations

import os
import sys
import time

from .. import api


def _log(path: "str | None", text: str) -> None:
    print(text)
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")


def _summarize(lines: list[dict], panel_x: int = 1200) -> str:
    """把 OCR 行按"左侧对话区 / 右侧手机面板"分组，输出紧凑摘要。"""
    left = [ln for ln in lines if ln["bbox"][0] < panel_x]
    right = [ln for ln in lines if ln["bbox"][0] >= panel_x]
    out = []
    for ln in left:
        b = ln["bbox"]
        if b[3] >= 20 and b[1] > 400:                     # 对话区（大字）
            out.append(f"    [对话 y={b[1]:>4}] {ln['text']}")
    for i, ln in enumerate(right):
        b = ln["bbox"]
        out.append(f"    [面板 {i:>2} y={b[1]:>4}] {ln['text']}")
    return "\n".join(out) or "    （无明显文字）"


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    game, crop, scale = "SG", None, 2.0
    click, index, exact = None, 0, False
    keys, out, summary = None, None, False
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--game":
            game = argv[i + 1]; i += 2
        elif a == "--crop":
            crop = argv[i + 1]; i += 2
        elif a == "--scale":
            scale = float(argv[i + 1]); i += 2
        elif a == "--click":
            click = argv[i + 1]; i += 2
        elif a == "--index":
            index = int(argv[i + 1]); i += 2
        elif a == "--exact":
            exact = True; i += 1
        elif a == "--keys":
            keys = [k.strip() for k in argv[i + 1].split(",") if k.strip()]; i += 2
        elif a == "--out":
            out = argv[i + 1]; i += 2
        elif a == "--summary":
            summary = True; i += 1
        else:
            i += 1

    if out and os.path.exists(out):
        os.remove(out)

    api.call("attach", {"target": game, "focus": True})

    def read(tag: str) -> list[dict]:
        res = api.call("read_screen", {"target": game, "crop": crop, "scale": scale})
        if not res.get("ok"):
            _log(out, f"[{tag}] 读取失败: {res.get('error')}")
            return []
        lines = res["data"]["lines"]
        _log(out, f"=== [{tag}] 屏幕文字 {len(lines)} 行")
        if summary:
            _log(out, _summarize(lines))
        else:
            for ln in lines:
                b = ln["bbox"]
                _log(out, f"  [{b[0]:>4},{b[1]:>4} {b[2]:>4}x{b[3]:>3}]  {ln['text']}")
        return lines

    read("初始")

    if keys:
        for k in keys:
            api.call("game_action", {"action": _to_action(k), "game": game}) if k in (
                "confirm", "back", "phone", "phone_close") else api.call(
                "key_press", {"key": k, "ms": 40})
            time.sleep(1.5)
            read(f"按 {k} 后")

    if click:
        res2 = api.call("click_text", {"text": click, "target": game, "crop": crop,
                                       "scale": scale, "index": index, "exact": exact})
        if res2.get("ok"):
            d = res2["data"]
            _log(out, f"\n>>> 已点击「{d['matched_text']}」 光标={d['cursor']} "
                      f"（命中 {d['hits']} 处，第 {d['hit_index']} 个）")
        else:
            _log(out, f"\n>>> 点击失败: {res2.get('error')}")
            return 1
    if out:
        print(f"\n（完整记录已写入 {out}）")
    return 0


def _to_action(key: str) -> str:
    """把常用键名映射成语义动作名（走 game_action 以便自动聚焦+英文输入法）。"""
    return {"confirm": "confirm", "enter": "confirm", "back": "back",
            "phone": "phone", "phone_close": "phone_close"}.get(key, key)


if __name__ == "__main__":
    raise SystemExit(main())
