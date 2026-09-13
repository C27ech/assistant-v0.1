"""sg_bridge.core.ocr — 屏幕文字识别（截图 → OCR → 带坐标的文字）

实现复用 ``sg_bridge.tools.ocr``（Windows.Media.Ocr，需本机有对应语言包；
本机已确认有 **zh-Hans-CN / en-US**）。Tesseract 作为备选引擎。

对外接口（见 ``api.py``）：
  * ``read_screen``  —— 截当前游戏窗口 → OCR → 返回每行文字与包围盒
  * ``click_text``   —— 在屏幕上找指定文字 → 按坐标点击
"""
from __future__ import annotations

import os
import time

from ..tools import ocr as _ocr
from . import wininput as W

#: 直接复用 tools/ocr.py 的实现
read_text = _ocr.read_text


def _grab(hwnd: "int | None", save_path: "str | None" = None) -> str:
    """截取窗口客户区，返回图片路径（不传 hwnd 则整屏）。"""
    if save_path is None:
        save_path = os.path.join(os.path.expanduser("~"), ".sg_bridge_shot.png")
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    if hwnd:
        W.screenshot_window(save_path, int(hwnd), client_only=True)
    else:
        W.screenshot(save_path)
    return save_path


def read_screen(hwnd: "int | None" = None, crop: "str | None" = None,
                scale: float = 2.0, lang: str = "zh-Hans-CN",
                engine: str = "win", save_path: "str | None" = None) -> dict:
    """截屏 + OCR，返回带坐标的文字行。"""
    t0 = time.perf_counter()
    path = _grab(hwnd, save_path)
    res = _ocr.read_text(path, crop=crop, scale=scale, engine=engine, lang=lang)
    res["shot"] = path
    res["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return res


def find_text(hwnd: "int | None" = None, needle: str = "", crop: "str | None" = None,
              scale: float = 2.0, lang: str = "zh-Hans-CN",
              exact: bool = False, threshold: float = 0.6) -> dict:
    """在屏幕上找文字，返回匹配行及其坐标（用于点击）。

    OCR 难免有错字（如「指」→「扌旨」），因此默认用 **difflib 相似度**做模糊匹配：
    相似度 ≥ ``threshold`` 即算命中（``exact=True`` 时要求包含/完全相同）。
    """
    import difflib
    import re

    res = read_screen(hwnd, crop=crop, scale=scale, lang=lang)
    norm = lambda s: re.sub(r"[\s\$\[\]（）()．.·、,，!！?？]", "", s or "")  # noqa: E731
    target = norm(needle)
    hits = []
    for ln in res["lines"]:
        text = ln.get("text", "")
        plain = norm(text)
        if exact:
            score = 1.0 if (text == needle or needle in text) else 0.0
        elif not target:
            score = 0.0
        else:
            if target in plain:
                score = 1.0
            else:
                score = difflib.SequenceMatcher(None, target, plain).ratio()
        if score >= threshold:
            x, y, w, h = ln["bbox"]
            hits.append({"text": text, "score": round(score, 3), "bbox": ln["bbox"],
                         "center": [x + w // 2, y + h // 2],
                         "words": [w2["text"] for w2 in ln.get("words", [])]})
    hits.sort(key=lambda d: (-d["score"], d["bbox"][1]))
    return {"needle": needle, "hits": hits, "threshold": threshold,
            "lines": res["plain_lines"], "shot": res["shot"], "elapsed_ms": res["elapsed_ms"]}
