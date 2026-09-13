# -*- coding: utf-8 -*-
"""隐藏 / 显示指定标题的窗口（SW_HIDE / SW_SHOW，可逆）。"""
import ctypes
import sys

user32 = ctypes.windll.user32


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else ""
    mode = sys.argv[2] if len(sys.argv) > 2 else "hide"
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        print("NOT_FOUND", repr(title))
        return 1
    if mode == "show":
        user32.ShowWindow(hwnd, 5)  # SW_SHOW
        print("SHOWN", int(hwnd))
    else:
        user32.ShowWindow(hwnd, 0)  # SW_HIDE
        print("HIDDEN", int(hwnd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
