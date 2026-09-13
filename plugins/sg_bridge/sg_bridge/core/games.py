"""四部作品的注册表、窗口识别与"语义动作"预设。

设计要点
--------
* **按 exe 所在目录匹配窗口**（而不是标题）：SG 与 SG0 的标题都含 "STEINS;GATE"，
  用文件夹匹配最稳；同目录若有多个窗口（如启动器），取客户区最大的那个。
* **语义动作**（advance/skip/menu/…）只是"按键/点击"的命名打包，方便 AI 调用。
  默认值来自 MAGES. 引擎的常见绑定，并标注置信度；用户可在
  ``sg_bridge/config/bindings.json`` 里覆盖（以游戏内 CONFIG 为准）。
"""
from __future__ import annotations

import json
import os
from typing import Any

from . import wininput as W

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config")
BINDINGS_PATH = os.path.join(CONFIG_DIR, "bindings.json")

#: 作品 key → 信息
GAMES: dict[str, dict[str, Any]] = {
    "SG": {
        "name": "STEINS;GATE",
        "lang": "zh-CN",
        "exe": r"C:\Program Files (x86)\Steam\steamapps\common\STEINS;GATE\Game.exe",
    },
    "SG0": {
        "name": "STEINS;GATE 0",
        "lang": "zh-CN",
        "exe": r"C:\Program Files (x86)\Steam\steamapps\common\STEINS;GATE 0\Game.exe",
    },
    "SGLBP": {
        "name": "STEINS;GATE: Linear Bounded Phenogram",
        "lang": "en",
        "exe": r"C:\Program Files (x86)\Steam\steamapps\common\SG_Phenogram\Game.exe",
    },
    "SGMDE": {
        "name": "STEINS;GATE: My Darling's Embrace",
        "lang": "en",
        "exe": r"C:\Program Files (x86)\Steam\steamapps\common\SG_My Darling's Embrace\Game.exe",
    },
}

