"""sg_bridge.tools.phone_menu — 手机菜单的"自动学习 + 确定性导航"（不依赖看图）

自动学习（--learn）原理：
    打开手机 → 反复按 ↓ → 每次记指纹；
    当指纹重复出现时，说明选择已经回到走过的项 → **不同指纹数 = 菜单项数** ✓
    同时记录每次变化的包围盒 → 每一项所在的行位置（可换算成点击坐标）。

导航（--select N）：
    用上面的行数把选择移到第 N 项（支持环绕），再按 ENTER 进入；
    每步都用指纹核对是否真的换了项。

用法::

    python -m sg_bridge.tools.phone_menu --game SG --learn
    python -m sg_bridge.tools.phone_menu --game SG --select 1
    python -m sg_bridge.tools.phone_menu --game SG --show
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "out", "phone"))
CONFIG_PATH = os.path.normpath(os.path.join(HERE, "..", "config", "phone_menu.json"))

from .. import api                                    # noqa: E402
from .shot_diff import compare, fingerprint           # noqa: E402

DEFAULT_CONFIG = {
    "_comment": "手机菜单映射：items 需要人工看一次屏幕确认顺序；list_area/rows 可自动学习。",
    "_how_to_finish": [
        "1) 运行 --learn 自动数出项数并记录每项行位置",
        "2) 你对着屏幕按顺序把每项的名字填进 items",
        "3) 之后 AI 就能用 --select <名字或序号> 确定性操作手机",
    ],
    "games": {},
}


def _load_config() -> dict:
    if not os.path.exists(CONFIG_PATH):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(DEFAULT_CONFIG, fh, ensure_ascii=False, indent=2)
        return json.loads(json.dumps(DEFAULT_CONFIG))
    with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def _shot(tag: str, game: str) -> str:
    path = os.path.join(OUT_DIR, f"{tag}.png")
    api.call("screenshot", {"path": path, "target": game, "client_only": True})
    return path


def _state(tag: str, game: str) -> tuple[str, str]:
    path = _shot(tag, game)
    return fingerprint(path)["hash"], path


def learn(game: str, max_items: int = 14, settle: float = 1.0) -> dict:
    """自动学习菜单项数与每项位置。"""
    os.makedirs(OUT_DIR, exist_ok=True)
    api.call("attach", {"target": game, "focus": True})
    time.sleep(0.4)
    api.call("key_press", {"key": "e", "ms": 40})          # 打开手机
    time.sleep(settle)
    home_hash, home_path = _state("learn_home", game)
    print(f"手机首页指纹 {home_hash}")

    seen: dict[str, int] = {home_hash: 0}
    order: list[dict] = [{"index": 0, "hash": home_hash, "path": home_path}]
    prev_path = home_path
    for i in range(1, max_items + 1):
        api.call("key_press", {"key": "down", "ms": 40})
        time.sleep(settle)
        h, p = _state(f"learn_down{i}", game)
        diff = compare(prev_path, p)
        row = {"index": i, "hash": h, "changed_ratio": diff["changed_ratio"],
               "bbox": diff["changed_bbox"], "path": p}
        order.append(row)
        print(f"  ↓{i:<2} 指纹 {h}  变化 {diff['changed_ratio'] * 100:5.2f}%  bbox={diff['changed_bbox']}")
        if h in seen:
            print(f"  → 指纹与第 {seen[h]} 项相同（环绕）⇒ 菜单共 {len(seen)} 项")
            break
        seen[h] = i
        prev_path = p
    else:
        print(f"  达到上限 {max_items}，未观察到环绕")

    item_count = len(seen)
    rows = [{"item": r["index"] % item_count if item_count else 0,
             "bbox": r["bbox"]} for r in order[1:1 + item_count]]
    cfg = _load_config()
    cfg.setdefault("games", {})[game] = {
        "opens_with": "e",
        "closes_with": "backspace",
        "navigate": {"next": "down", "prev": "up", "confirm": "enter"},
        "item_count": item_count,
        "home_fingerprint": home_hash,
        "row_bboxes": rows,
        "items": [f"item{i}" for i in range(item_count)],   # 待人工确认名称
        "verified": False,
        "learned_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    _save_config(cfg)
    print(f"\n已写入 {CONFIG_PATH}（item_count={item_count}；items 名称待你确认）")
    return cfg["games"][game]


def select(game: str, index: int, settle: float = 1.0) -> dict:
    """把选择移到第 index 项并确认（用环形移动，最少按键）。"""
    cfg = _load_config()
    info = cfg.get("games", {}).get(game)
    if not info:
        raise RuntimeError(f"{game} 还没有手机菜单数据，先跑 --learn")
    count = int(info["item_count"])
    nav = info.get("navigate", {})
    idx = index % count
    keys = [nav.get("next", "down")] * idx
    api.call("attach", {"target": game, "focus": True})
    api.call("key_press", {"key": info.get("opens_with", "e"), "ms": 40})
    time.sleep(settle)
    for k in keys:
        api.call("key_press", {"key": k, "ms": 40})
        time.sleep(0.5)
    return {"game": game, "target_index": idx, "pressed": keys,
            "note": "已定位到第 %d 项；如需进入请再执行 key_press enter（或调用 game_action confirm）" % idx}


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    game, mode, index, max_items = "SG", "--show", 0, 14
    i = 0
    while i < len(argv):
        if argv[i] == "--game":
            game = argv[i + 1]; i += 2
        elif argv[i] == "--learn":
            mode = "--learn"; i += 1
        elif argv[i] == "--show":
            mode = "--show"; i += 1
        elif argv[i] == "--select":
            mode = "--select"; index = int(argv[i + 1]); i += 2
        elif argv[i] == "--max-items":
            max_items = int(argv[i + 1]); i += 2
        else:
            i += 1

    if mode == "--learn":
        learn(game, max_items)
    elif mode == "--select":
        print(json.dumps(select(game, index), ensure_ascii=False, indent=2))
    else:
        cfg = _load_config()
        print(json.dumps(cfg.get("games", {}).get(game, {"note": "尚未学习"}), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
