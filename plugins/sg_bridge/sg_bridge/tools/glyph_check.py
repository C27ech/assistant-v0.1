"""sg_bridge.tools.glyph_check — 用"字形对称性"判别截图里是哪个字母（不靠读图）

原理（对字体差异免疫）：
    * ``X``：左右镜像、上下镜像后基本还是自己 → 镜像相关系数高
    * ``C``：左右镜像后开口跑到左边 → 镜像相关系数明显低
    * ``Z``：左右镜像低、180° 旋转高
    * ``E``：左右镜像低、上下镜像低
    * ``I``：左右镜像高、上下镜像高（与 X 类似，但笔画分布不同）

用法::

    python -m sg_bridge.tools.glyph_check <图.png> <x,y,w,h> [更多框…]
    python -m sg_bridge.tools.glyph_check <图.png> 470,695,50,45 --label Z
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image


def _crop_glyph(path: str, box: tuple[int, int, int, int], size: int = 48) -> np.ndarray:
    """裁出区域 → 灰度 → 二值化 → 按墨迹包围盒裁紧 → 归一化为 size×size。"""
    img = Image.open(path).convert("L").crop((box[0], box[1], box[0] + box[2], box[1] + box[3]))
    arr = np.asarray(img, dtype=np.float32)
    thr = arr.mean() + 0.5 * arr.std()          # 取"比平均亮"的部分作为笔画（键帽是亮的）
    mask = (arr > thr).astype(np.float32)
    ys, xs = np.nonzero(mask > 0)
    if len(xs) < 4:
        mask = (arr < (arr.mean() - 0.5 * arr.std())).astype(np.float32)
        ys, xs = np.nonzero(mask > 0)
    if len(xs) < 4:
        return np.zeros((size, size), np.float32)
    m = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    return np.asarray(Image.fromarray((m * 255).astype("uint8")).resize((size, size), Image.BILINEAR),
                      dtype=np.float32) / 255.0


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    d = (np.linalg.norm(a) * np.linalg.norm(b))
    return float((a * b).sum() / d) if d else 0.0


def analyse(path: str, box: tuple[int, int, int, int]) -> dict:
    g = _crop_glyph(path, box)
    h = np.fliplr(g)
    v = np.flipud(g)
    r180 = np.rot90(g, 2)
    ink = float((g > 0.5).mean())
    res = {
        "box": list(box),
        "ink_ratio": round(ink, 3),
        "corr_self": round(_corr(g, g), 3),
        "corr_hflip": round(_corr(g, h), 3),      # 左右镜像
        "corr_vflip": round(_corr(g, v), 3),      # 上下镜像
        "corr_rot180": round(_corr(g, r180), 3),  # 180° 旋转
    }
    # 逐行/逐列墨迹分布（X 的两端各行都有墨、C 只在左侧有上下墨）
    col = (g > 0.5).mean(axis=0)
    res["left_cols_ink"] = round(float(col[: g.shape[1] // 4].mean()), 3)
    res["right_cols_ink"] = round(float(col[-g.shape[1] // 4:].mean()), 3)
    guess = []
    if res["corr_hflip"] > 0.72 and res["corr_vflip"] > 0.72:
        guess.append("X（或 I）")
    if res["corr_hflip"] < 0.6 and res["corr_vflip"] > 0.72:
        guess.append("C")
    if res["corr_hflip"] < 0.6 and res["corr_vflip"] < 0.6 and res["corr_rot180"] > 0.7:
        guess.append("Z")
    if res["corr_hflip"] < 0.6 and res["corr_vflip"] < 0.6 and res["corr_rot180"] < 0.6:
        guess.append("E 或其它")
    res["guess"] = " / ".join(guess) or "不确定"
    return res


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = argv[0]
    boxes: list[tuple[int, int, int, int]] = []
    for spec in argv[1:]:
        if spec.startswith("--"):
            continue
        parts = [int(p) for p in spec.split(",")]
        if len(parts) == 4:
            boxes.append(tuple(parts))
    for b in boxes:
        r = analyse(path, b)
        print(f"框 {r['box']}  墨迹={r['ink_ratio']}  "
              f"左右镜像={r['corr_hflip']}  上下镜像={r['corr_vflip']}  180°={r['corr_rot180']}  "
              f"左列墨={r['left_cols_ink']} 右列墨={r['right_cols_ink']}  ⇒ **{r['guess']}**")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
