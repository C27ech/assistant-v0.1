"""sg_bridge.tools.find_texture — 在截图里定位"游戏自己的 UI 贴图"（不依赖看图）

原理（块签名精确匹配，对缩放/混合免疫性有限但速度极快）：
    1. 把贴图（如 PHONE.DDS 解出的 PNG）切成 patch×patch 的小块，每块缩成 q×q 灰度，
       再把每个格子量化成 2bit（4 档）→ 得到 16 字节的"签名"。
    2. 对截图做同样的事，查签名表 → 命中即说明"贴图这块画在屏幕上这个位置"。
    3. 汇总命中点 → 得到 **贴图坐标 → 屏幕坐标** 的映射（含缩放/偏移估计）。

默认先尝试 1:1（游戏把 UI 贴图按原始像素绘制时命中率最高）。

用法::

    python -m sg_bridge.tools.find_texture <截图.png> <贴图.png> [--patch 32] [--q 8] [--step 8]
    python -m sg_bridge.tools.find_texture <截图.png> <贴图.dds> --list-matches 20
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def _load_gray(path: str) -> np.ndarray:
    from PIL import Image

    if path.lower().endswith(".dds"):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from dds import read_dds
        img = read_dds(path)
        if img.rgba is not None:
            arr = (img.rgba[..., :3].mean(axis=2)).astype("uint8")
        else:
            arr = (np.clip(img.pixels, 0, 1) * 255).astype("uint8")
        return arr
    return np.asarray(Image.open(path).convert("L"), dtype="uint8")


def _signatures(gray: np.ndarray, patch: int, q: int, step: int,
                min_std: float = 12.0) -> tuple[np.ndarray, list]:
    """返回 (签名数组 [N, q*q] uint8(0..3), 位置列表)。

    ``min_std``：过滤掉"平坦无纹理"的块 —— 这类块在任何图上都一样，是假命中的主要来源。
    """
    from PIL import Image

    h, w = gray.shape
    coords: list[tuple[int, int]] = []
    sigs: list[np.ndarray] = []
    for y in range(0, h - patch + 1, step):
        for x in range(0, w - patch + 1, step):
            block = gray[y:y + patch, x:x + patch]
            if float(np.asarray(block, dtype=np.float32).std()) < min_std:
                continue
            small = np.asarray(Image.fromarray(block).resize((q, q), Image.BOX), dtype=np.float32)
            quant = np.clip((small / 64.0).astype(np.int32), 0, 3).astype("uint8")
            sigs.append(quant.reshape(-1))
            coords.append((y, x))
    if not sigs:
        return np.zeros((0, q * q), "uint8"), []
    return np.stack(sigs), coords


def find(shot_path: str, tex_path: str, patch: int = 32, q: int = 8, step: int = 8,
         list_matches: int = 12, tolerance: int = 0) -> dict:
    shot = _load_gray(shot_path)
    tex = _load_gray(tex_path)
    tex_sigs, tex_coords = _signatures(tex, patch, q, step)
    shot_sigs, shot_coords = _signatures(shot, patch, q, step)

    # 允许 tolerance 个格子不同（0 = 逐格完全一致）
    def pack(vec: np.ndarray) -> bytes:
        """0..3 的格子值按 2bit/格 打包（64 格 → 16 字节），比 1bit 严格得多。"""
        bits = np.zeros(vec.size * 2, dtype="uint8")
        bits[0::2] = (vec >> 1) & 1
        bits[1::2] = vec & 1
        return bytes(np.packbits(bits).tobytes())

    table: dict[bytes, list[int]] = {}
    for i in range(tex_sigs.shape[0]):
        table.setdefault(pack(tex_sigs[i]), []).append(i)

    matches: list[dict] = []
    for j in range(shot_sigs.shape[0]):
        row = shot_sigs[j]
        cand = table.get(pack(row))
        if not cand:
            continue
        for i in cand[:2]:
            same = int((tex_sigs[i] == row).sum())
            score = same / row.size
            if score >= 1.0 - tolerance / 100.0:
                ty, tx = tex_coords[i]
                sy, sx = shot_coords[j]
                matches.append({"tex": [tx, ty], "shot": [sx, sy],
                                "score": round(score, 4)})
    if matches:
        from collections import Counter

        dx = [m["shot"][0] - m["tex"][0] for m in matches]
        dy = [m["shot"][1] - m["tex"][1] for m in matches]
        # 按 4px 网格聚类偏移量：真实映射会形成一个明显的尖峰
        clusters = Counter(((x // 4) * 4, (y // 4) * 4) for x, y in zip(dx, dy))
        top = [{"offset": list(k), "count": v} for k, v in clusters.most_common(5)]
        result = {
            "shot": shot_path, "texture": tex_path, "patch": patch, "step": step,
            "matches": len(matches),
            "offset_x": {"min": int(min(dx)), "max": int(max(dx)), "median": int(np.median(dx))},
            "offset_y": {"min": int(min(dy)), "max": int(max(dy)), "median": int(np.median(dy))},
            "top_offsets": top,
            "peak_share": round(top[0]["count"] / len(matches), 3) if top else 0.0,
            "examples": matches[:list_matches],
        }
    else:
        result = {"shot": shot_path, "texture": tex_path, "patch": patch, "step": step,
                  "matches": 0, "examples": []}
    return result


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print(__doc__)
        return 2
    shot, tex = argv[0], argv[1]
    patch, q, step, tol, show = 32, 8, 8, 0, 12
    i = 2
    while i < len(argv):
        if argv[i] == "--patch":
            patch = int(argv[i + 1]); i += 2
        elif argv[i] == "--q":
            q = int(argv[i + 1]); i += 2
        elif argv[i] == "--step":
            step = int(argv[i + 1]); i += 2
        elif argv[i] == "--tolerance":
            tol = int(argv[i + 1]); i += 2
        elif argv[i] == "--list-matches":
            show = int(argv[i + 1]); i += 2
        else:
            i += 1
    res = find(shot, tex, patch, q, step, show, tol)
    print(f"命中块数 = {res['matches']}   (patch={patch}, step={step}, tolerance={tol})")
    if res["matches"]:
        print(f"贴图→屏幕 偏移: x={res['offset_x']}  y={res['offset_y']}")
        print(f"主导偏移簇（占比 {res['peak_share'] * 100:.1f}%）: "
              f"{[ (o['offset'], o['count']) for o in res['top_offsets'] ]}")
        print("样例（贴图坐标 → 屏幕坐标）:")
        for m in res["examples"]:
            print(f"   tex{m['tex']} → shot{m['shot']}  score={m['score']}")
        print("判读：峰值占比高（>30%）且偏移集中 ⇒ 该贴图确实以 1:1 画在屏幕上；"
              "分散则说明只是平坦区域撞签名，不可信。")
    else:
        print("未命中：说明该贴图没有以 1:1 像素绘制（可能被缩放/混合/裁剪），"
              "或此界面根本没用到它")
    return 0 if res["matches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
