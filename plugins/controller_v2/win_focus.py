# -*- coding: utf-8 -*-
"""把指定标题的窗口恢复到前台（带 Alt 键技巧，提高 SetForegroundWindow 成功率）。"""
import ctypes
import sys
import time

user32 = ctypes.windll.user32


def _find(title):
    hwnd = user32.FindWindowW(None, title)
    if hwnd:
        return hwnd
    # 部分匹配
    proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
    found = []

    def cb(h, _l):
        n = user32.GetWindowTextLengthW(h)
        if n <= 0:
            return 1
        b = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(h, b, n + 1)
        if title.lower() in b.value.lower():
            found.append(h)
        return 1

    user32.EnumWindows(proc(cb), 0)
    return found[0] if found else None


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else ""
    hwnd = _find(title)
    if not hwnd:
        print("NOT_FOUND", repr(title))
        return 1

    SW_RESTORE = 9
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.2)

    # Alt 键技巧：先发一个 Alt 空击，绕过前台锁定限制。
    keybd = ctypes.windll.user32.keybd_event
    keybd(0x12, 0, 0, 0)   # VK_MENU down
    keybd(0x12, 0, 2, 0)   # VK_MENU up
    time.sleep(0.1)

    user32.SetForegroundWindow(hwnd)
    time.sleep(0.3)
    user32.ShowWindow(hwnd, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    print("FOCUSED", int(hwnd), "foreground_now", int(fg) if fg else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
