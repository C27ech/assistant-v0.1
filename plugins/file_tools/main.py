#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件读取插件 —— 让助手AI 读取本机文本文件 / 列目录 / 找文件 / 搜内容。

用法：
    python main.py read "<路径>" [--start 1] [--lines 200]
    python main.py list "<目录>" [--all]
    python main.py find "<通配符>" [--root "<目录>"] [--n 40]
    python main.py grep "<正则>" [--root "<目录>"] [--ext .py,.md] [--n 30]

安全设计（重要，三层）：
    1. 可读范围：由 config.json 的 roots 决定（默认 "*" = 整台电脑；也可以写成具体目录列表）。
    2. 敏感文件黑名单：密钥/凭据/令牌/私钥/.env/config.json/.git/浏览器登录数据/QQ 登录态 一律拒绝——
       即使文件就在可读范围内也读不到。这是为了防「有人诱导助手读 .env 把 API key 发到聊天里」。
       黑名单内置在代码里，只允许通过配置追加、不能取消。
    3. 体积与类型限制：跳过 >8MB 的文件和二进制文件；输出超限自动截断，并提示用
       --start 分块续读。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:  # 保证 Windows 控制台/管道下中文不乱码
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"

DEFAULTS = {
    "roots": ["*"],                        # "*" = 整台电脑（自动枚举所有盘）
    "default_path": "",                # 不给路径时的默认起点；留空 = 自动探测用户桌面
    "deny_patterns": [],
    "max_file_mb": 8,
    "max_output_chars": 12000,
    "max_list_entries": 200,
    "max_find_results": 40,
    "max_grep_matches": 30,
    "grep_max_files": 3000,
}

# 内置敏感规则（小写子串匹配，不可取消，只能追加）
BUILTIN_DENY = (
    # 配置 / 凭据
    ".env", "config.json", "credentials", "credential", "secret", "password",
    "passwd", "apikey", "api_key", "access_token", "token", "cookie",
    "login data", "web data", "keychain", "netrc", "kubeconfig",
    ".aws", ".docker", ".npmrc", ".pypirc", ".git-credentials",
    # 私钥 / 证书 / 密钥库
    "id_rsa", "id_ed25519", "id_dsa", ".ppk", ".pem", ".pfx", ".p12", ".key",
    ".keystore", ".jks", ".kdbx", ".ovpn", "unattend.xml",
    # 版本库 / 登录态
    ".git", ".ssh", "tencent files", "nt_qq", "napcat",
    # Windows 凭据库与注册表蜂巢
    "\\microsoft\\credentials", "\\config\\sam", "\\config\\system",
)


def _all_drives() -> list:
    """枚举本机所有存在的盘符（Windows）。"""
    drives = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        d = Path(f"{letter}:/")
        try:
            if d.exists():
                drives.append(str(d))
        except Exception:  # noqa: BLE001
            pass
    return drives


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(user, dict):
                cfg.update(user)
    except Exception as e:  # noqa: BLE001
        print(f"[警告] 读取 config.json 失败，改用默认配置：{e}", file=sys.stderr)

    raw_roots = cfg.get("roots") or DEFAULTS["roots"]
    roots = []
    for r in raw_roots:
        token = str(r).strip()
        if token in ("*", "all", "全盘", "全部"):
            roots.extend(_all_drives())          # "*" = 整台电脑
            continue
        try:
            roots.append(str(Path(os.path.expandvars(token)).expanduser().resolve()))
        except Exception:  # noqa: BLE001
            pass
    cfg["roots"] = roots or [str(Path.home())]

    # 不给路径时的默认起点（list / find / grep / 相对路径都以它为准）
    # 空值 / 占位符 / 路径不存在 → 自动探测（用户桌面 → 用户主目录 → 当前目录）
    dp = str(cfg.get("default_path") or "").strip()
    resolved = None
    if dp and "<" not in dp and ">" not in dp:
        try:
            resolved = Path(os.path.expandvars(dp)).expanduser().resolve()
        except Exception:  # noqa: BLE001
            resolved = None
    if resolved is None or not resolved.exists():
        for cand in (Path.home() / "Desktop", Path.home(), Path.cwd()):
            if cand.exists():
                resolved = cand
                break
    cfg["default_path"] = str(resolved or Path.home())

    extra = [str(p).lower() for p in (cfg.get("deny_patterns") or [])]
    cfg["deny_patterns"] = [d.lower() for d in BUILTIN_DENY] + extra
    return cfg


