"""sg_bridge.tools.montage — 把多张截图拼成一张小图（一次读图看多个状态）

用途：读图额度有限时，把"按 A / 按 B / 按 C / 基线"四张拼成一张对比图，一次看完。

用法::

    python -m sg_bridge.tools.montage a.png b.png c.png d.png --cols 2 --tile 360 --out grid.jpg
    python -m sg_bridge.tools.montage a.png b.png --cols 2 --tile 400 --out pair.jpg
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw


def build(paths: list[str], out: str, cols: int = 2, tile: int = 360,
          quality: int = 60, label: bool = True) -> dict:
    imgs = []
    for p in paths:
        img = Image.open(p).convert("RGB")
        ratio = tile / img.size[0]
        imgs.append(img.resize((tile, max(1, int(round(img.size[1] * ratio)))), Image.LANCZOS))
    rows = (len(imgs) + cols - 1) // cols
    tw, th = imgs[0].size
    sheet = Image.new("RGB", (tw * cols, th * rows), (20, 20, 20))
    draw = ImageDraw.Draw(sheet)
    for i, img in enumerate(imgs):
        x, y = (i % cols) * tw, (i // cols) * th
        sheet.paste(img, (x, y))
        if label:
            tag = os.path.basename(paths[i])[:28]
            draw.rectangle([x, y, x + 8 + 6 * len(tag), y + 12], fill=(0, 0, 0))
            draw.text((x + 2, y + 2), tag, fill=(255, 220, 120))
    sheet.save(out, "JPEG", quality=quality)
    return {"out": out, "size": list(sheet.size), "tiles": len(paths),
            "kb": round(os.path.getsize(out) / 1024, 1)}


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    paths, out, cols, tile, label = [], None, 2, 360, True
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--cols":
            cols = int(argv[i + 1]); i += 2
        elif a == "--tile":
            tile = int(argv[i + 1]); i += 2
        elif a == "--out":
            out = argv[i + 1]; i += 2
        elif a == "--no-label":
            label = False; i += 1
        else:
            paths.append(a); i += 1
    if not paths:
        print(__doc__)
        return 2
    if out is None:
        out = os.path.splitext(paths[0])[0] + "_grid.jpg"
    res = build(paths, out, cols, tile, label=label)
    print(f"{res['out']}  {res['size'][0]}x{res['size'][1]}  {res['kb']} KB  ({res['tiles']} 张)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
