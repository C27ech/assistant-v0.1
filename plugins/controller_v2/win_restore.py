# -*- coding: utf-8 -*-
"""还原并前置指定标题/句柄的窗口（只读操作，用于观测 Steam 状态）。"""
import ctypes
import sys

user32 = ctypes.windll.user32


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else ""
    hwnd = None
    if title:
        hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        print("NOT_FOUND", repr(title))
        return 1

    # SW_RESTORE=9
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)
    # 如果最小化，先解除
    user32.ShowWindow(hwnd, 9)
    print("RESTORED", int(hwnd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
