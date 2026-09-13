"""sg_bridge.tools.advance_until_dialogue — 反复推进，直到游戏"在等输入"

判据（全程不看图）：**画面漂移降到接近 0**。
过场动画/影片每帧都在变；对话等待输入时画面基本静止（只有轻微循环动画）。

用法::

    python -m sg_bridge.tools.advance_until_dialogue --game SG
    python -m sg_bridge.tools.advance_until_dialogue --game SG --key enter --max 60 --drift 1.0
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "out", "waitdlg"))

from .. import api                                  # noqa: E402
from .shot_diff import compare                      # noqa: E402


def _shot(name: str, game: str) -> str:
    path = os.path.join(OUT_DIR, f"{name}.png")
    api.call("screenshot", {"path": path, "target": game, "client_only": True})
    return path


def drift(game: str, span: float = 1.5) -> float:
    """测一次"无操作漂移"（两张截图间隔 span 秒的变化比例）。"""
    a = _shot("_d_a", game)
    time.sleep(span)
    b = _shot("_d_b", game)
    return compare(a, b)["changed_ratio"]


def run(game: str, key: str = "enter", max_presses: int = 40, drift_limit: float = 0.010,
        span: float = 1.5, gap: float = 0.6, need_hits: int = 2) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    api.call("attach", {"target": game, "focus": True})
    time.sleep(0.4)
    history: list[dict] = []
    hits = 0

    d = drift(game, span)
    history.append({"press": 0, "drift": d})
    print(f"  初始漂移 {d * 100:6.2f}%")
    if d < drift_limit:
        hits = 1

    for i in range(1, max_presses + 1):
        api.call("key_press", {"key": key, "ms": 40})
        time.sleep(gap)
        d = drift(game, span)
        history.append({"press": i, "drift": d})
        mark = ""
        if d < drift_limit:
            hits += 1
            mark = f"  <-- 静止 {hits}/{need_hits}"
        else:
            hits = 0
        print(f"  第 {i:>3} 次推进后漂移 {d * 100:6.2f}%{mark}")
        if hits >= need_hits:
            result = {"game": game, "ok": True, "presses": i,
                      "drift": d, "history": history}
            break
    else:
        result = {"game": game, "ok": False, "presses": max_presses,
                  "drift": history[-1]["drift"], "history": history}

    path = os.path.join(OUT_DIR, "result.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(f"\n结论: {'已到等待输入状态' if result['ok'] else '仍未静止'} "
          f"(漂移 {result['drift'] * 100:.2f}%, 共 {result['presses']} 次推进)")
    print(f"结果: {path}")
    return result


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    game, key, max_presses, drift_limit, span, gap = "SG", "enter", 40, 0.010, 1.5, 0.6
    i = 0
    while i < len(argv):
        if argv[i] == "--game":
            game = argv[i + 1]; i += 2
        elif argv[i] == "--key":
            key = argv[i + 1]; i += 2
        elif argv[i] == "--max":
            max_presses = int(argv[i + 1]); i += 2
        elif argv[i] == "--drift":
            drift_limit = float(argv[i + 1]) / 100.0; i += 2
        elif argv[i] == "--span":
            span = float(argv[i + 1]); i += 2
        elif argv[i] == "--gap":
            gap = float(argv[i + 1]); i += 2
        else:
            i += 1
    print(f"推进直到静止：{game}  键={key}  最多 {max_presses} 次  漂移阈值 {drift_limit * 100:.1f}%")
    res = run(game, key, max_presses, drift_limit, span, gap)
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
