"""sg_bridge.tools.probe_keybindings — 从 exe 里挖"按键/配置界面"相关字符串

用途：确认游戏的按键设置界面用什么措辞（日文/中文）、有哪些动作名、
以及输入实现方式（DirectInput / GetAsyncKeyState / 鼠标），为"实测读取绑定"做准备。

用法::

    python -m sg_bridge.tools.probe_keybindings            # 默认扫四作 Game.exe + Launcher
    python -m sg_bridge.tools.probe_keybindings <exe> ...
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.normpath(os.path.join(HERE, "..", "out", "keybindings_probe.txt"))

GAME_DIRS = [
    r"C:\Program Files (x86)\Steam\steamapps\common\STEINS;GATE",
    r"C:\Program Files (x86)\Steam\steamapps\common\STEINS;GATE 0",
    r"C:\Program Files (x86)\Steam\steamapps\common\SG_Phenogram",
    r"C:\Program Files (x86)\Steam\steamapps\common\SG_My Darling's Embrace",
]

#: 分组关键词
GROUPS: dict[str, list[str]] = {
    "动作名(日)": ["キー", "コンフィグ", "決定", "キャンセル", "スキップ", "オート",
                   "バックログ", "セーブ", "ロード", "メニュー", "右クリック", "ホイール",
                   "マウス", "入力", "操作", "音量", "画面"],
    "动作名(中)": ["按键", "键位", "设置", "快进", "自动", "历史", "存档", "读档",
                   "菜单", "鼠标", "滚轮", "确定", "取消"],
    "输入实现": ["dinput", "DirectInput", "GetAsyncKeyState", "GetKeyboardState",
                 "GetCursorPos", "mouse_event", "SendInput", "DIK_", "JOY"],
    "配置/存档文件": ["config", "CONFIG", "setting", "Setting", ".dat", ".sav",
                      "savedata", "ksave", "gamedata", "system.dat", "profile"],
    "按键名": ["RETURN", "ENTER", "SPACE", "ESCAPE", "CONTROL", "SHIFT", "ALT",
               "LEFT", "RIGHT", "UP", "DOWN", "TAB", "BACK", "WHEEL", "BUTTON"],
}


def _ascii_strings(data: bytes, min_len: int = 4):
    for m in re.finditer(rb"[\x20-\x7E]{%d,}" % min_len, data):
        yield m.start(), m.group().decode("ascii", "ignore")


def _utf16_strings(data: bytes, min_len: int = 3):
    for m in re.finditer(rb"(?:[\x20-\x7E\x00-\xFF]\x00){%d,}" % min_len, data):
        try:
            s = m.group().decode("utf-16-le", "ignore")
        except Exception:
            continue
        s = s.strip()
        if len(s) >= min_len:
            yield m.start(), s


def scan(path: str) -> dict:
    with open(path, "rb") as fh:
        data = fh.read()
    hits: dict[str, list[str]] = {k: [] for k in GROUPS}
    seen: set[str] = set()
    for _off, s in list(_ascii_strings(data)) + list(_utf16_strings(data)):
        for group, keys in GROUPS.items():
            if any(k in s for k in keys):
                if s not in seen:
                    seen.add(s)
                    hits[group].append(s)
                break
    return {"path": path, "size": len(data),
            "hits": {k: v for k, v in hits.items() if v}}


def _candidates() -> list[str]:
    out = []
    for d in GAME_DIRS:
        for name in ("Game.exe", "Launcher.exe", "launcher.exe"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                out.append(p)
    return out


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    targets = argv or _candidates()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    lines: list[str] = []
    for path in targets:
        res = scan(path)
        lines.append(f"===== {res['path']}  ({res['size']:,} bytes)")
        for group, items in res["hits"].items():
            lines.append(f"  --- {group} ({len(items)})")
            for s in items[:400]:
                lines.append(f"      {s}")
        lines.append("")
    text = "\n".join(lines)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    for path in targets:
        res = scan(path)
        print(f"--- {os.path.basename(os.path.dirname(path))}\\{os.path.basename(path)}")
        for group, items in res["hits"].items():
            print(f"    {group}: {len(items)} 条")
    print(f"\n完整结果: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
