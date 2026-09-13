# -*- coding: utf-8 -*-
"""只读脚本：枚举所有顶层窗口（含隐藏/最小化），打印句柄/标题/类名/矩形/可见性/最小化。"""
import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def main():
    rows = []
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def cb(hwnd, _lp):
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        rect = RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        rows.append({
            "hwnd": int(hwnd),
            "title": title,
            "class": cls.value,
            "rect": [rect.left, rect.top, rect.right, rect.bottom],
            "vis": bool(user32.IsWindowVisible(hwnd)),
            "iconic": bool(user32.IsIconic(hwnd)),
        })
        return 1

    user32.EnumWindows(proc_type(cb), 0)

    for r in rows:
        t = r["title"]
        c = r["class"]
        if ("steam" in t.lower()) or ("steam" in c.lower()) or ("sdl" in c.lower()) or \
           ("vgui" in c.lower()) or ("slay" in t.lower()) or ("spire" in t.lower()):
            print("HWND %d | %r | %s | rect=%s | vis=%s min=%s" % (
                r["hwnd"], t, c, r["rect"], r["vis"], r["iconic"]))


if __name__ == "__main__":
    main()
