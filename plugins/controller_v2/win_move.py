# -*- coding: utf-8 -*-
"""移动指定标题窗口到指定屏幕坐标（可逆，不改变大小）。"""
import ctypes
import sys

user32 = ctypes.windll.user32


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else ""
    x = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    y = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        print("NOT_FOUND", repr(title))
        return 1
    # SWP_NOZORDER(0x0004) | SWP_NOSIZE(0x0001)
    user32.SetWindowPos(hwnd, 0, x, y, 0, 0, 0x0004 | 0x0001)
    print("MOVED", int(hwnd), "to", x, y)
    return 0


if __name__ == "__main__":
    sys.exit(main())
