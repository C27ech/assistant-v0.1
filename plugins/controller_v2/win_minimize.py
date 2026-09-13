# -*- coding: utf-8 -*-
"""最小化指定标题的窗口。"""
import ctypes
import sys

user32 = ctypes.windll.user32


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else ""
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        print("NOT_FOUND", repr(title))
        return 1
    user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
    print("MINIMIZED", int(hwnd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
