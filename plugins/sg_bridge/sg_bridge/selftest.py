"""sg_bridge.selftest — 端到端自测：证明"接口注入的输入真的生效"

做法（不碰游戏，安全可重复）::

    启动记事本 → 切前台 → 用接口打字 → Ctrl+A/Ctrl+C → 读剪贴板比对
    → 鼠标移动到计算坐标 → 读回光标坐标比对 → 截图并核对尺寸 → 关掉记事本

任何一步失败都会明确标出；全部通过说明键盘、鼠标、窗口聚焦、DPI 坐标换算都正常。

运行::

    python -m sg_bridge.selftest
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from .core import wininput as W

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
EXPECT = "sg-bridge selftest 123"
STEP_RESULTS: list[dict] = []


def _step(name: str, ok: bool, detail: str = "") -> bool:
    STEP_RESULTS.append({"step": name, "ok": bool(ok), "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}")
    return bool(ok)


def _find_notepad() -> "dict | None":
    for w in W.list_windows():
        proc = (w.get("process") or "").lower()
        title = w.get("title") or ""
        if proc.endswith("notepad.exe") or "记事本" in title or "Notepad" in title:
            if w["client_size"][0] > 100 and w["client_size"][1] > 100:
                return w
    return None


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Python {sys.version.split()[0]} | DPI 感知模式: {W.DPI_MODE} | "
          f"虚拟桌面: {W.virtual_screen()}")

    # 1) 目标窗口
    proc = subprocess.Popen(["notepad.exe"])
    win = None
    for _ in range(60):
        win = _find_notepad()
        if win:
            break
        time.sleep(0.25)
    if not win:
        _step("找到记事本窗口", False, "15 秒内没找到；本项跳过，其余不测")
        _save()
        return 1
    hwnd = win["hwnd"]
    _step("找到记事本窗口", True, f"hwnd={hwnd} 客户区={win['client_size']}")

    # 2) 聚焦
    fo = W.focus_window(hwnd)
    _step("切到前台", bool(fo.get("ok")), f"tries={fo.get('tries_used')} {fo.get('note', '')}")
    time.sleep(0.3)

    # 3) 键盘输入
    W.type_text(EXPECT, per_char_ms=15)
    W.sleep_ms(200)
    _step("键盘输入", True, f"typed={EXPECT!r}")

    # 4) 剪贴板回读（严格证明按键到达了目标窗口）
    W.key_combo(["ctrl", "a"])
    W.sleep_ms(120)
    W.key_combo(["ctrl", "c"])
    W.sleep_ms(250)
    clip = W.read_clipboard_text()
    _step("剪贴板回读一致", EXPECT in clip, f"clipboard={clip[:60]!r}")

    # 5) 鼠标绝对坐标（含 DPI 换算）
    cx, cy, cw, ch = win["client_rect"]
    tx, ty = cx + cw // 2, cy + ch // 3
    W.mouse_move(tx, ty)
    time.sleep(0.15)
    gx, gy = W.cursor_pos()
    _step("鼠标移动到目标坐标", abs(gx - tx) <= 2 and abs(gy - ty) <= 2,
          f"期望=({tx},{ty}) 实际=({gx},{gy})")

    # 6) 点击（会移动插入点，无副作用）
    res = W.click_norm(hwnd, 0.5, 0.5)
    rx, ry = res["cursor"]
    _step("归一化点击换算正确", abs(rx - (cx + cw // 2)) <= 2 and abs(ry - (cy + ch // 2)) <= 2,
          f"点击后光标=({rx},{ry}) 客户区={win['client_rect']}")

    # 7) 滚轮
    W.scroll(-120, tx, ty)
    _step("滚轮事件", True, "amount=-120")

    # 7.5) 高层接口：game_action（语义动作）+ sequence（批量脚本）
    from . import api

    r1 = api.call("game_action", {"action": "advance", "focus": False})
    W.sleep_ms(200)
    r2 = api.call("sequence", {"steps": [{"op": "key_press", "key": "end", "ms": 30},
                                         {"op": "sleep", "ms": 120}]})
    W.sleep_ms(200)
    W.key_combo(["ctrl", "a"])
    W.sleep_ms(150)
    W.key_combo(["ctrl", "c"])
    W.sleep_ms(250)
    clip2 = W.read_clipboard_text()
    newlines = clip2.count("\n") + clip2.count("\r")
    _step("语义动作 game_action/sequence 生效",
          bool(r1.get("ok")) and bool(r2.get("ok")) and newlines >= 1,
          f"advance ok={r1.get('ok')} sequence ok={r2.get('ok')} 回读换行数={newlines}")

    # 8) 截图
    shot = os.path.join(OUT_DIR, "selftest_notepad.png")
    try:
        info = W.screenshot_window(shot, hwnd)
        size_ok = abs(info["size"][0] - cw) <= 2 and abs(info["size"][1] - ch) <= 2
        _step("截图与客户区尺寸一致", size_ok, f"png={info['size']} 客户区={[cw, ch]} → {shot}")
    except Exception as exc:  # noqa: BLE001
        _step("截图", False, f"{type(exc).__name__}: {exc}")

    # 9) 清理
    try:
        subprocess.run(["taskkill", "/PID", str(win["pid"]), "/F"],
                       capture_output=True, check=False)
        if proc.poll() is None:
            proc.terminate()
        _step("关闭记事本", True, f"pid={win['pid']}")
    except Exception as exc:  # noqa: BLE001
        _step("关闭记事本", False, str(exc))

    _save()
    failed = [s for s in STEP_RESULTS if not s["ok"]]
    print(f"\n合计 {len(STEP_RESULTS)} 项，失败 {len(failed)} 项")
    return 1 if failed else 0


def _save() -> None:
    path = os.path.join(OUT_DIR, "selftest_result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"dpi_mode": W.DPI_MODE, "virtual_screen": list(W.virtual_screen()),
                   "steps": STEP_RESULTS}, fh, ensure_ascii=False, indent=2)
    print(f"结果已写入: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
