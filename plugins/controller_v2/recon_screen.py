# -*- coding: utf-8 -*-
"""临时侦察脚本（只读，不改动 controller_v2 任何业务文件）。

用途：在不改动业务代码的前提下，复用 controller_v2 现有能力做「看屏幕」：
  1) core.screen.capture_screenshot 截当前屏幕（DPI 感知，与执行层同一时钟源）；
  2) ctypes EnumWindows 枚举可见顶层窗口（标题/类名/矩形），判断属性窗口是否存在；
  3) 如带参数 question，则复用 vision_exec.executor._chat（deepseek v4.1 视觉请求
     机制）就当前截图做一次自由问答，只返回文本。

用法：
    python recon_screen.py ["自由问答文本"] [--out 截图路径]
"""
from __future__ import annotations

import ctypes
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.screen import (  # noqa: E402
    capture_screenshot,
    describe_screen,
    get_screen_profile,
)


def _list_windows() -> list:
    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    rows = []

    proc_type = ctypes.WINFUNCTYPE(
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p
    )

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return 1
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return 1
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        rows.append(
            {
                "hwnd": int(hwnd),
                "title": title,
                "class": cls.value,
                "rect": [rect.left, rect.top, rect.right, rect.bottom],
            }
        )
        return 1

    callback = proc_type(_cb)
    user32.EnumWindows(callback, 0)
    return rows


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    args = sys.argv[1:]
    out = None
    if "--out" in args:
        idx = args.index("--out")
        out = args[idx + 1]
        args = args[:idx] + args[idx + 2:]

    task = " ".join(args).strip()

    # 屏幕画像（分辨率 / 缩放 / 多显示器布局）：坐标换算与截图都以此为准。
    profile = get_screen_profile(force=True)
    print("SCREEN " + describe_screen(profile).replace("\n", " | "))
    for monitor in profile["monitors"]:
        print(
            "MONITOR {index} rect={rect} work={work} dpi={dpi} scale={scale} primary={primary}".format(
                index=monitor["index"],
                rect=monitor["rect"],
                work=monitor["work_rect"],
                dpi=monitor["dpi"],
                scale=monitor["scale"],
                primary=monitor["primary"],
            )
        )

    shot = capture_screenshot(out)
    print("SCREENSHOT " + shot)

    for row in _list_windows():
        print(
            "WINDOW {hwnd} | {title} | {cls} | {rect}".format(
                hwnd=row["hwnd"],
                title=row["title"],
                cls=row["class"],
                rect=row["rect"],
            )
        )

    if task:
        from vision_exec.executor import _chat_single

        answer = _chat_single(shot, task, tier="pro")
        print("VISION_ANSWER_BEGIN")
        print(answer)
        print("VISION_ANSWER_END")

    return 0


if __name__ == "__main__":
    sys.exit(main())

def print_foreground() -> None:
    """打印当前前台窗口（供安全确认 window_close 之类接口通道的作用目标）。"""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    cls = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, cls, 256)
    print(f"FOREGROUND {int(hwnd)} | {buf.value} | {cls.value}")


def print_all_windows() -> None:
    """打印所有顶层窗口（含隐藏 / 最小化），带可见性与最小化状态。"""
    user32 = ctypes.windll.user32

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def _cb(hwnd, _lparam):
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return 1
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if "steam" not in title.lower():
            return 1
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        print(
            "ALL {hwnd} | {title} | visible={vis} iconic={ico} | {rect}".format(
                hwnd=int(hwnd),
                title=title,
                vis=bool(user32.IsWindowVisible(hwnd)),
                ico=bool(user32.IsIconic(hwnd)),
                rect=[rect.left, rect.top, rect.right, rect.bottom],
            )
        )
        return 1

    callback = proc_type(_cb)
    user32.EnumWindows(callback, 0)
