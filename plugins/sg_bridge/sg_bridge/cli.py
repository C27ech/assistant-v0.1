"""sg_bridge.cli — 命令行快速测试（不开 HTTP 也能验证接口）

示例::

    python -m sg_bridge.cli tools
    python -m sg_bridge.cli games
    python -m sg_bridge.cli windows --title Game
    python -m sg_bridge.cli attach SG0
    python -m sg_bridge.cli press enter
    python -m sg_bridge.cli hold ctrl 2000
    python -m sg_bridge.cli click-norm 0.5 0.9
    python -m sg_bridge.cli action advance --game SG0
    python -m sg_bridge.cli bindings --game SG
    python -m sg_bridge.cli call key_press key=enter ms=40
    python -m sg_bridge.cli serve --port 8765
"""
from __future__ import annotations

import argparse
import json
import sys

from . import api


def _parse_value(text: str):
    """把 ``k=v`` 里的 v 解析成 JSON（失败则当字符串）。"""
    try:
        return json.loads(text)
    except Exception:
        return text


def _print(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _run(name: str, args: dict) -> int:
    res = api.call(name, args)
    _print(res)
    return 0 if res.get("ok") else 1


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="python -m sg_bridge.cli", add_help=True,
                                     description="SG Bridge 命令行")
    parser.add_argument("--game", default=None, help="作品 key：SG/SG0/SGLBP/SGMDE")
    parser.add_argument("--target", default=None, help="窗口句柄或标题片段")
    parser.add_argument("--ms", type=int, default=None, help="时长覆盖（快进等）")
    parser.add_argument("cmd", nargs="?", default="help")
    parser.add_argument("rest", nargs="*")
    ns = parser.parse_args(argv)

    cmd, rest = ns.cmd, ns.rest

    if cmd in ("help", "-h", "--help"):
        print(__doc__)
        return 0
    if cmd == "tools":
        _print({"count": len(api.TOOLS), "tools": [t["name"] for t in api.TOOLS]})
        return 0
    if cmd == "games":
        return _run("list_games", {})
    if cmd == "windows":
        p = dict(kv.split("=", 1) for kv in rest if "=" in kv)
        return _run("list_windows", {k: _parse_value(v) for k, v in p.items()})
    if cmd == "attach":
        if not rest:
            print("用法: attach <SG|SG0|SGLBP|SGMDE|hwnd|标题片段>")
            return 2
        return _run("attach", {"target": rest[0]})
    if cmd == "status":
        return _run("status", {})
    if cmd == "ping":
        return _run("ping", {})
    if cmd == "press":
        if not rest:
            print("用法: press <key> [ms]")
            return 2
        ms = int(rest[1]) if len(rest) > 1 else (ns.ms or 40)
        return _run("key_press", {"key": rest[0], "ms": ms})
    if cmd == "down":
        return _run("key_down", {"key": rest[0]})
    if cmd == "up":
        return _run("key_up", {"key": rest[0]})
    if cmd == "hold":
        if len(rest) < 2:
            print("用法: hold <key> <ms>")
            return 2
        return _run("key_hold", {"key": rest[0], "ms": int(rest[1])})
    if cmd == "combo":
        return _run("key_combo", {"keys": rest})
    if cmd == "click":
        if len(rest) < 2:
            print("用法: click <x> <y> [left|right|middle]")
            return 2
        args = {"x": int(rest[0]), "y": int(rest[1])}
        if len(rest) > 2:
            args["button"] = rest[2]
        return _run("click", args)
    if cmd == "click-norm":
        if len(rest) < 2:
            print("用法: click-norm <nx> <ny> [left|right|middle]")
            return 2
        args = {"nx": float(rest[0]), "ny": float(rest[1])}
        if len(rest) > 2:
            args["button"] = rest[2]
        if ns.target:
            args["target"] = ns.target
        return _run("click_norm", args)
    if cmd == "scroll":
        return _run("scroll", {"amount": int(rest[0]) if rest else 120})
    if cmd == "bindings":
        return _run("game_bindings", {"game": ns.game})
    if cmd == "action":
        if not rest:
            print("用法: action <advance|confirm|menu|skip|...> [--game SG0] [--ms 2000]")
            return 2
        args = {"action": rest[0], "game": ns.game}
        if ns.ms is not None:
            args["ms"] = ns.ms
        return _run("game_action", args)
    if cmd == "seq":
        steps = json.loads(rest[0])
        return _run("sequence", {"steps": steps, "game": ns.game})
    if cmd == "call":
        if not rest:
            print("用法: call <tool> [k=v ...]")
            return 2
        name, pairs = rest[0], rest[1:]
        args = {}
        for kv in pairs:
            if "=" in kv:
                k, v = kv.split("=", 1)
                args[k] = _parse_value(v)
        return _run(name, args)
    if cmd == "serve":
        from .http_server import main as http_main
        sys.argv = ["http_server"] + [a for a in (ns.rest or [])]
        http_main()
        return 0

    print(f"未知命令: {cmd}\n")
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
