"""sg_bridge.tools.phone_flow — 驱动/测绘"手机"界面流程（不依赖看图）

每一步都会：按键 → 等稳定 → 截图 → 与上一步做像素差 + 算屏幕指纹，
从而在**完全不开图**的情况下得到：手机界面的导航图（哪些键能切到哪个界面）。

步骤 DSL（逗号分隔）::

    open / close            # 打开 / 关闭手机（E / BACKSPACE）
    up / down / left / right
    enter / back
    key:<键名>              # 任意键，如 key:f4
    sleep:<毫秒>

示例::

    python -m sg_bridge.tools.phone_flow --game SG --steps open,down,down,enter,back,back
    python -m sg_bridge.tools.phone_flow --game SG --steps open,right,right,right,enter,back
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "out", "phone"))

from .. import api                                # noqa: E402
from .shot_diff import average, compare, fingerprint   # noqa: E402

KEY_ALIASES = {
    "open": "e", "close": "backspace", "enter": "enter", "back": "backspace",
    "up": "up", "down": "down", "left": "left", "right": "right",
}


def _shot(name: str, game: str) -> str:
    path = os.path.join(OUT_DIR, f"{name}.png")
    res = api.call("screenshot", {"path": path, "target": game, "client_only": True})
    if not res.get("ok"):
        raise RuntimeError(
            f"截图失败（{res.get('error', {}).get('code')}: {res.get('error', {}).get('message')}）"
            f" —— 游戏窗口是否还在运行？可先跑 `python -m sg_bridge.cli games` 确认")
    if not os.path.exists(path):
        raise RuntimeError(f"截图文件未生成: {path}")
    return path


def _parse_steps(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for raw in text.split(","):
        token = raw.strip()
        if not token:
            continue
        if token.startswith("key:"):
            out.append(("key", token[4:]))
        elif token.startswith("sleep:"):
            out.append(("sleep", token[6:]))
        elif token in KEY_ALIASES:
            out.append(("key", KEY_ALIASES[token]))
        else:
            raise ValueError(f"未知步骤: {token!r}")
    return out


def run(game: str, steps_text: str, settle: float = 1.6, prefix: str = "",
        avg: int = 3) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    steps = _parse_steps(steps_text)
    api.call("attach", {"target": game, "focus": True})
    time.sleep(0.4)

    shots: list[dict] = []

    def take(tag: str) -> dict:
        """拍 ``avg`` 帧并平均，得到抗动态背景的稳定帧。"""
        idx = len(shots)
        raw: list[str] = []
        for k in range(max(1, avg)):
            raw.append(_shot(f"{prefix}{idx:02d}_{tag}_r{k}", game))
            if k + 1 < avg:
                time.sleep(0.28)
        path = os.path.join(OUT_DIR, f"{prefix}{idx:02d}_{tag}_avg.png")
        if avg > 1:
            average(raw, path)
        else:
            path = raw[0]
        fp = fingerprint(path)
        return {"tag": tag, "path": path, "hash": fp["hash"], "mean": fp["mean"],
                "raw": raw}

    screens: dict[str, str] = {}          # hash → 屏幕名
    first = take("base")
    screens.setdefault(first["hash"], "S0")
    first["screen"] = screens[first["hash"]]
    shots.append(first)
    print(f"  {'步骤':<14}{'变化%':>8}  {'变化区域':<24}{'屏幕':<6}说明")

    for kind, arg in steps:
        label = f"{kind}:{arg}" if kind != "key" else arg
        if kind == "sleep":
            time.sleep(int(arg) / 1000.0)
            continue
        api.call("key_press", {"key": arg, "ms": 40})
        time.sleep(settle)
        cur = take(arg)
        prev = shots[-1]
        diff = compare(prev["path"], cur["path"])
        name = screens.setdefault(cur["hash"], f"S{len(screens)}")
        cur["screen"] = name
        cur["changed_ratio"] = diff["changed_ratio"]
        cur["bbox"] = diff["changed_bbox"]
        shots.append(cur)
        if diff["changed_ratio"] < 0.004:
            arrow = "（无变化 → 该键在此界面无效）"
        elif name == prev.get("screen"):
            arrow = "（同一界面，内容有变动）"
        elif name in {s.get("screen") for s in shots[:-1]}:
            arrow = f"（回到见过的界面 {name}）"
        else:
            arrow = f"（{prev.get('screen')} → {name} 新界面）"
        print(f"  {label:<14}{diff['changed_ratio'] * 100:7.2f}%  "
              f"{str(diff['changed_bbox']):<24}{name:<6}{arrow}")

    result = {
        "game": game, "steps": steps_text, "settle": settle, "avg": avg,
        "screens": screens,
        "timeline": [{k: v for k, v in s.items() if k not in ("mean", "raw")} for s in shots],
    }
    path = os.path.join(OUT_DIR, f"{prefix}result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(f"\n界面指纹表: {json.dumps(screens, ensure_ascii=False)}")
    print(f"结果: {path}")
    return result


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    game, steps, settle, prefix, avg = "SG", "open,down,down,enter,back,back", 1.6, "", 3
    i = 0
    while i < len(argv):
        if argv[i] == "--game":
            game = argv[i + 1]; i += 2
        elif argv[i] == "--steps":
            steps = argv[i + 1]; i += 2
        elif argv[i] == "--settle":
            settle = float(argv[i + 1]); i += 2
        elif argv[i] == "--prefix":
            prefix = argv[i + 1]; i += 2
        elif argv[i] == "--avg":
            avg = int(argv[i + 1]); i += 2
        else:
            i += 1
    print(f"手机流程测绘：{game}  步骤 = {steps}  (settle={settle}s, 每步平均 {avg} 帧)")
    run(game, steps, settle, prefix, avg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
