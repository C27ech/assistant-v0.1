# -*- coding: utf-8 -*-
"""把指定标题窗口设为/取消置顶（HWND_TOPMOST / HWND_NOTOPMOST）。"""
import ctypes
import sys

user32 = ctypes.windll.user32
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else ""
    mode = sys.argv[2] if len(sys.argv) > 2 else "top"
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        print("NOT_FOUND", repr(title))
        return 1
    target = HWND_TOPMOST if mode == "top" else HWND_NOTOPMOST
    user32.SetWindowPos(hwnd, target, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    print(("TOPMOST" if mode == "top" else "NOTOPMOST"), int(hwnd))
    return 0


if __name__ == "__main__":
    sys.exit(main())