def denied_by(p: Path, cfg: dict) -> str:
    """命中敏感规则就返回规则本身，否则返回空串。"""
    low = str(p).lower()
    for pat in cfg["deny_patterns"]:
        if pat and pat in low:
            return pat
    return ""


def resolve_path(raw: str, cfg: dict):
    """把用户给的路径解析成安全路径。返回 (Path | None, 错误信息)。"""
    text = str(raw or "").strip().strip('"').strip("'")
    if not text:
        return None, "路径为空"
    p = Path(os.path.expandvars(text)).expanduser()
    if not p.is_absolute():
        p = Path(cfg.get("default_path") or cfg["roots"][0]) / p   # 相对路径 → 以默认起点为基准
    try:
        p = p.resolve()
    except Exception as e:  # noqa: BLE001
        return None, f"路径无法解析：{e}"

    hit = denied_by(p, cfg)
    if hit:
        return None, (
            f"安全策略拦截：路径命中敏感规则「{hit}」。"
            "含密钥 / 凭据 / 登录态的文件（.env、config.json、私钥与证书、SSH key、"
            "浏览器 Cookie 与登录数据、云凭据、QQ 登录态、Windows 凭据库等）"
            "一律不允许读取——这是硬规则，请换别的文件。"
        )

    roots = [Path(r) for r in cfg["roots"]]
    if not any(p == r or r in p.parents for r in roots):
        return None, ("越界：不在允许的根目录内。允许范围："
                      + "、".join(str(r) for r in roots))
    return p, ""


def looks_binary(p: Path) -> bool:
    try:
        with p.open("rb") as f:
            chunk = f.read(8192)
    except Exception:  # noqa: BLE001
        return False
    if not chunk:
        return False
    if b"\x00" in chunk:
        return True
    printable = sum(1 for b in chunk if b in b"\t\n\r" or 32 <= b < 127 or b >= 0x80)
    return printable / len(chunk) < 0.85


def read_text(p: Path) -> str:
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ------------------------------------------------------------------ 遍历
SKIP_DIRS = {".git", ".svn", "node_modules", "__pycache__", ".venv", "venv",
             ".idea", ".vscode", "logs", "dist", "build", "$recycle.bin",
             # 整盘遍历时跳掉这些，不然 find/grep 会慢到没法用
             "windows", "program files", "program files (x86)", "programdata",
             "appdata", "system volume information", "recovery", "perflogs",
             "$windows.~bt", "$windows.~ws", "msys64", "wsl"} 


def walk_files(root: Path, cfg: dict, include_hidden: bool = False, include_denied: bool = False):
    """在 root 下遍历文件，自动跳过敏感目录 / 缓存目录 / （可选）隐藏目录。

    include_denied=True 时只「列出」受保护文件（用于 find 显示 ⛔），绝不用于读内容。
    """
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        keep = []
        for name in dirnames:
            child = d / name
            if denied_by(child, cfg) and not include_denied:
                continue
            if name.lower() in SKIP_DIRS:
                continue
            if not include_hidden and name.startswith("."):
                continue
            keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            if not include_hidden and name.startswith("."):
                continue
            f = d / name
            if denied_by(f, cfg) and not include_denied:
                continue
            scanned += 1
            if scanned > cfg["grep_max_files"]:
                return
            yield f


