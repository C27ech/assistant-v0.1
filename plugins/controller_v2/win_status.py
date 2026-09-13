# -*- coding: utf-8 -*-
"""打印指定 hwnd 的矩形/可见性/最小化，以及当前前台窗口。"""
import ctypes
import sys

user32 = ctypes.windll.user32


class RECT(ctypes.Structure):
    _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                ("r", ctypes.c_long), ("b", ctypes.c_long)]


def main():
    for arg in sys.argv[1:]:
        h = int(arg)
        rect = RECT()
        user32.GetWindowRect(h, ctypes.byref(rect))
        print("HWND", h, "rect", [rect.l, rect.t, rect.r, rect.b],
              "vis", bool(user32.IsWindowVisible(h)),
              "iconic", bool(user32.IsIconic(h)))
    print("foreground", user32.GetForegroundWindow())
    return 0


if __name__ == "__main__":
    sys.exit(main())
