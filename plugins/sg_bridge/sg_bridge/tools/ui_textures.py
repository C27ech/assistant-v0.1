"""sg_bridge.tools.ui_textures — 从 mpk 里提取 UI 贴图（DDS→PNG）并用数字描述其布局

为什么需要：手机/系统菜单等界面是**贴图**画出来的（`PHONE.DDS` 等）。
把贴图拿出来后可以：
  * 用**模板匹配**判断"当前画面里有没有这个界面、在什么位置"（不依赖看图）；
  * 用不透明区域（alpha>0）的包围盒推断界面在屏幕上的实际范围。

用法::

    python -m sg_bridge.tools.ui_textures <system.mpk> --filter PHONE
    python -m sg_bridge.tools.ui_textures <system.mpk> --filter PHONE --out out/ui/sg
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from dds import read_dds          # noqa: E402  （复用早期写好的 DDS 解码器）
from mpk import MpkArchive        # noqa: E402


def _ascii_map(alpha: np.ndarray, cols: int = 24, rows: int = 12) -> list[str]:
    """把 alpha 通道粗略画成字符图（方便在纯文字环境里判断布局）。"""
    h, w = alpha.shape
    ramp = " .:-=+*#%@"
    lines = []
    for r in range(rows):
        y0, y1 = h * r // rows, h * (r + 1) // rows
        line = []
        for c in range(cols):
            x0, x1 = w * c // cols, w * (c + 1) // cols
            v = float(alpha[y0:y1, x0:x1].mean())
            line.append(ramp[min(len(ramp) - 1, int(v * (len(ramp) - 1) / 255.0))])
        lines.append("".join(line))
    return lines


def extract(mpk_path: str, filt: str, out_dir: str, png: bool = True) -> list[dict]:
    os.makedirs(out_dir, exist_ok=True)
    results: list[dict] = []
    with MpkArchive(mpk_path) as arc:
        for entry in arc.entries():
            if filt and filt.upper() not in entry.name.upper():
                continue
            raw = arc.read(entry)
            info = {"name": entry.name, "bytes": len(raw)}
            dest = os.path.join(out_dir, entry.name)
            with open(dest, "wb") as fh:
                fh.write(raw)
            if entry.name.lower().endswith(".dds"):
                try:
                    img = read_dds(dest)
                    info.update({"width": img.width, "height": img.height,
                                 "fourcc": img.fourcc, "bits": img.bit_count})
                    rgba = img.rgba
                    if rgba is not None:
                        alpha = (rgba[..., 3] * 255).astype("uint8")
                        ys, xs = np.nonzero(alpha > 8)
                        if len(xs):
                            info["opaque_bbox"] = [int(xs.min()), int(ys.min()),
                                                   int(xs.max()), int(ys.max())]
                        info["opaque_ratio"] = round(float((alpha > 8).mean()), 4)
                        if png:
                            from PIL import Image
                            Image.fromarray(rgba, "RGBA").save(dest[:-4] + ".png")
                            info["png"] = dest[:-4] + ".png"
                        info["alpha_map"] = _ascii_map(alpha)
                except Exception as exc:      # noqa: BLE001
                    info["decode_error"] = f"{type(exc).__name__}: {exc}"
            results.append(info)
    return results


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    mpk_path, filt, out_dir, show_map = argv[0], "", "", True
    i = 1
    while i < len(argv):
        if argv[i] == "--filter":
            filt = argv[i + 1]; i += 2
        elif argv[i] == "--out":
            out_dir = argv[i + 1]; i += 2
        elif argv[i] == "--no-map":
            show_map = False; i += 1
        else:
            i += 1
    if not out_dir:
        out_dir = os.path.join(ROOT, "sg_bridge", "out", "ui",
                               os.path.splitext(os.path.basename(mpk_path))[0])
    for info in extract(mpk_path, filt, out_dir):
        extra = ""
        if "width" in info:
            extra = (f"  {info['width']}x{info['height']} {info['fourcc']}"
                     f" 不透明比例={info['opaque_ratio']} bbox={info.get('opaque_bbox')}")
        print(f"{info['name']:<28}{info['bytes']:>10} bytes{extra}")
        if show_map and "alpha_map" in info:
            for line in info["alpha_map"]:
                print(f"      |{line}|")
    print(f"\n输出目录: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