# ------------------------------------------------------------------ read
def cmd_read(args) -> int:
    cfg = load_config()
    p, err = resolve_path(getattr(args, "path", ""), cfg)
    if err:
        print(f"[错误] {err}")
        return 1
    if not p.exists():
        print(f"[错误] 文件不存在：{p}")
        return 1
    if p.is_dir():
        print(f"[提示] 这是目录，不是文件。请改用 list：{p}")
        return 1

    size = p.stat().st_size
    if size > cfg["max_file_mb"] * 1024 * 1024:
        print(f"[错误] 文件太大（{size / 1024 / 1024:.1f} MB > {cfg['max_file_mb']} MB），不读。")
        return 1
    if looks_binary(p):
        print(f"[错误] 这看起来是二进制文件（图片 / 程序 / 压缩包 / 数据库等），读不出文本：{p.name}")
        return 1

    text = read_text(p)
    lines = text.splitlines()
    total = len(lines)
    start = max(1, int(getattr(args, "start", 1) or 1))
    if total and start > total:
        print(f"[错误] 起始行 {start} 超过文件总行数 {total}")
        return 1
    want = int(getattr(args, "lines", 0) or 0)
    end = total if want <= 0 else min(total, start + want - 1)
    body = "\n".join(lines[start - 1:end]) if total else ""

    limit = cfg["max_output_chars"]
    tail = ""
    if len(body) > limit:
        shown = body[:limit]
        printed_lines = shown.count("\n") + 1
        body = shown
        tail = (f"\n\n……（本次已截断；文件还有内容。用 --start {start + printed_lines} 继续读下一段，"
                "或先用 --lines 指定更小的行数。）")

    print(f"文件：{p}")
    print(f"总计 {total} 行 / {len(text):,} 字｜本次输出：第 {start}~{end} 行\n")
    print(body if body else "（空文件）")
    if tail:
        print(tail)
    return 0