#: 语义动作 → 步骤。来源：**游戏内 HELP（KEYBOARD & MOUSE 1/2 页）画面**（权威）
#: confidence: verified=已实测 / help=直接读自 HELP 画面 / guess=推测
#: dangerous=True 的动作会显著改变游戏状态（退出/回标题/全屏/覆盖存档），
#: 调用 game_action 时需显式传 allow_dangerous=True 才会执行。
MAGES_DEFAULT: dict[str, dict[str, Any]] = {
    # ---- 对话推进 ----
    "advance": {
        "steps": [{"op": "key_press", "key": "enter", "ms": 40}],
        "confidence": "verified",
        "note": "推进对话（左键点击同样有效）。实测：与对话框区域产生小幅像素变化。",
    },
    "confirm": {
        "steps": [{"op": "key_press", "key": "enter", "ms": 40}],
        "confidence": "verified",
        "note": "确定。实测：标题画面按 Enter 进入主菜单。",
    },
    "back": {
        "steps": [{"op": "key_press", "key": "backspace", "ms": 40}],
        "confidence": "verified",
        "note": "返回。实测：可关闭 HELP 画面、系统菜单等界面。",
    },
    "cancel": {
        "steps": [{"op": "key_press", "key": "backspace", "ms": 40}],
        "confidence": "verified",
        "note": "取消（同「返回」）。注意 **不是 ESC** —— ESC 是「游戏结束」。",
    },
    "click_confirm": {
        "steps": [{"op": "click", "button": "left"}],
        "confidence": "help",
        "note": "鼠标左键＝确定。",
    },
    "right_click": {
        "steps": [{"op": "click", "button": "right"}],
        "confidence": "help",
        "note": "鼠标右键＝返回／系统菜单（仅游戏中）。",
    },
    # ---- 界面 ----
    "menu": {
        "steps": [{"op": "key_press", "key": "1", "ms": 40}],
        "confidence": "verified",
        "note": "系统菜单＝数字键 1（HELP 明确标注；实测画面变化约 9.6%，BACKSPACE 可关）。"
                "⚠️ 早期版本曾误设为 ESC，ESC 其实是「游戏结束」。",
    },
    "help": {
        "steps": [{"op": "key_press", "key": "f1", "ms": 40}],
        "confidence": "help",
        "note": "HELP（按键一览）。",
    },
    "config": {
        "steps": [{"op": "key_press", "key": "f9", "ms": 40}],
        "confidence": "help",
        "note": "配置（音量/画面等）。",
    },
    "tips": {
        "steps": [{"op": "key_press", "key": "f4", "ms": 40}],
        "confidence": "help",
        "note": "TIPS 用语辞典。",
    },
    "backup_log": {
        "steps": [{"op": "key_press", "key": "f2", "ms": 40}],
        "confidence": "help",
        "note": "备份日志（INSERT 亦可）。",
    },
    "backup_log_alt": {
        "steps": [{"op": "key_press", "key": "insert", "ms": 40}],
        "confidence": "help",
        "note": "备份日志（INSERT）。",
    },
    # ---- 快进 / 自动 / 显示 ----
    "skip": {
        "steps": [{"op": "key_press", "key": "ctrl", "ms": 3000}],
        "confidence": "help",
        "note": "强行快进＝**按住 CTRL**（HELP 标注）。调用时可用 ms 覆盖时长，"
                "也能 key_down/key_up 精确控制按住时间。",
    },
    "skip_hold": {
        "steps": [{"op": "key_press", "key": "ctrl", "ms": 3000}],
        "confidence": "help",
        "note": "同 skip（长按强制快进）。",
    },
    "skip_mode": {
        "steps": [{"op": "key_press", "key": "z", "ms": 40}],
        "confidence": "help",
        "note": "快进模式切换＝Z。实测：在未读文本处按 Z 画面无明显变化（未读文本不会被跳过），"
                "属预期；读过后再测应能看到差异。",
    },
    "auto": {
        "steps": [{"op": "key_press", "key": "f3", "ms": 40}],
        "confidence": "help",
        "note": "自动模式＝F3（DELETE 亦可）。",
    },
    "auto_alt": {
        "steps": [{"op": "key_press", "key": "delete", "ms": 40}],
        "confidence": "help",
        "note": "自动模式（DELETE）。",
    },
    "hide_text": {
        "steps": [{"op": "key_press", "key": "shift", "ms": 600}],
        "confidence": "help",
        "note": "隐藏文字＝按住 SHIFT（默认按 600ms；要长时间隐藏请用 key_down/key_up）。",
    },
    # ---- 手机（本作核心系统） ----
    "phone": {
        "steps": [{"op": "key_press", "key": "z", "ms": 40}],
        "confidence": "verified",
        "note": "手机触发器＝**Z**（用户实测确认）。打开后用 ↑↓ 在手机菜单移动、ENTER 进入、BACKSPACE 返回。",
    },
    "phone_alt": {
        "steps": [{"op": "key_press", "key": "c", "ms": 40}],
        "confidence": "verified",
        "note": "手机触发器的第二个键＝**C**（与 HELP 图上 Z/C 连线到同一标签一致；"
                "实测：退出手机后按 C 能再次打开）。",
    },
    "phone_close": {
        "steps": [{"op": "key_press", "key": "x", "ms": 40}],
        "confidence": "verified",
        "note": "收起手机＝**X**（用户实测确认）。与「返回」(BACKSPACE) 不同："
                "BACKSPACE 在手机内部是返回上一层，X 是直接收起手机。",
    },
    # ---- 存档相关 ----
    "save": {
        "steps": [{"op": "key_press", "key": "f8", "ms": 40}],
        "confidence": "help",
        "note": "保存（打开存档界面，随后需选择槽位）。",
    },
    "load": {
        "steps": [{"op": "key_press", "key": "f6", "ms": 40}],
        "confidence": "help",
        "note": "载入（打开读档界面）。",
    },
    "quick_save": {
        "steps": [{"op": "key_press", "key": "f5", "ms": 40}],
        "confidence": "help",
        "dangerous": True,
        "note": "快速保存＝F5（直接覆盖快速存档）。",
    },
    "quick_load": {
        "steps": [{"op": "key_press", "key": "f7", "ms": 40}],
        "confidence": "help",
        "dangerous": True,
        "note": "快速载入＝F7（立刻回到快速存档点，当前进度丢弃）。",
    },
    # ---- 危险动作（需 allow_dangerous=True） ----
    "quit": {
        "steps": [{"op": "key_press", "key": "escape", "ms": 40}],
        "confidence": "help",
        "dangerous": True,
        "note": "⚠️ 游戏结束＝ESC（弹出退出确认；确认=ENTER，取消=BACKSPACE）。",
    },
    "title": {
        "steps": [{"op": "key_press", "key": "f10", "ms": 40}],
        "confidence": "help",
        "dangerous": True,
        "note": "⚠️ 回到标题画面＝F10（未保存进度丢失）。",
    },
    "fullscreen": {
        "steps": [{"op": "key_press", "key": "f11", "ms": 40}],
        "confidence": "help",
        "dangerous": True,
        "note": "⚠️ 全屏切换＝F11（改变显示模式，可能影响坐标/截图）。",
    },
    # ---- 其它 ----
    "screenshot": {
        "steps": [{"op": "key_press", "key": "f12", "ms": 40}],
        "confidence": "help",
        "note": "游戏自带截图＝F12（与桥接的 screenshot 工具不同）。",
    },
    "choice_up": {
        "steps": [{"op": "key_press", "key": "up", "ms": 40}],
        "confidence": "help",
        "note": "选项上移（HELP：项目选择＝方向键）。",
    },
    "choice_down": {
        "steps": [{"op": "key_press", "key": "down", "ms": 40}],
        "confidence": "help",
        "note": "选项下移。",
    },
    "choice_left": {
        "steps": [{"op": "key_press", "key": "left", "ms": 40}],
        "confidence": "help",
        "note": "向左（TIPS 正文滚动等）。",
    },
    "choice_right": {
        "steps": [{"op": "key_press", "key": "right", "ms": 40}],
        "confidence": "help",
        "note": "向右（TIPS 正文滚动等）。",
    },
    "scroll_up": {
        "steps": [{"op": "scroll", "amount": 120}],
        "confidence": "help",
        "note": "滚轮向上（列表/正文滚动、备份日志）。",
    },
    "scroll_down": {
        "steps": [{"op": "scroll", "amount": -120}],
        "confidence": "help",
        "note": "滚轮向下。",
    },
}


