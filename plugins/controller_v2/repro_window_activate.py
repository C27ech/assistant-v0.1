# -*- coding: utf-8 -*-
"""复现 window_activate 失败，抓取原始 traceback，不吞异常。"""
import json
import sys
import traceback

import win32gui

from router.channels import _handle_window_activate


def fg_title():
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return None, None
    return int(hwnd), win32gui.GetWindowText(hwnd)


def main():
    title = sys.argv[1] if len(sys.argv) > 1 else "Steam"
    action = {
        "type": "interface",
        "channel": "win32",
        "name": "window_activate",
        "args": {"window_title": title},
    }
    print("before fg:", fg_title())
    try:
        result = _handle_window_activate(action)
        print("result:", json.dumps(result, ensure_ascii=False))
    except Exception:
        traceback.print_exc()
        return 1
    print("after fg:", fg_title())
    return 0


if __name__ == "__main__":
    sys.exit(main())