# ------------------------------------------------------------------ list
def cmd_list(args) -> int:
    cfg = load_config()
    raw = getattr(args, "path", "") or cfg.get("default_path") or cfg["roots"][0]
    p, err = resolve_path(raw, cfg)
    if err:
        print(f"[错误] {err}")
        return 1
    if not p.exists():
        print(f"[错误] 目录不存在：{p}")
        return 1
    if p.is_file():
        print(f"（{p.name} 是文件，不是目录；共 {p.stat().st_size:,} 字节。想看内容请用 read。）")
        return 0

    show_all = bool(getattr(args, "all", False))
    rows = []
    for child in sorted(p.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
        if not show_all and child.name.startswith("."):
            continue
        if child.is_dir():
            rows.append(f"{child.name}/")
        else:
            mark = "  ⛔受保护" if denied_by(child, cfg) else ""
            try:
                size = child.stat().st_size
                rows.append(f"{child.name}  ({size:,} B){mark}")
            except Exception:  # noqa: BLE001
                rows.append(f"{child.name}{mark}")

    cap = cfg["max_list_entries"]
    print(f"目录：{p}")
    print(f"共 {len(rows)} 项" + (f"（只显示前 {cap} 项）" if len(rows) > cap else "") + "\n")
    for r in rows[:cap]:
        print("  " + r)
    return 0


# ------------------------------------------------------------------ find
def cmd_find(args) -> int:
    cfg = load_config()
    root, err = resolve_path(getattr(args, "root", "") or cfg.get("default_path") or cfg["roots"][0], cfg)
    if err:
        print(f"[错误] {err}")
        return 1
    if not root.is_dir():
        print(f"[错误] 搜索起点不是目录：{root}")
        return 1

    pattern = (getattr(args, "pattern", "") or "*").strip()
    cap = int(getattr(args, "n", 0) or cfg["max_find_results"])

    # 通配符匹配文件（也支持用目录名模式，如 "*报告*"）
    import fnmatch

    hits = []
    for f in walk_files(root, cfg, include_hidden=False, include_denied=True):
        if fnmatch.fnmatch(f.name.lower(), pattern.lower()) or fnmatch.fnmatch(str(f).lower(), pattern.lower()):
            try:
                hits.append((f, f.stat().st_size))
            except Exception:  # noqa: BLE001
                hits.append((f, 0))
        if len(hits) >= cap:
            break

    print(f"搜索范围：{root}\n通配符：{pattern}")
    if not hits:
        print("\n（没找到匹配的文件。可换更宽的模式，比如 *关键字* 或 *.md）")
        return 0
    print(f"找到 {len(hits)} 个" + (f"（已到上限 {cap}）" if len(hits) >= cap else "") + "\n")
    for f, size in hits:
        try:
            rel = f.relative_to(root)
        except Exception:  # noqa: BLE001
            rel = f
        mark = "  ⛔受保护（读不了）" if denied_by(f, cfg) else ""
        print(f"  {rel}  ({size:,} B){mark}")
    return 0


# ------------------------------------------------------------------ grep
def cmd_grep(args) -> int:
    cfg = load_config()
    root, err = resolve_path(getattr(args, "root", "") or cfg.get("default_path") or cfg["roots"][0], cfg)
    if err:
        print(f"[错误] {err}")
        return 1
    if not root.is_dir():
        print(f"[错误] 搜索起点不是目录：{root}")
        return 1

    pat = getattr(args, "pattern", "") or ""
    if not pat.strip():
        print("[错误] 搜索内容为空")
        return 1
    try:
        rx = re.compile(pat, re.IGNORECASE)
    except re.error as e:
        print(f"[错误] 正则表达式有问题：{e}")
        return 1

    exts = [e.strip().lower() for e in (getattr(args, "ext", "") or "").split(",") if e.strip()]
    cap = int(getattr(args, "n", 0) or cfg["max_grep_matches"])
    max_mb = cfg["max_file_mb"]

    matches = []
    scanned = 0
    for f in walk_files(root, cfg):
        if exts and f.suffix.lower() not in exts:
            continue
        try:
            if f.stat().st_size > max_mb * 1024 * 1024 or looks_binary(f):
                continue
        except Exception:  # noqa: BLE001
            continue
        scanned += 1
        try:
            text = read_text(f)
        except Exception:  # noqa: BLE001
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if rx.search(line):
                try:
                    rel = f.relative_to(root)
                except Exception:  # noqa: BLE001
                    rel = f
                matches.append(f"{rel}:{i}: {line.strip()[:180]}")
                if len(matches) >= cap:
                    break
        if len(matches) >= cap:
            break

    print(f"搜索范围：{root}｜正则：{pat}" + (f"｜限定后缀：{','.join(exts)}" if exts else ""))
    print(f"扫描了 {scanned} 个文本文件\n")
    if not matches:
        print("（没有匹配。可以放宽正则、去掉 --ext 限定，或换个关键词。）")
        return 0
    print(f"命中 {len(matches)} 处" + (f"（已到上限 {cap}）" if len(matches) >= cap else "") + "：\n")
    for m in matches:
        print(f"  {m}")
    return 0


# ------------------------------------------------------------------ CLI
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="file_tools", description="读文件 / 列目录 / 找文件 / 搜内容")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("read", help="读文本文件")
    p.add_argument("path", help="文件路径（相对路径以第一个根目录为基准）")
    p.add_argument("--start", type=int, default=1, help="从第几行开始（1 起）")
    p.add_argument("--lines", type=int, default=0, help="读多少行（默认读到输出上限）")

    p = sub.add_parser("list", help="列目录")
    p.add_argument("path", nargs="?", default="", help="目录路径（默认第一个根目录）")
    p.add_argument("--all", action="store_true", help="包含隐藏项")

    p = sub.add_parser("find", help="按文件名找")
    p.add_argument("pattern", help="通配符，如 *.md / *报告* / config*")
    p.add_argument("--root", default="", help="搜索起点目录")
    p.add_argument("--n", type=int, default=0, help="最多返回条数")

    p = sub.add_parser("grep", help="在文件内容里搜")
    p.add_argument("pattern", help="正则表达式")
    p.add_argument("--root", default="", help="搜索起点目录")
    p.add_argument("--ext", default="", help="限定后缀，逗号分隔，如 .py,.md")
    p.add_argument("--n", type=int, default=0, help="最多返回条数")

    args = ap.parse_args(argv)
    return {
        "read": cmd_read,
        "list": cmd_list,
        "find": cmd_find,
        "grep": cmd_grep,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())