def _load_bindings_file() -> dict[str, Any]:
    """读取（首次自动生成）绑定覆盖文件。"""
    if not os.path.exists(BINDINGS_PATH):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        payload = {
            "_comment": "语义动作覆盖表：把 steps 改成本机实际按键即可；未写的动作沿用默认。",
            "_example": {"advance": {"steps": [{"op": "key_press", "key": "space", "ms": 40}]}},
            "_ops": ["key_press", "key_down", "key_up", "click", "click_norm",
                     "scroll", "mouse_move", "sleep"],
            "overrides": {},
        }
        with open(BINDINGS_PATH, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        return {}
    try:
        with open(BINDINGS_PATH, "r", encoding="utf-8") as fh:
            return json.load(fh).get("overrides", {}) or {}
    except Exception:
        return {}


def bindings(game: "str | None" = None) -> dict[str, Any]:
    """返回语义动作表（默认 → ``*`` 覆盖 → 该作品覆盖）。"""
    overrides = _load_bindings_file()
    merged: dict[str, Any] = {k: dict(v) for k, v in MAGES_DEFAULT.items()}
    for k, v in (overrides.get("*") or {}).items():
        merged.setdefault(k, {}).update(v)
    if game:
        for k, v in (overrides.get(game) or {}).items():
            merged.setdefault(k, {}).update(v)
    return merged


def game_info(key: str) -> dict[str, Any]:
    """作品信息（含 exe 是否存在、目录）。"""
    if key not in GAMES:
        raise KeyError(f"未知作品: {key!r}（可选：{', '.join(GAMES)}）")
    info = dict(GAMES[key])
    info["key"] = key
    info["exe_exists"] = os.path.exists(info["exe"])
    info["dir"] = os.path.dirname(info["exe"])
    return info


def _same_dir(path: str, folder: str) -> bool:
    if not path:
        return False
    try:
        return os.path.normcase(os.path.dirname(os.path.abspath(path))) == os.path.normcase(
            os.path.abspath(folder)
        )
    except Exception:
        return False


def find_game_windows(key: str) -> list[dict]:
    """找出该作品进程的所有可见窗口（按客户区面积降序）。"""
    folder = game_info(key)["dir"]
    hits = [w for w in W.list_windows() if _same_dir(w.get("process", ""), folder)]
    hits.sort(key=lambda w: w["client_size"][0] * w["client_size"][1], reverse=True)
    return hits


def detect_running() -> list[dict]:
    """四部作品的运行状态（含 hwnd / 标题 / 客户区尺寸）。"""
    out = []
    for key in GAMES:
        wins = find_game_windows(key)
        main = wins[0] if wins else None
        out.append({
            "game": key,
            "name": GAMES[key]["name"],
            "exe_exists": os.path.exists(GAMES[key]["exe"]),
            "running": bool(wins),
            "hwnd": main["hwnd"] if main else None,
            "title": main["title"] if main else None,
            "client_size": main["client_size"] if main else None,
            "window_count": len(wins),
        })
    return out


def resolve_window(target: "str | int | None", hwnd: "int | None" = None) -> dict:
    """把 ``target``（作品 key / hwnd 数字 / 标题片段）解析为一个窗口信息 dict。"""
    if hwnd:
        return W.window_info(int(hwnd))
    if target is None:
        fg = W.foreground_window()
        if not fg:
            raise RuntimeError("没有前台窗口")
        return fg
    if isinstance(target, int):
        return W.window_info(int(target))
    text = str(target).strip()
    if text.upper() in GAMES:
        wins = find_game_windows(text.upper())
        if not wins:
            raise RuntimeError(f"{text.upper()} 未在运行（找不到对应窗口）")
        return wins[0]
    if text.isdigit():
        return W.window_info(int(text))
    win = W.find_window(title_contains=text)
    if not win:
        raise RuntimeError(f"找不到标题包含 {text!r} 的窗口")
    return win