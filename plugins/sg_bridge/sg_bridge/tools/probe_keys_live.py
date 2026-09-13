"""sg_bridge.tools.probe_keys_live — 在真实游戏里实测每个按键"是否生效"

原理（完全不依赖"看图"）：
    基线截图 → 按键 → 等一会 → 截图 → 与基线做像素差 →
    再按一次（复位）→ 截图 → 与基线比 → 判断是否为"开关型"

判读经验：
    * 变化很小（<1%，集中在下方对话框区域）→ 多半是"推进对话"
    * 中等（1%~15%）→ 切换类（快进指示、隐藏文字）
    * 很大（>20%）→ 打开了一个界面（系统菜单 / 手机）
    * 复位后与基线几乎一致 → 该键是"开关"（再按一次即关闭）

用法::

    python -m sg_bridge.tools.probe_keys_live --game SG --keys z,e,c,1,q
    python -m sg_bridge.tools.probe_keys_live --game SG0 --keys escape,backspace
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "out", "liveprobe"))

from .. import api                                   # noqa: E402
from .shot_diff import compare                       # noqa: E402


def _shot(name: str, game: str) -> str:
    path = os.path.join(OUT_DIR, f"{name}.png")
    api.call("screenshot", {"path": path, "target": game, "client_only": True})
    return path


def wait_stable(game: str, settle: float, max_wait: float = 150.0,
                threshold: float = 0.008) -> tuple[bool, dict]:
    """等到画面"基本静止"（用来避开影片/过场动画造成的基线漂移）。

    连续两张间隔 ``settle`` 的截图差异小于 ``threshold`` 视为静止。
    """
    last = {"changed_ratio": 1.0}
    deadline = time.time() + max_wait
    while time.time() < deadline:
        a = _shot("stable_a", game)
        time.sleep(settle)
        b = _shot("stable_b", game)
        last = compare(a, b)
        if last["changed_ratio"] < threshold:
            return True, last
        print(f"    (画面仍在动：{last['changed_ratio'] * 100:.1f}% …继续等)")
    return False, last


def probe(game: str, keys: list[str], settle: float = 2.5,
          restore_key: "str | None" = None, wait: bool = True) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    api.call("attach", {"target": game, "focus": True})
    time.sleep(0.5)

    stable, noise = (True, {"changed_ratio": 0.0})
    if wait:
        print("  等待画面静止…")
        stable, noise = wait_stable(game, settle)
        print(f"  静止判定={'是' if stable else '否'}  无操作漂移={noise['changed_ratio'] * 100:.2f}%")

    base = _shot("00_base", game)
    rows = []
    for key in keys:
        api.call("key_press", {"key": key, "ms": 40})
        time.sleep(settle)
        after = _shot(f"after_{key}", game)
        d1 = compare(base, after)
        api.call("key_press", {"key": restore_key or key, "ms": 40})
        time.sleep(settle)
        back = _shot(f"restore_{key}", game)
        d2 = compare(base, back)
        row = {
            "key": key,
            "changed_ratio": d1["changed_ratio"],
            "changed_pixels": d1["changed_pixels"],
            "bbox": d1["changed_bbox"],
            "restored_ratio": d2["changed_ratio"],
            "restored_identical": d2["changed_ratio"] < 0.005,
            "verdict": _verdict(d1["changed_ratio"], d2["changed_ratio"]),
        }
        rows.append(row)
        print(f"  {key:<10} 按下后 {row['changed_ratio'] * 100:6.2f}%   "
              f"复位后 {row['restored_ratio'] * 100:6.2f}%   → {row['verdict']}")
    result = {"game": game, "stable": stable, "noise_ratio": noise["changed_ratio"],
              "baseline": base, "keys": rows}
    with open(os.path.join(OUT_DIR, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    return result


def _verdict(r1: float, r2: float) -> str:
    if r1 < 0.002:
        return "无反应"
    if r1 < 0.02:
        return "轻微变化(可能是推进/文字动画)"
    if r1 < 0.30:
        return "明显变化(切换类)" if r2 < 0.02 else "明显变化(不可逆/进入新状态)"
    return "大幅变化(打开了界面)" if r2 >= 0.02 else "大幅变化且可复位(开关型界面)"


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    game, keys, settle, restore_key, wait = "SG", ["z", "e", "c", "1", "q"], 2.5, None, True
    i = 0
    while i < len(argv):
        if argv[i] == "--game":
            game = argv[i + 1]; i += 2
        elif argv[i] == "--keys":
            keys = [k.strip() for k in argv[i + 1].split(",") if k.strip()]; i += 2
        elif argv[i] == "--settle":
            settle = float(argv[i + 1]); i += 2
        elif argv[i] == "--restore-key":
            restore_key = argv[i + 1]; i += 2
        elif argv[i] == "--no-wait":
            wait = False; i += 1
        else:
            i += 1
    print(f"实测 {game} 的按键：{', '.join(keys)}（每键间隔 {settle}s）")
    probe(game, keys, settle, restore_key, wait)
    print(f"\n结果: {os.path.join(OUT_DIR, 'result.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
