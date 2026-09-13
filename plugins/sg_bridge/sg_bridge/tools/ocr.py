"""sg_bridge.tools.ocr — 用**本机 OCR** 把截图里的文字读出来（替代"读图"）

为什么需要：本会话的图像读取有配额限制，但 OCR 不受影响 —— 而且对"读游戏界面文字"来说，
OCR 比肉眼看图更快、更结构化，可直接喂给程序/AI。

引擎：
  * ``win`` ：Windows 自带 OCR（Windows.Media.Ocr），本机有 **zh-Hans-CN / en-US** 语言包 ✓
  * ``tess``：Tesseract（本机已装 `C:\\Program Files\\Tesseract-OCR\\tesseract.exe`）

用法::

    python -m sg_bridge.tools.ocr <图.png>                          # 全图中文
    python -m sg_bridge.tools.ocr <图.png> --crop 0.68,0.15,0.32,0.75  # 只读手机面板
    python -m sg_bridge.tools.ocr <图.png> --crop 100,200,400,300 --scale 2 --lang en-US
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
PS1 = os.path.join(HERE, "win_ocr.ps1")
TESS = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
CREATE_NO_WINDOW = 0x08000000          # 子进程不创建控制台窗口（关键：避免抢走游戏焦点）


def _prepare(src: str, crop: "str | None", scale: float = 1.0, out: "str | None" = None) -> str:
    img = Image.open(src).convert("RGB")
    if crop:
        vals = [float(v) for v in crop.split(",")]
        if all(v <= 1.0 for v in vals):
            x, y, w, h = vals[0] * img.size[0], vals[1] * img.size[1], vals[2] * img.size[0], vals[3] * img.size[1]
        else:
            x, y, w, h = vals
        img = img.crop((int(x), int(y), int(x + w), int(y + h)))
    if scale != 1.0:
        img = img.resize((max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale))),
                         Image.LANCZOS)
    if out is None:
        fd, out = tempfile.mkstemp(suffix=".png")
        os.close(fd)
    img.save(out, "PNG")
    return out


def ocr_win(img_path: str, lang: str = "zh-Hans-CN") -> str:
    """Windows 自带 OCR（子进程以**无窗口**方式运行，避免抢走游戏焦点）。"""
    fd, txt = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
           "-File", PS1, "-Path", os.path.abspath(img_path), "-Out", txt, "-LangTag", lang]
    # CREATE_NO_WINDOW：不创建控制台窗口，避免抢焦点（否则前台游戏会丢焦点、按键状态被重置）
    subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    with open(txt, "r", encoding="utf-8-sig", errors="ignore") as fh:
        text = fh.read().strip()
    os.unlink(txt)
    return text


def ocr_tess(img_path: str, lang: str = "chi_sim+eng") -> str:
    """Tesseract OCR。"""
    if not os.path.exists(TESS):
        return "TESSERACT_NOT_FOUND"
    res = subprocess.run([TESS, img_path, "stdout", "-l", lang],
                         capture_output=True, text=True, encoding="utf-8", errors="ignore")
    return (res.stdout or "").strip()


def _parse_words(raw: str, crop: "str | None", scale: float, src: str) -> list[dict]:
    """把 PS 输出的 ``x,y,w,h<TAB>text`` 解析为词列表，并把坐标映射回**原图**。"""
    img_w, img_h = Image.open(src).size
    ox = oy = 0.0
    if crop:
        vals = [float(v) for v in crop.split(",")]
        if all(v <= 1.0 for v in vals):
            ox, oy = vals[0] * img_w, vals[1] * img_h
        else:
            ox, oy = vals[0], vals[1]
    words: list[dict] = []
    for line in raw.splitlines():
        if "\t" not in line:
            continue
        box, text = line.split("\t", 1)
        try:
            x, y, w, h = [int(float(v)) for v in box.split(",")]
        except ValueError:
            continue
        if not text.strip():
            continue
        words.append({
            "text": text.strip(),
            "x": int(ox + x / scale), "y": int(oy + y / scale),
            "w": int(w / scale), "h": int(h / scale),
        })
    return words


def _group_lines(words: list[dict], tol: int = 12) -> list[dict]:
    """按 y 把词聚成行（行内按 x 排序），返回带包围盒的行。"""
    lines: list[dict] = []
    for w in sorted(words, key=lambda d: (d["y"], d["x"])):
        placed = False
        for ln in lines:
            if abs(ln["y"] - w["y"]) <= tol:
                ln["words"].append(w)
                ln["x0"] = min(ln["x0"], w["x"])
                ln["x1"] = max(ln["x1"], w["x"] + w["w"])
                ln["y0"] = min(ln["y0"], w["y"])
                ln["y1"] = max(ln["y1"], w["y"] + w["h"])
                placed = True
                break
        if not placed:
            lines.append({"y": w["y"], "x0": w["x"], "x1": w["x"] + w["w"],
                          "y0": w["y"], "y1": w["y"] + w["h"], "words": [w]})
    out = []
    for ln in lines:
        ln["words"].sort(key=lambda d: d["x"])
        ln["text"] = "".join(d["text"] for d in ln["words"])
        ln["bbox"] = [ln["x0"], ln["y0"], ln["x1"] - ln["x0"], ln["y1"] - ln["y0"]]
        out.append(ln)
    out.sort(key=lambda d: d["y0"])
    return out


def read_text(src: str, crop: "str | None" = None, scale: float = 1.0,
              engine: str = "win", lang: "str | None" = None) -> dict:
    prepared = _prepare(src, crop, scale)
    try:
        if engine == "tess":
            raw = ocr_tess(prepared, lang or "chi_sim+eng")
            words = [{"text": t, "x": 0, "y": 0, "w": 0, "h": 0}
                     for t in raw.splitlines() if t.strip()]
            lines = [{"text": t.strip(), "bbox": None, "words": []}
                     for t in raw.splitlines() if t.strip()]
        else:
            raw = ocr_win(prepared, lang or "zh-Hans-CN")
            words = _parse_words(raw, crop, scale, src)
            lines = _group_lines(words)
    finally:
        if prepared.startswith(tempfile.gettempdir()):
            try:
                os.unlink(prepared)
            except OSError:
                pass
    text = "\n".join(ln["text"] for ln in lines)
    return {"src": src, "crop": crop, "scale": scale, "engine": engine,
            "words": words, "lines": lines, "text": text,
            "plain_lines": [ln["text"] for ln in lines]}


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    src, crop, scale, engine, lang, boxes, words_mode = argv[0], None, 1.0, "win", None, False, False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == "--crop":
            crop = argv[i + 1]; i += 2
        elif a == "--scale":
            scale = float(argv[i + 1]); i += 2
        elif a == "--engine":
            engine = argv[i + 1]; i += 2
        elif a == "--lang":
            lang = argv[i + 1]; i += 2
        elif a == "--boxes":
            boxes = True; i += 1
        elif a == "--words":
            words_mode = True; i += 1
        else:
            i += 1
    res = read_text(src, crop, scale, engine, lang)
    print(f"=== OCR({res['engine']}) {os.path.basename(src)} crop={res['crop']} ×{res['scale']}"
          f"  行数={len(res['lines'])}  词数={len(res['words'])}")
    if words_mode:
        for w in sorted(res["words"], key=lambda d: (d["y"], d["x"])):
            print(f"  [{w['x']:>4},{w['y']:>4} {w['w']:>4}x{w['h']:>3}]  {w['text']}")
        return 0
    if not res["lines"]:
        print("（没有识别到文字）")
    for ln in res["lines"]:
        if boxes and ln.get("bbox"):
            b = ln["bbox"]
            print(f"  [{b[0]:>4},{b[1]:>4} {b[2]:>4}x{b[3]:>3}]  {ln['text']}")
        else:
            print("  " + ln["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
