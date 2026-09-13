"""sg_bridge.tools.view_shot — 把截图缩小/裁剪成"便于查看"的图

用途：全屏截图（1920×1080 PNG 1~2MB）直接"看"容易超限，先缩小或裁剪再读。

用法::

    python -m sg_bridge.tools.view_shot <src.png>                     # 缩到 1200 宽
    python -m sg_bridge.tools.view_shot <src.png> --width 1600 --scale 1
    python -m sg_bridge.tools.view_shot <src.png> --crop 0.6,0,0.4,1  # 右侧 40% 裁剪
    python -m sg_bridge.tools.view_shot <src.png> --crop 300,100,800,400 --scale 2
"""
from __future__ import annotations

import os
import sys

from PIL import Image


def _crop_box(size: tuple[int, int], spec: str) -> tuple[int, int, int, int]:
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 4:
        raise ValueError("--crop 需要 4 个值：x,y,w,h（像素，或 0~1 的小数比例）")
    vals = [float(p) for p in parts]
    if all(v <= 1.0 for v in vals):
        x, y, w, h = (vals[0] * size[0], vals[1] * size[1], vals[2] * size[0], vals[3] * size[1])
    else:
        x, y, w, h = vals
    box = (int(x), int(y), int(x + w), int(y + h))
    box = (max(0, box[0]), max(0, box[1]), min(size[0], box[2]), min(size[1], box[3]))
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError(f"裁剪区域无效: {box}")
    return box


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    src = argv[0]
    width, scale, crop, out, quality = 1200, 1.0, None, None, 88
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == "--width":
            width = int(argv[i + 1]); i += 2
        elif arg == "--scale":
            scale = float(argv[i + 1]); i += 2
        elif arg == "--crop":
            crop = argv[i + 1]; i += 2
        elif arg == "--out":
            out = argv[i + 1]; i += 2
        elif arg == "--quality":
            quality = int(argv[i + 1]); i += 2
        else:
            i += 1

    img = Image.open(src)
    if crop:
        img = img.crop(_crop_box(img.size, crop))
    if scale != 1.0:
        img = img.resize((max(1, int(img.size[0] * scale)), max(1, int(img.size[1] * scale))),
                         resample=Image.LANCZOS)
    if img.size[0] > width:
        ratio = width / img.size[0]
        img = img.resize((width, max(1, int(round(img.size[1] * ratio)))), resample=Image.LANCZOS)

    if out is None:
        base = os.path.splitext(src)[0]
        out = base + "_view.jpg"
    img.convert("RGB").save(out, "JPEG", quality=quality)
    size_kb = round(os.path.getsize(out) / 1024)
    print(f"{out}  {img.size[0]}x{img.size[1]}  {size_kb} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
