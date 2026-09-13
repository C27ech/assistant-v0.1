"""router C 层自测：py_compile + 最小导入 + 危险护栏 + AI 语义路由。

用法：在工作目录执行 ``python -m router.self_test``。

AI 用例需要有效 deepseek key（env -> secrets.json -> config.json）；无 key 时
自动跳过 AI 网络断言，仅验证危险护栏与降级结构。
"""

from __future__ import annotations

import py_compile
from pathlib import Path


def main() -> None:
    here = Path(__file__).resolve().parent

    # 1) 语法编译自测。
    for filename in ("__init__.py", "router.py", "channels.py", "ai_router.py"):
        py_compile.compile(str(here / filename), doraise=True)
        print(f"[py_compile] OK: router/{filename}")

    # 2) 最小导入自测。
    from router import RouteDecision, channels, route  # noqa: F401
    from core.config import resolve_vision_api_key

    assert RouteDecision is not None
    assert callable(route)
    assert len(channels.CHANNELS) >= 11
    assert channels.get_channel("window_activate") is not None
    print(f"[import] OK: 预置接口通道数 = {len(channels.CHANNELS)}（含 window_activate）")

    # 3) 危险操作硬护栏：必须拦截，不交给 AI。
    blocked = route("删掉某个文件")
    assert blocked.channel == "blocked", blocked
    assert blocked.interface_name == "file_remove", blocked
    print(f"[guard] OK: 删除文件被拦截 reason={blocked.reason}")

    blocked_dir = route("新建文件夹 router_self_test_dir")
    assert blocked_dir.channel == "blocked", blocked_dir
    assert blocked_dir.interface_name == "dir_create", blocked_dir
    print(f"[guard] OK: 建目录被拦截 reason={blocked_dir.reason}")

    # 4) 空任务 -> vision。
    empty_decision = route("")
    assert empty_decision.channel == "vision", empty_decision
    print("[route] OK: 空任务降级 vision")

    # 5) AI 语义路由（需要 key；无 key 则跳过）。
    api_key = resolve_vision_api_key()
    if not api_key:
        print("[route] SKIP AI 用例：未解析到 deepseek API Key")
        print("ALL SELF TESTS PASSED (AI cases skipped)")
        return

    activate = route("把 Steam 切到前台")
    print(
        f"[route] 样例1 channel={activate.channel} "
        f"interface_name={activate.interface_name} params={activate.params} "
        f"reason={activate.reason}"
    )
    assert activate.channel == "interface", activate
    assert activate.interface_name == "window_activate", activate
    assert isinstance(activate.params, dict), activate
    assert activate.params.get("window_title"), activate

    search = route("在游戏库搜索 Slay the Spire 2")
    print(
        f"[route] 样例2 channel={search.channel} prompt={search.prompt} "
        f"reason={search.reason}"
    )
    assert search.channel == "vision", search
    assert isinstance(search.prompt, str) and search.prompt.strip(), search

    print("ALL SELF TESTS PASSED")


if __name__ == "__main__":
    main()
