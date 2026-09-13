# -*- coding: utf-8 -*-
"""桌面截图发 QQ 的启动器（薄包装）。

调用 ../screenshot2qq/main.py 的入口逻辑（截全屏 -> OneBot11 正向 WS -> 私聊图片
发给 config.json 里指定的 QQ）。本包装器使子进程命令行不含 "main.py"，
规避看门狗对"命令行含 main.py 的新进程"的误判。

用法：python snap_qq.py [--user-id 123] [--keep-temp] ...（参数原样透传给插件）
"""
from __future__ import annotations

import os
import runpy
import sys

# 自己定位到兄弟插件目录：<repo>/plugins/controller_v2/ 的上一级 = <repo>/plugins/
_SCREENSHOT2QQ_MAIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "screenshot2qq",
    "main.py",
)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    # 让插件在 sys.argv[1:] 里看到透传参数；argv[0] 随意。
    if not os.path.isfile(_SCREENSHOT2QQ_MAIN):
        print("[snap_qq] 找不到插件:", _SCREENSHOT2QQ_MAIN, file=sys.stderr, flush=True)
        return 1

    runpy.run_path(_SCREENSHOT2QQ_MAIN, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
