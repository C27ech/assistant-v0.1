"""sg_bridge.tools.verify_games — 逐部作品验证"这个插件能不能用"

对每部作品做同一套检查（不需要玩到剧情深处，标题画面即可）：
  1. 找到 exe / 是否安装
  2. 启动（优先直接起 Game.exe；失败则用同目录的 Launcher）
  3. 等待窗口出现 → attach（含自动切英文输入）
  4. **按键响应测试**：截图 A → 按 ENTER → 截图 B → 像素差（应明显变化）
  5. **读屏测试**：OCR 全屏，看能否读出文字（标题/菜单）
  6. **语义动作测试**：菜单键(1) → 变化；BACKSPACE 返回
  7. 关闭游戏（Alt+F4，除非 --keep）

用法::

    python -m sg_bridge.tools.verify_games                 # 验证全部四作
    python -m sg_bridge.tools.verify_games --games SGLBP,SGMDE
    python -m sg_bridge.tools.verify_games --game SG0 --keep
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "out", "verify"))

from .. import api                                   # noqa: E402
from ..core import games as G                        # noqa: E402
from ..core import ocr as OCR                        # noqa: E402
from ..core import wininput as W                     # noqa: E402
from .shot_diff import compare                       # noqa: E402


def _shot(path: str, game: str) -> str:
    api.call("focus", {"target": game})
    api.call("screenshot", {"path": path, "target": game, "client_only": True})
    return path


def _start(game: str) -> str:
    """启动游戏：优先 Game.exe，失败再用 Launcher（若是启动器，点一下左上角按钮）。"""
    info = G.game_info(game)
    exe_dir = info["dir"]
    started = "none"
    for name in ("Game.exe", "Launcher.exe", "launcher.exe"):
        path = os.path.join(exe_dir, name)
        if not os.path.exists(path):
            continue
        try:
            subprocess.Popen([path], cwd=exe_dir)
            started = name
            time.sleep(4)
            if name.lower().startswith("launcher"):
                win = W.find_window(process_contains=name)
                if win:
                    W.focus_window(win["hwnd"])
                    W.click_norm(win["hwnd"], 0.16, 0.14)
            break
        except Exception:
            continue
    return started


def _wait_window(game: str, seconds: int = 60) -> "dict | None":
    deadline = time.time() + seconds
    while time.time() < deadline:
        wins = G.find_game_windows(game)
        if wins and wins[0]["client_size"][0] > 200:
            return wins[0]
        time.sleep(2)
    return None


def _dismiss_exit_dialog(game: str) -> bool:
    """若屏幕上是"退出确认"对话框，**按文字找到「取消/Cancel」并点掉它**（各作弹窗不同）"""
    path = _shot(os.path.join(OUT_DIR, f"{game}_dlg.png"), game)
    lines = []
    for lang in ("zh-Hans-CN", "en-US"):
        lines += OCR.read_text(path, scale=1.5, lang=lang)["lines"]
    target = None
    for ln in lines:
        t = ln["text"]
        if any(k in t for k in ("Cancel", "取消", "Cance1", "CanCel")):
            target = ln
            break
    is_dialog = any(("Exit" in ln["text"]) or ("退出" in ln["text"]) or ("lost" in ln["text"])
                    or ("未保存" in ln["text"]) for ln in lines)
    if not target and is_dialog:
        # 没找到取消按钮：退而求其次按 ESC
        api.call("key_press", {"key": "escape", "ms": 40})
        time.sleep(1.2)
        return True
    if target:
        hwnd = G.resolve_window(game)["hwnd"]
        cx, cy, _, _ = W.window_info(hwnd)["client_rect"]
        x, y, w, h = target["bbox"]
        W.focus_window(hwnd)
        W.click(cx + x + w // 2, cy + y + h // 2)
        time.sleep(1.5)
        return True
    return False


def _close(game: str, tries: int = 3) -> bool:
    """稳健关闭：Alt+F4 → 若仍存在则 ENTER（确认"退出？"对话框）→ 复查"""
    for _ in range(max(1, tries)):
        if not G.find_game_windows(game):
            return True
        api.call("focus", {"target": game})
        api.call("key_combo", {"keys": ["alt", "f4"]})
        time.sleep(2.2)
        if not G.find_game_windows(game):
            return True
        api.call("key_press", {"key": "enter", "ms": 40})
        time.sleep(2.0)
    return not bool(G.find_game_windows(game))


def verify(game: str, keep: bool = False, wait: int = 60) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    res: dict = {"game": game, "checks": {}}
    info = G.game_info(game)
    res["exe"] = info["exe"]
    res["exe_exists"] = info["exe_exists"]
    res["already_running"] = bool(G.find_game_windows(game))

    if not res["already_running"]:
        res["started_via"] = _start(game)
    win = _wait_window(game, wait)
    res["window"] = ({"hwnd": win["hwnd"], "title": win["title"],
                      "client_size": win["client_size"]} if win else None)
    res["checks"]["window"] = bool(win)
    if not win:
        res["verdict"] = "找不到窗口（未启动成功？）"
        return res

    api.call("attach", {"target": game, "focus": True})
    time.sleep(1.0)
    res["had_exit_dialog"] = _dismiss_exit_dialog(game)   # 先清掉可能的"退出？"弹窗

    # ① 按键响应
    a = _shot(os.path.join(OUT_DIR, f"{game}_a.png"), game)
    api.call("key_press", {"key": "enter", "ms": 40})
    time.sleep(3.0)
    b = _shot(os.path.join(OUT_DIR, f"{game}_b.png"), game)
    d1 = compare(a, b)
    res["enter_changed"] = d1["changed_ratio"]
    res["checks"]["key_enter"] = d1["changed_ratio"] > 0.002

    # ② 读屏（OCR）—— 按作品语言选 OCR 语言；失败换语言/重拍几次，取读到最多的那次
    lang = "en-US" if G.GAMES.get(game, {}).get("lang") == "en" else "zh-Hans-CN"
    best, best_lang = None, lang
    for attempt in range(3):
        if attempt:
            time.sleep(2.0)
            b = _shot(os.path.join(OUT_DIR, f"{game}_b{attempt}.png"), game)
        for cand in (lang, "zh-Hans-CN" if lang == "en-US" else "en-US"):
            r = OCR.read_text(b, scale=2.0, lang=cand)
            if best is None or len(r["lines"]) > len(best["lines"]):
                best, best_lang = r, cand
        if best and best["lines"]:
            break
    res["ocr_lang"] = best_lang
    res["ocr_tries"] = attempt + 1
    res["ocr_lines"] = [ln["text"] for ln in best["lines"]][:12]
    res["checks"]["ocr"] = bool(best["lines"])

    # ③ 语义动作：系统菜单(1)
    c = _shot(os.path.join(OUT_DIR, f"{game}_c.png"), game)
    api.call("game_action", {"action": "menu", "game": game})
    time.sleep(1.5)
    d = _shot(os.path.join(OUT_DIR, f"{game}_d.png"), game)
    d2 = compare(c, d)
    res["menu_changed"] = d2["changed_ratio"]
    res["checks"]["menu_key"] = d2["changed_ratio"] > 0.002
    api.call("key_press", {"key": "backspace", "ms": 40})
    time.sleep(1.2)

    # ④ IME 状态
    st = api.call("ime_status", {"target": game})
    res["ime"] = st.get("data", {}) if st.get("ok") else st

    if not keep:
        res["closed"] = _close(game)

    passed = sum(1 for v in res["checks"].values() if v)
    res["passed"] = f"{passed}/{len(res['checks'])}"
    res["verdict"] = "可用 ✓" if all(res["checks"].values()) else "部分失败（见 checks）"
    return res


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    games, keep, wait = list(G.GAMES.keys()), False, 60
    i = 0
    while i < len(argv):
        if argv[i] == "--games":
            games = [g.strip().upper() for g in argv[i + 1].split(",")]; i += 2
        elif argv[i] == "--game":
            games = [argv[i + 1].strip().upper()]; i += 2
        elif argv[i] == "--keep":
            keep = True; i += 1
        elif argv[i] == "--wait":
            wait = int(argv[i + 1]); i += 2
        else:
            i += 1

    results = []
    for g in games:
        print(f"\n===== 验证 {g} =====")
        r = verify(g, keep=keep, wait=wait)
        results.append(r)
        print(f"  exe={r['exe_exists']}  window={r['checks'].get('window')}  "
              f"enter={r['checks'].get('key_enter')}  ocr={r['checks'].get('ocr')}  "
              f"menu={r['checks'].get('menu_key')}  → {r['verdict']} ({r.get('passed')})")
        if r.get("ocr_lines"):
            print("    OCR 样例: " + " / ".join(r["ocr_lines"][:5]))

    print("\n===== 汇总 =====")
    for r in results:
        print(f"  {r['game']:<7} {r.get('passed', '-'):<5} {r['verdict']}")

    path = os.path.join(OUT_DIR, "verify_result.json")
    # 累积保存：每次只更新当次验证的作品，保留其它作品的记录
    merged: dict = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for item in json.load(fh):
                    merged[item.get("game")] = item
        except Exception:
            merged = {}
    for r in results:
        merged[r["game"]] = r
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([merged[k] for k in sorted(merged)], fh, ensure_ascii=False, indent=2)
    print(f"\n明细（累积）: {path}")
    ok = all(all(r["checks"].values()) for r in results if r.get("checks"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
