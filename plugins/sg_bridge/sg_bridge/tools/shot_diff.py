"""sg_bridge.tools.shot_diff — 比较两张截图的差异（不依赖"看图"）

用途：验证"某个按键/点击是否真的让画面变了"，并给出变化区域（可选只比某块区域）。
这是"无视觉"路径下判断操作是否生效的核心工具。

用法::

    python -m sg_bridge.tools.shot_diff a.png b.png
    python -m sg_bridge.tools.shot_diff a.png b.png --crop 0,0.5,1,0.5   # 只比下半屏
    python -m sg_bridge.tools.shot_diff a.png b.png --threshold 12 --save-diff out\\diff.png
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageChops


def _box(size: tuple[int, int], spec: "str | None"):
    if not spec:
        return (0, 0, size[0], size[1])
    vals = [float(p) for p in spec.split(",")]
    if len(vals) != 4:
        raise ValueError("--crop 需要 4 个值：x,y,w,h（像素或 0~1 比例）")
    if all(v <= 1.0 for v in vals):
        x, y, w, h = vals[0] * size[0], vals[1] * size[1], vals[2] * size[0], vals[3] * size[1]
    else:
        x, y, w, h = vals
    return (int(x), int(y), int(x + w), int(y + h))


def compare(a_path: str, b_path: str, crop: "str | None" = None,
            threshold: int = 12, save_diff: "str | None" = None) -> dict:
    a, b = Image.open(a_path).convert("RGB"), Image.open(b_path).convert("RGB")
    if a.size != b.size:
        b = b.resize(a.size, resample=Image.LANCZOS)
    box = _box(a.size, crop)
    a, b = a.crop(box), b.crop(box)
    diff = ImageChops.difference(a, b).convert("L")
    hist = diff.histogram()
    total = sum(hist) or 1
    changed = sum(hist[threshold:])
    # 变化区域的包围盒
    bbox = diff.point(lambda v: 255 if v >= threshold else 0).getbbox()
    result = {
        "size": list(a.size),
        "crop": list(box),
        "mean_abs_diff": round(sum(i * h for i, h in enumerate(hist)) / total, 2),
        "changed_pixels": changed,
        "changed_ratio": round(changed / total, 4),
        "changed_bbox": list(bbox) if bbox else None,
        "identical": changed == 0,
    }
    if save_diff:
        diff.save(save_diff)
        result["diff_path"] = save_diff
    return result


def fingerprint(path: str, grid: int = 16) -> dict:
    """给一张截图算"屏幕指纹"：缩到 grid×grid 的灰度均值矩阵 + 稳定哈希。

    用途：**不开图也能识别"当前是哪个界面"**。同一界面（无动画时）指纹完全一致；
    不同界面几乎必然不同。配合 :func:`compare` 可搭出状态机。
    """
    import hashlib

    img = Image.open(path).convert("L").resize((grid, grid), resample=Image.BOX)
    cells = list(img.getdata())                       # 长度 grid*grid
    # 量化到 0..31，降低压缩/抖动噪声
    quant = [c >> 3 for c in cells]
    digest = hashlib.sha1(bytes(quant)).hexdigest()[:16]
    return {
        "grid": grid,
        "hash": digest,
        "mean": round(sum(cells) / len(cells), 2),
        "cells": quant,
        "path": path,
    }


def same_screen(a_path: str, b_path: str, grid: int = 16, tolerance: int = 2) -> dict:
    """判断两张图是否"同一个界面"（逐格比较，允许 tolerance 级灰度差）。"""
    fa, fb = fingerprint(a_path, grid), fingerprint(b_path, grid)
    diffs = [abs(x - y) for x, y in zip(fa["cells"], fb["cells"])]
    bad = [d for d in diffs if d > tolerance]
    return {
        "same": len(bad) <= max(1, len(diffs) // 100),   # 允许 1% 格子越界
        "max_cell_diff": max(diffs) if diffs else 0,
        "cells_over_tolerance": len(bad),
        "total_cells": len(diffs),
        "hash_a": fa["hash"], "hash_b": fb["hash"],
    }


def average(paths: list[str], out_path: "str | None" = None) -> dict:
    """把多张截图**平均**成一张（用来压掉动态背景噪声，如雨景/影片）。

    做法：同一界面多拍几帧 → 逐像素取平均 → 静态 UI 保留、动态背景被抹平。
    """
    import numpy as np

    frames = [np.asarray(Image.open(p).convert("RGB"), dtype=np.float32) for p in paths]
    stack = np.stack(frames, axis=0)
    mean = stack.mean(axis=0).astype("uint8")
    img = Image.fromarray(mean)
    if out_path:
        img.save(out_path, "PNG")
    return {"out": out_path, "frames": len(paths), "size": list(img.size)}


def tile_matrix(a_path: str, b_path: str, cols: int = 8, rows: int = 6,
                threshold: int = 12) -> dict:
    """按 ``cols×rows`` 瓦片比较两张图的差异，返回每个瓦片的"变化像素占比"矩阵。

    用途：在动态背景里定位**静态 UI 面板** —— 面板会遮住动画区域，
    因此"原本在动、开界面后变成不动"的瓦片集合就是面板所在位置。
    """
    import numpy as np

    a = np.asarray(Image.open(a_path).convert("L"), dtype=np.int16)
    b = np.asarray(Image.open(b_path).convert("L"), dtype=np.int16)
    if a.shape != b.shape:
        b = np.asarray(Image.open(b_path).convert("L").resize(
            (a.shape[1], a.shape[0]), resample=Image.LANCZOS), dtype=np.int16)
    diff = np.abs(a - b) >= threshold
    h, w = diff.shape
    out: list[list[float]] = []
    for r in range(rows):
        y0, y1 = h * r // rows, h * (r + 1) // rows
        row: list[float] = []
        for c in range(cols):
            x0, x1 = w * c // cols, w * (c + 1) // cols
            tile = diff[y0:y1, x0:x1]
            row.append(round(float(tile.mean()) * 100, 2) if tile.size else 0.0)
        out.append(row)
    return {"cols": cols, "rows": rows, "matrix": out,
            "tile_size": [w // cols, h // rows]}


def print_matrix(mat: dict, title: str = "") -> None:
    if title:
        print(title)
    for row in mat["matrix"]:
        print("   " + " ".join(f"{v:6.1f}" for v in row))


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "fingerprint":
        for p in argv[1:]:
            fp = fingerprint(p)
            print(f"{p}  hash={fp['hash']}  mean={fp['mean']}")
        return 0
    if argv[0] == "same":
        res = same_screen(argv[1], argv[2])
        print(f"same={res['same']}  max_cell_diff={res['max_cell_diff']}  "
              f"over_tolerance={res['cells_over_tolerance']}/{res['total_cells']}")
        return 0 if res["same"] else 1

    a, b = argv[0], argv[1]
    crop, threshold, save_diff = None, 12, None
    i = 2
    while i < len(argv):
        if argv[i] == "--crop":
            crop = argv[i + 1]; i += 2
        elif argv[i] == "--threshold":
            threshold = int(argv[i + 1]); i += 2
        elif argv[i] == "--save-diff":
            save_diff = argv[i + 1]; i += 2
        else:
            i += 1
    res = compare(a, b, crop, threshold, save_diff)
    print(f"mean_abs_diff={res['mean_abs_diff']}  changed={res['changed_pixels']} "
          f"({res['changed_ratio'] * 100:.2f}%)  bbox={res['changed_bbox']}  "
          f"identical={res['identical']}")
    return 0 if not res["identical"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
