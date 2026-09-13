# -*- coding: utf-8 -*-
"""稳健前台化：AttachThreadInput 绕过前台锁 + SetWindowPos 临时置顶。"""
import ctypes
import sys
import time

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040


def _find(title):
    hwnd = user32.FindWindowW(None, title)
    if hwnd:
        return hwnd
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

    # 恢复（若最小化）
    user32.ShowWindow(hwnd, 9)

    # 临时置顶，确保可见
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    time.sleep(0.2)

    # AttachThreadInput 绕过前台锁定
    fg = user32.GetForegroundWindow()
    our_tid = kernel32.GetCurrentThreadId()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)

    attached = []
    if fg_tid and fg_tid != our_tid:
        if user32.AttachThreadInput(our_tid, fg_tid, True):
            attached.append(fg_tid)
    if target_tid and target_tid != our_tid:
        if user32.AttachThreadInput(our_tid, target_tid, True):
            attached.append(target_tid)

    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    user32.SetActiveWindow(hwnd)

    time.sleep(0.3)

    for tid in attached:
        user32.AttachThreadInput(our_tid, tid, False)

    # 取消置顶，恢复正常 z 序
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)

    fg_now = user32.GetForegroundWindow()
    print("FOCUSED", int(hwnd), "foreground_now", int(fg_now) if fg_now else None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
