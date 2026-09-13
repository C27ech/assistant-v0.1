"""sg_bridge.tools.find_ui_text — 在我们已提取的文本里搜"界面措辞"

用途：定位游戏内"按键设置/选项"界面的原文，从而知道有哪些可配置动作、界面怎么叫。

用法::

    python -m sg_bridge.tools.find_ui_text                  # 默认关键词
    python -m sg_bridge.tools.find_ui_text 键 快进 自动       # 自定义关键词
    python -m sg_bridge.tools.find_ui_text --max 8 KEY CONFIG
"""
from __future__ import annotations

import glob
import os
import sys

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
OUT_DIR = os.path.join(ROOT, "output")

DEFAULT_CN = ["按键", "键位", "键", "快进", "自动", "历史记录", "存档", "读档", "菜单",
              "设置", "设定", "音量", "全屏", "窗口", "鼠标", "滚轮", "右击", "右键"]
DEFAULT_EN = ["KEY CONFIG", "KEYCONFIG", "KEY SETTING", "CONFIG", "SETTING", "SKIP",
              "AUTO", "BACKLOG", "VOLUME", "MOUSE", "WHEEL", "CLICK"]


def _targets() -> list[str]:
    files: list[str] = []
    for pattern in (os.path.join(OUT_DIR, "*", "lines.tsv"),
                    os.path.join(OUT_DIR, "*", "summary.txt")):
        files.extend(glob.glob(pattern))
    return files


def search(files: list[str], keywords: list[str], max_per_kw: int = 6) -> dict:
    result: dict[str, list[tuple[str, str]]] = {kw: [] for kw in keywords}
    for path in files:
        set_name = os.path.basename(os.path.dirname(path))
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for lineno, line in enumerate(fh, 1):
                    lowered = line.lower()
                    for kw in keywords:
                        if kw.lower() in lowered and len(result[kw]) < max_per_kw:
                            result[kw].append((f"{set_name}:{lineno}", line.strip()[:160]))
        except OSError:
            continue
    return result


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    max_per_kw = 6
    keywords: list[str] = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--max":
            max_per_kw = int(argv[i + 1])
            i += 2
            continue
        if arg.startswith("--kw="):
            keywords = [k for k in arg[5:].split(",") if k]
            i += 1
            continue
        keywords.append(arg)
        i += 1
    if not keywords:
        keywords = DEFAULT_CN + DEFAULT_EN
    files = _targets()
    print(f"搜索 {len(files)} 个文件，关键词 {len(keywords)} 个\n")
    for kw, hits in search(files, keywords, max_per_kw).items():
        if not hits:
            continue
        print(f"=== 「{kw}」 {len(hits)} 条")
        for where, text in hits:
            print(f"    [{where}] {text}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
