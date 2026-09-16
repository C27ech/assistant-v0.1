#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图发QQ —— 独立 CLI 插件

功能：
    1. 截图：默认截取当前所有显示器屏幕（PIL.ImageGrab.grab(all_screens=True)）；
       也可以指定目标：
         --target all      整个虚拟桌面（默认，多显示器拼一张）
         --target screen   某一台显示器（--monitor N，N 见 --list-monitors）
         --target active   当前前台窗口
         --target window   指定窗口（--window 标题片段 / --hwnd 句柄；优先 PrintWindow，
                           窗口被别的窗口挡住也能截到）
         --target region   屏幕上一块矩形（--region x,y,w,h）
    2. 可通过 NapCat 的 OneBot11 正向 WebSocket 通道发送到指定 QQ 私聊（--no-send 可只截图）
    3. 发送完成后清理临时截图文件；用 --out 指定路径时会保留一份到该路径
    4. 只查询不截图：--list-windows（看看现在有哪些窗口）/ --list-monitors

依赖：
    - Pillow（已安装）
    - websocket-client（已安装）
    - 同目录 win_capture.py（窗口/显示器枚举与截图，纯 ctypes，无需 pywin32）
"""

import argparse
import base64
import datetime
import json
import os
import sys
import tempfile
import time

import websocket

import win_capture as wc

# 保证 Windows 控制台 / 管道下中文输出不因 GBK 编码报错
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")

DEFAULTS = {
    "ws_url": "ws://127.0.0.1:3001",
    "token": "",
    # 改成你自己的 QQ 号（也可以用 config.json 或命令行覆盖）。
    # 0 表示未配置，运行时会直接报错提醒，不会误发。
    "user_id": 0,
    "delete_temp_files": True,
    "timeout": 10.0,
    "image_format": "PNG",
    "temp_dir": None,
    "jpeg_quality": 85,
    # 图过大时自动等比缩小（只缩不放）：超过 max_side / max_pixels 就缩到限制内。
    "auto_fit": {
        "enabled": True,
        "max_side": 2560,
        "max_pixels": 8000000,
    },
}

# 外部调用可依据退出码判断结果
EXIT_OK = 0        # 成功
EXIT_CONFIG = 1    # 参数 / 配置错误
EXIT_CAPTURE = 2   # 截图失败（没有任何可用屏幕截图）
EXIT_CONNECT = 3   # WebSocket 连接失败 / 超时
EXIT_SEND = 4      # 发送（或读取截图数据）失败
EXIT_RESPONSE = 5  # NapCat 返回错误
EXIT_TARGET = 6    # 截图目标无效（窗口找不到 / 区域越界 / 显示器序号越界）
EXIT_UNSUPPORTED = 7  # 当前系统不支持该目标（非 Windows 的窗口截图）


# 兼容旧名：截图阶段错误现在统一用 win_capture 的异常类型
CaptureError = wc.WinCaptureError


def log(msg):
    print("[截图发QQ] " + str(msg), file=sys.stderr, flush=True)


def _to_bool(value, default):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "y")
    return bool(value)


def _to_int(value):
    if isinstance(value, bool):
        raise ValueError(value)
    return int(value)


def load_config(args):
    """合并 内置默认值 -> config.json -> CLI 覆盖，返回配置 dict；失败返回 None。"""
    cfg = dict(DEFAULTS)

    config_path = args.config if args.config else DEFAULT_CONFIG_PATH
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            log("读取配置文件失败 %s：%s" % (config_path, exc))
            return None
        if not isinstance(data, dict):
            log("配置文件格式错误（顶层必须是 JSON 对象）：%s" % config_path)
            return None
        for key in DEFAULTS:
            if key in data:
                cfg[key] = data[key]
    else:
        if args.config:
            log("指定的配置文件不存在：%s" % config_path)
            return None
        log("未找到默认配置文件 %s，使用内置默认值" % config_path)

    # auto_fit 是嵌套配置：逐键合并，避免用户只写一项就丢掉其它默认值
    auto_fit = dict(DEFAULTS["auto_fit"])
    raw_auto_fit = cfg.get("auto_fit")
    if isinstance(raw_auto_fit, dict):
        for key in auto_fit:
            if raw_auto_fit.get(key) is not None:
                auto_fit[key] = raw_auto_fit[key]
    elif raw_auto_fit is not None:
        auto_fit["enabled"] = raw_auto_fit
    cfg["auto_fit"] = auto_fit

    # CLI 覆盖
    if args.user_id is not None:
        cfg["user_id"] = args.user_id
    if args.ws_url is not None:
        cfg["ws_url"] = args.ws_url
    if args.token is not None:
        cfg["token"] = args.token
    if args.timeout is not None:
        cfg["timeout"] = args.timeout
    if args.keep_temp:
        cfg["delete_temp_files"] = False
    if args.no_auto_fit is not None:
        cfg["auto_fit"]["enabled"] = not _to_bool(args.no_auto_fit, True)
    elif args.auto_fit is not None:
        cfg["auto_fit"]["enabled"] = _to_bool(args.auto_fit, True)

    # 校验
    try:
        cfg["user_id"] = _to_int(cfg["user_id"])
        cfg["timeout"] = float(cfg["timeout"])
    except Exception as exc:
        log("配置值类型错误：%s" % exc)
        return None

    # 不发送时（--no-send / 只查询）不强制要求配置 user_id，方便先试截图
    if not getattr(args, "no_send", False) and cfg["user_id"] <= 0:
        log("user_id 必须为正整数，当前为 %r（先在 config.json 里填你自己的 QQ 号）" % cfg["user_id"])
        return None

    cfg["ws_url"] = str(cfg["ws_url"] or "")
    if not cfg["ws_url"].lower().startswith(("ws://", "wss://")):
        log("ws_url 必须以 ws:// 或 wss:// 开头，当前为 %r" % cfg["ws_url"])
        return None

    if cfg["timeout"] <= 0:
        log("timeout 必须大于 0，当前为 %r" % cfg["timeout"])
        return None

    cfg["token"] = "" if cfg["token"] is None else str(cfg["token"])
    cfg["delete_temp_files"] = _to_bool(cfg.get("delete_temp_files"), True)
    cfg["image_format"] = str(cfg.get("image_format") or "PNG").upper()
    cfg["temp_dir"] = cfg.get("temp_dir") or None
    try:
        cfg["jpeg_quality"] = int(cfg.get("jpeg_quality") or 85)
    except (TypeError, ValueError):
        log("jpeg_quality 必须是整数，当前为 %r" % cfg.get("jpeg_quality"))
        return None
    if not 1 <= cfg["jpeg_quality"] <= 100:
        log("jpeg_quality 必须在 1~100 之间，当前为 %r" % cfg["jpeg_quality"])
        return None

    if cfg["temp_dir"] is not None:
        cfg["temp_dir"] = os.path.abspath(str(cfg["temp_dir"]))
        try:
            os.makedirs(cfg["temp_dir"], exist_ok=True)
        except Exception as exc:
            log("无法创建临时目录 %s：%s" % (cfg["temp_dir"], exc))
            return None

    # auto_fit 校验（超大分辨率自适应）
    try:
        cfg["auto_fit"] = {
            "enabled": _to_bool(cfg["auto_fit"].get("enabled"), True),
            "max_side": int(cfg["auto_fit"].get("max_side") or 2560),
            "max_pixels": int(cfg["auto_fit"].get("max_pixels") or 8000000),
        }
    except (TypeError, ValueError, AttributeError):
        log("auto_fit.max_side / auto_fit.max_pixels 必须是整数")
        return None
    if cfg["auto_fit"]["max_side"] < 64 or cfg["auto_fit"]["max_pixels"] < 10000:
        log("auto_fit 配置过小（max_side 至少 64，max_pixels 至少 10000）")
        return None

    return cfg


# --------------------------------------------------------------------------- #
# 目标解析 / 按目标截图
# --------------------------------------------------------------------------- #


def _parse_bool(value, default=False):
    """把 CLI / 占位符传进来的 true/false/1/0/yes/no 解析成 bool。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "y")


def _arg_bool(text):
    """argparse 的 type：把 true/false/1/0/yes/no 转成 bool（支持 --flag=false 写法）。"""
    return _parse_bool(text, False)


def parse_hwnd(text):
    """解析窗口句柄：支持十进制与 0x 十六进制，空值返回 None。"""
    if text in (None, ""):
        return None
    s = str(text).strip()
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s, 10)
    except ValueError:
        raise wc.WinCaptureError(
            "窗口句柄必须是整数（十进制或 0x 十六进制），当前为 %r" % text
        )


def describe_target(job) -> dict:
    """把任务描述成可放进 JSON 摘要的 dict。"""
    if job["kind"] == "window":
        info = job["info"]
        return {"kind": "window", "title": info["title"], "hwnd": info["hwnd_hex"],
                "process": info.get("process_name", "")}
    if job["kind"] == "region":
        x, y, w, h = job["region"]
        return {"kind": "region", "x": x, "y": y, "w": w, "h": h}
    if job["kind"] == "monitor":
        return {"kind": "monitor", "monitor": job["monitor"]}
    return {"kind": "all"}


def resolve_targets(args) -> list:
    """把命令行参数解析成「待截图任务」列表（这一步只定位目标，不截图）。

    每项：{"kind", "label", "info"/"region"/"monitor"}，kind ∈ all/screen/window/region。
    """
    target = str(args.target or "all").strip().lower()
    if args.region and target == "all":
        target = "region"     # 只给了 --region 就按区域截，省得调用方还得多传 target

    if target in ("all", "screens", "desktop"):
        return [{"kind": "all", "label": "全部显示器"}]

    if target in ("screen", "monitor"):
        mon = "1" if args.monitor is None else str(args.monitor).strip().lower()
        if mon in ("all", "*"):
            return [{"kind": "monitor", "monitor": m["index"],
                     "label": "显示器%d_%s" % (m["index"], wc.safe_filename(m["device"], "mon"))}
                    for m in wc.monitors()]
        try:
            idx = int(mon)
        except ValueError:
            raise wc.WinCaptureError(
                "显示器序号必须是整数或 all，当前为 %r（可用 --list-monitors 查看）" % args.monitor
            )
        mons = wc.monitors()
        if idx < 1 or idx > len(mons):
            raise wc.WinCaptureError(
                "显示器序号 %d 越界：本机共 %d 台（%s）"
                % (idx, len(mons), "、".join(m["device"] for m in mons))
            )
        return [{"kind": "monitor", "monitor": idx, "label": "显示器%d" % idx}]

    if target == "region":
        if not args.region:
            raise wc.WinCaptureError(
                "target=region 需要 --region x,y,w,h（起点 x,y + 宽高，屏幕物理像素）"
            )
        x, y, w, h = wc.parse_region(args.region)
        return [{"kind": "region", "region": (x, y, w, h),
                 "label": "区域_%d_%d_%d_%d" % (x, y, w, h)}]

    if target in ("window", "active", "foreground"):
        hwnd = parse_hwnd(args.hwnd)
        if hwnd is not None:
            info = wc.window_info(hwnd)
            if not info["visible"] or info["window_rect"][2] <= 0 or info["window_rect"][3] <= 0:
                raise wc.WinCaptureError(
                    "hwnd %s 不是有效窗口（不可见或尺寸为 0）：标题=%r。"
                    "可用 --list-windows 查看当前窗口的 hwnd / 标题"
                    % (info["hwnd_hex"], info["title"])
                )
        elif args.window:
            info = wc.find_window(str(args.window),
                                  use_regex=_parse_bool(args.window_regex, False))
        elif target == "active":
            info = wc.foreground_window()
        else:
            raise wc.WinCaptureError(
                "target=window 需要 --window 标题片段（也可用 --hwnd，或 --target active 截当前前台窗口）；"
                "不确定窗口标题时先跑 --list-windows"
            )
        return [{"kind": "window", "info": info,
                 "label": "窗口_%s" % wc.safe_filename(info["title"], info["hwnd_hex"])}]

    raise wc.WinCaptureError(
        "不认识的 target=%r（可选 all / screen / active / window / region）" % args.target
    )


def capture_jobs(jobs, args) -> list:
    """按任务列表截图，返回 [{"label", "image", "how", "rect", "notes", "target"}]。"""
    client_area = _parse_bool(args.client_area, False)
    prefer_pw = not _parse_bool(args.no_printwindow, False)
    results = []
    for job in jobs:
        pre_notes = []
        kind = job["kind"]
        if kind == "all":
            res = wc.capture_all_screens()
        elif kind == "monitor":
            res = wc.capture_monitor(job["monitor"])
        elif kind == "region":
            res = wc.capture_region(*job["region"])
        else:
            info = job["info"]
            if _parse_bool(args.activate, False):
                ok = wc.activate_window(info["hwnd"])
                pre_notes.append("已把窗口切到前台" if ok
                                 else "尝试切前台失败（系统前台锁定），按当前状态截图")
                info = wc.window_info(info["hwnd"])   # 还原/切换后位置可能变了，重新取
            res = wc.capture_window(info, client_area=client_area,
                                    prefer_printwindow=prefer_pw)
        res["label"] = job["label"]
        res["target"] = describe_target(job)
        res["notes"] = pre_notes + list(res.get("notes") or [])
        results.append(res)
    return results


IMAGE_SUFFIX = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp", "BMP": ".bmp"}


def image_format_of(args, cfg) -> str:
    """确定图片格式：--format > config.json 的 image_format > PNG。"""
    fmt = str(args.format or cfg["image_format"] or "PNG").upper()
    if fmt == "JPG":
        fmt = "JPEG"
    if fmt not in IMAGE_SUFFIX:
        raise wc.WinCaptureError(
            "不支持的图片格式 %r（可用 %s）" % (args.format, " / ".join(IMAGE_SUFFIX))
        )
    return fmt


def save_image(img, path, fmt, quality):
    """按格式保存图片；JPEG/WEBP 带质量参数（JPEG 不支持透明，先转 RGB）。"""
    if fmt in ("JPEG", "WEBP"):
        img.convert("RGB").save(path, fmt, quality=int(quality))
    else:
        img.save(path, fmt)


def save_screen_to_temp(img, index, cfg, fmt=None, quality=None):
    """把一张图保存为临时文件，返回路径；失败抛异常并清理残留。"""
    fmt = fmt or cfg["image_format"]
    suffix = IMAGE_SUFFIX.get(fmt, "." + str(fmt).lower())
    fd, path = tempfile.mkstemp(
        prefix="screenshot2qq_screen%d_" % index,
        suffix=suffix,
        dir=cfg["temp_dir"],
    )
    os.close(fd)
    try:
        save_image(img, path, fmt,
                   quality if quality is not None else cfg["jpeg_quality"])
    except Exception:
        try:
            os.remove(path)
        except Exception:
            pass
        raise
    return path


def build_out_path(out, label, index, fmt, count) -> str:
    """算 --out 的落盘路径：给目录（或没写扩展名的路径）就自动起名，给文件名就按序号加后缀。"""
    ext = IMAGE_SUFFIX.get(fmt, ".png")
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = "%s_%s" % (wc.safe_filename(label, "capture"), stamp)
    p = os.path.abspath(os.path.expanduser(str(out)))
    if os.path.isdir(p) or not os.path.splitext(p)[1]:
        os.makedirs(p, exist_ok=True)
        return os.path.join(p, name + ext)
    directory = os.path.dirname(p)
    if directory:
        os.makedirs(directory, exist_ok=True)
    stem, fext = os.path.splitext(p)
    if count > 1:
        return "%s_%d%s" % (stem, index, fext)
    return p


def write_out_copy(img, out, label, index, count, fmt, quality) -> str:
    """把图另存到 --out 指定位置（临时文件照旧会被清理，这份会保留）。"""
    path = build_out_path(out, label, index, fmt, count)
    save_image(img, path, fmt, quality)
    return path


def apply_auto_fit(img, cfg):
    """图过大时等比缩小（只缩不放）。

    触发条件（任一满足）：最长边 > auto_fit.max_side，或总像素 > auto_fit.max_pixels。
    默认限制下 1920x1200（2.3MP、长边 1920）不触发；3840x2160 会缩到 2560x1440。

    :returns: ``(img, info)``；未缩放时 info 为 None。
    """
    af = cfg.get("auto_fit") if isinstance(cfg, dict) else None
    if not af or not _to_bool(af.get("enabled"), True):
        return img, None

    max_side = int(af.get("max_side") or 2560)
    max_pixels = int(af.get("max_pixels") or 8000000)
    width, height = int(img.width), int(img.height)

    if max(width, height) <= max_side and width * height <= max_pixels:
        return img, None

    fitted = wc.resize_image(img, max_width=max_side, max_height=max_side)
    if fitted.width * fitted.height > max_pixels:
        factor = (max_pixels / float(fitted.width * fitted.height)) ** 0.5
        fitted = wc.resize_image(fitted, scale=factor)

    if fitted.size == img.size:
        return img, None
    return fitted, {
        "from": [width, height],
        "to": [fitted.width, fitted.height],
        "max_side": max_side,
        "max_pixels": max_pixels,
    }


def build_summary(results, out_paths, send_state, fmt, quality, profile=None) -> str:
    """结果文本摘要（含分辨率 / 缩放 / auto_fit 信息）。"""
    lines = []
    if results:
        lines.append("已截图 %d 张：" % len(results))
    for i, r in enumerate(results, start=1):
        img = r["image"]
        target = r.get("target") or {}
        if target.get("kind") == "window":
            who = "窗口「%s」(hwnd=%s, 进程=%s)" % (
                target.get("title", ""), target.get("hwnd", ""), target.get("process", ""))
        elif target.get("kind") == "region":
            who = "屏幕区域 (%s,%s,%s,%s)" % (target.get("x"), target.get("y"),
                                          target.get("w"), target.get("h"))
        elif target.get("kind") == "monitor":
            who = "显示器 %s" % target.get("monitor")
        else:
            who = "全部显示器"
        extra = ("；备注：" + "；".join(str(n) for n in r.get("notes") or [])) if r.get("notes") else ""
        lines.append("%d. %s → %s，尺寸 %dx%d，%s%s"
                     % (i, who, r["label"], img.width, img.height, r["how"], extra))
    if out_paths:
        lines.append("已另存文件：" + "；".join(out_paths))
    if results:
        lines.append("图片格式：%s%s" % (fmt, "（质量 %d）" % quality if fmt in ("JPEG", "WEBP") else ""))
    screen_line = _summarize_profile(profile)
    if screen_line:
        lines.append("屏幕：" + screen_line)
    lines.append(send_state)
    return "\n".join(lines)


def _summarize_profile(profile) -> str:
    """屏幕画像的单行摘要；取不到（非 Windows / 异常）时返回空串。"""
    if isinstance(profile, dict):
        try:
            return wc.format_screen_profile(profile)
        except Exception:
            return ""
    try:
        return wc.format_screen_profile()
    except Exception:  # noqa: BLE001 - 摘要不该因为取屏幕信息失败而中断
        return ""


def build_summary_json(results, out_paths, send_state, fmt, quality, profile=None) -> str:
    """机器可读的结果摘要（--json 用，含屏幕分辨率 / 缩放信息）。"""
    payload = {
        "ok": True,
        "count": len(results),
        "format": fmt,
        "quality": quality if fmt in ("JPEG", "WEBP") else None,
        "shots": [
            {
                "label": r["label"],
                "target": r.get("target"),
                "how": r["how"],
                "size": [r["image"].width, r["image"].height],
                "rect": r.get("rect"),
                "auto_fit": r.get("auto_fit"),
                "notes": r.get("notes") or [],
            }
            for r in results
        ],
        "files": list(out_paths),
        "screen": _profile_json(profile),
        "send": send_state,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _profile_json(profile=None):
    """屏幕画像的 JSON 友好子集（分辨率 / 缩放 / 显示器）。"""
    try:
        p = profile if isinstance(profile, dict) else wc.screen_profile()
    except Exception:  # noqa: BLE001 - 非 Windows / 取不到时不阻塞结果输出
        return None
    return {
        "physical": list(p.get("physical") or []),
        "logical": list(p.get("logical") or []),
        "scale": p.get("scale"),
        "scale_percent": p.get("scale_percent"),
        "dpi": p.get("dpi"),
        "dpi_mode": p.get("dpi_mode"),
        "monitor_count": p.get("monitor_count"),
        "multi_monitor": p.get("multi_monitor"),
        "virtual_rect": list(p.get("virtual_rect") or []),
        "monitors": [
            {
                "index": m.get("index"),
                "device": m.get("device"),
                "primary": m.get("primary"),
                "rect": list(m.get("rect") or []),
                "dpi": m.get("dpi"),
                "scale": m.get("scale"),
            }
            for m in (p.get("monitors") or [])
        ],
    }


def build_message_segments(temp_paths):
    """把临时截图读成 bytes -> base64，生成 OneBot11 array 消息段（对象数组）。

    修复：NapCat 不解析数组内的 CQ 字符串（实测报错 retcode=1200
    “未知的消息类型：undefined”），必须使用标准消息段对象：
    {"type": "image", "data": {"file": "base64://..."}}。
    """
    segments = []
    for path in temp_paths:
        try:
            with open(path, "rb") as f:
                raw = f.read()
            b64 = base64.b64encode(raw).decode("ascii")
            segments.append(
                {"type": "image", "data": {"file": "base64://%s" % b64}}
            )
        except Exception as exc:
            log("读取截图文件失败，已跳过该屏 %s：%s" % (path, exc))
            continue
    return segments


def send_via_ws(cfg, segments):
    """连上 NapCat OneBot11 正向 WS，发送一条 send_private_msg 后关闭连接。"""
    ws = None
    raw_response = None

    try:
        ws_kwargs = {"timeout": cfg["timeout"]}
        if cfg["token"]:
            ws_kwargs["header"] = {"Authorization": "Bearer " + cfg["token"]}

        log("正在连接 WebSocket：%s" % cfg["ws_url"])
        ws = websocket.create_connection(cfg["ws_url"], **ws_kwargs)

        payload = {
            "action": "send_private_msg",
            "params": {
                "user_id": cfg["user_id"],
                "message": segments,
            },
            "echo": "screenshot2qq",
        }
        log("已连接，发送 send_private_msg（%d 段图片）" % len(segments))
        ws.send(json.dumps(payload, ensure_ascii=False))
        log("等待 NapCat 响应...")

        # 修复：NapCat 正向 WS 连接建立后会先推送 lifecycle meta_event
        # （sub_type=connect），期间也可能有心跳/通知等推送。若只 recv() 一次，
        # 会把 meta_event 误当成 send_private_msg 的 API 响应而误报失败。
        # 这里循环读取，跳过非 API 响应，直到拿到带 echo 匹配（或含 status/retcode）
        # 的真实响应，或在超时时间内等不到。
        deadline = time.time() + cfg["timeout"]
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                log("等待响应超时（%.1fs），未收到匹配 echo=%r 的 API 响应"
                    % (cfg["timeout"], payload["echo"]))
                return EXIT_SEND
            try:
                ws.settimeout(min(remaining, 1.0))
                msg = ws.recv()
            except websocket.WebSocketTimeoutException:
                # 单次 1s 内无数据，回到循环顶部继续等待，直到整体超时
                continue
            except Exception as exc:
                log("WebSocket 发送或接收失败：%s" % exc)
                return EXIT_SEND

            try:
                obj = json.loads(msg)
            except Exception as exc:
                log("收到非 JSON 消息，已跳过：%s（%r）" % (exc, msg[:200]))
                continue

            if not isinstance(obj, dict):
                continue
            # 命中本请求携带的 echo，即真正的 API 响应
            if obj.get("echo") == payload["echo"]:
                raw_response = msg
                break
            # 兼容不回显 echo 的网关：同时含 status 与 retcode 的即为 API 响应
            if obj.get("echo") is None and "status" in obj and "retcode" in obj:
                raw_response = msg
                break
            # 其余推送（lifecycle/heartbeat/notice/request）一律跳过
            log("跳过非响应消息：post_type=%r, meta_event_type=%r, sub_type=%r"
                % (obj.get("post_type"), obj.get("meta_event_type"),
                   obj.get("sub_type")))
    except Exception as exc:
        # 区分连接阶段和发送/接收阶段
        if raw_response is None and ws is None:
            log("WebSocket 连接失败/超时：%s" % exc)
            return EXIT_CONNECT
        log("WebSocket 发送或接收失败：%s" % exc)
        return EXIT_SEND
    finally:
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    if raw_response is None:
        log("未收到 NapCat 响应")
        return EXIT_SEND

    try:
        resp = json.loads(raw_response)
    except Exception as exc:
        log("NapCat 响应不是合法 JSON（%s）：%r" % (exc, raw_response))
        return EXIT_RESPONSE

    status = resp.get("status")
    retcode = resp.get("retcode")
    echo = resp.get("echo")
    if status != "ok" or retcode != 0:
        log("发送失败，NapCat 返回 status=%r, retcode=%r，原始响应=%r"
            % (status, retcode, raw_response))
        return EXIT_RESPONSE

    log("发送成功：echo=%r, status=%r, retcode=%r，响应=%s"
        % (echo, status, retcode, json.dumps(resp, ensure_ascii=False)))
    return EXIT_OK


def cleanup_temp_files(temp_paths, cfg):
    """按配置删除或保留临时截图文件。"""
    if cfg["delete_temp_files"]:
        for path in temp_paths:
            try:
                os.remove(path)
                log("已删除临时文件：%s" % path)
            except Exception as exc:
                log("删除临时文件失败 %s：%s" % (path, exc))
    else:
        for path in temp_paths:
            log("保留临时文件：%s" % path)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="screenshot2qq",
        description="截屏（可指定窗口/显示器/区域），并通过 NapCat OneBot11 正向 WebSocket "
                    "发送到指定 QQ 私聊；也可以只查询窗口/显示器列表而不截图。",
    )
    parser.add_argument(
        "--config",
        default=None,
        help="配置文件路径（默认：脚本同目录 config.json）",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="覆盖目标 QQ 号（默认：config.json 里的 user_id）",
    )
    parser.add_argument(
        "--ws-url",
        default=None,
        help="覆盖 WebSocket 地址（默认：ws://127.0.0.1:3001）",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="覆盖 access token；为空则不发送鉴权头（默认：空）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="WebSocket 连接/收发超时秒数（默认：10）",
    )
    parser.add_argument(
        "--keep-temp",
        action="store_true",
        help="保留临时截图文件（默认发送后删除）",
    )

    g = parser.add_argument_group("截图目标")
    g.add_argument(
        "--target",
        default="all",
        help="截哪里：all=全部显示器（默认）/ screen=指定显示器 / active=当前前台窗口 / "
             "window=指定窗口 / region=屏幕上一块区域",
    )
    g.add_argument("--window", default=None,
                   help="窗口标题片段（target=window 用；先 --list-windows 看标题）")
    g.add_argument("--hwnd", default=None,
                   help="窗口句柄，十进制或 0x 十六进制（比标题更精确）")
    g.add_argument("--window-regex", nargs="?", const=True, default=False, type=_arg_bool,
                   help="--window 按正则匹配（默认按子串匹配）")
    g.add_argument("--monitor", default=None,
                   help="显示器序号（target=screen 用；all=每台各截一张），见 --list-monitors")
    g.add_argument("--region", default=None,
                   help="屏幕区域 x,y,w,h（物理像素；target=region 用。单独给 --region 也会按区域截）")
    g.add_argument("--client-area", nargs="?", const=True, default=False, type=_arg_bool,
                   help="只截客户区（去掉标题栏/边框），仅窗口目标有效")
    g.add_argument("--activate", nargs="?", const=True, default=False, type=_arg_bool,
                   help="截图前把目标窗口切到前台（会动用户桌面，默认不动）")
    g.add_argument("--no-printwindow", nargs="?", const=True, default=False, type=_arg_bool,
                   help="不用 PrintWindow，直接按屏幕区域截窗口（调试用）")
    g.add_argument("--delay", type=float, default=0.0,
                   help="截图前先等几秒（等界面稳定/等切换生效）")

    h = parser.add_argument_group("输出")
    h.add_argument("--scale", type=float, default=None,
                   help="按倍数缩放（如 0.5 缩小一半；1=原尺寸）")
    h.add_argument("--max-width", type=int, default=None, help="最宽不超过该像素（只缩不放）")
    h.add_argument("--max-height", type=int, default=None, help="最高不超过该像素（只缩不放）")
    h.add_argument("--format", default=None,
                   help="图片格式 PNG / JPEG / WEBP / BMP（默认取 config.json 的 image_format）")
    h.add_argument("--quality", type=int, default=None,
                   help="JPEG/WEBP 质量 1~100（默认取 config.json 的 jpeg_quality）")
    h.add_argument("--out", default=None,
                   help="把截图另存到这个文件或目录（给了就保留文件，并在结果里报出路径）")
    h.add_argument("--no-send", action="store_true",
                   help="只截图/落盘，不连 NapCat、不发 QQ")
    h.add_argument("--auto-fit", nargs="?", const=True, default=None, type=_arg_bool,
                   help="图过大时自动缩小（默认开；见 config.json 的 auto_fit）")
    h.add_argument("--no-auto-fit", nargs="?", const=True, default=None, type=_arg_bool,
                   help="关闭自动缩小：再大的图也按原尺寸发送")
    h.add_argument("--json", action="store_true",
                   help="结果用 JSON 打印到 stdout（方便程序解析）")

    q = parser.add_argument_group("只查询")
    q.add_argument("--list-windows", action="store_true",
                   help="列出当前可见窗口（标题 / hwnd / 进程 / 位置尺寸），不截图")
    q.add_argument("--list-monitors", action="store_true",
                   help="列出显示器（序号 / 分辨率 / 位置 / 缩放），不截图")
    q.add_argument("--filter", default=None, help="--list-windows 的过滤词（标题/进程/类名子串）")
    q.add_argument("--filter-regex", nargs="?", const=True, default=False, type=_arg_bool,
                   help="--filter 按正则匹配标题")
    q.add_argument("--include-minimized", nargs="?", const=True, default=False, type=_arg_bool,
                   help="列表里包含最小化的窗口")
    q.add_argument("--all-windows", nargs="?", const=True, default=False, type=_arg_bool,
                   help="列表里包含工具窗口（默认过滤掉不可见的工具窗口）")
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    # 0) 纯查询模式：只看窗口 / 显示器，不截图、不发消息
    if args.list_windows or args.list_monitors:
        try:
            blocks = []
            if args.list_monitors:
                blocks.append(wc.format_monitor_list(wc.monitors()))
            if args.list_windows:
                wins = wc.list_windows(
                    filter_text=args.filter,
                    use_regex=_parse_bool(args.filter_regex, False),
                    include_minimized=_parse_bool(args.include_minimized, False),
                    include_tool_windows=_parse_bool(args.all_windows, False),
                )
                blocks.append(wc.format_window_list(wins))
        except wc.WinCaptureUnsupported as exc:
            log(str(exc))
            return EXIT_UNSUPPORTED
        except wc.WinCaptureError as exc:
            log(str(exc))
            return EXIT_TARGET
        print("\n\n".join(blocks))
        return EXIT_OK

    cfg = load_config(args)
    if cfg is None:
        return EXIT_CONFIG

    try:
        fmt = image_format_of(args, cfg)
        quality = cfg["jpeg_quality"] if args.quality is None else int(args.quality)
        if not 1 <= quality <= 100:
            raise wc.WinCaptureError("--quality 必须在 1~100 之间，当前为 %r" % args.quality)
    except wc.WinCaptureError as exc:
        log(str(exc))
        return EXIT_CONFIG

    log("配置：ws_url=%s, user_id=%s, token=%s, timeout=%s, 目标=%s, 格式=%s, DPI=%s"
        % (cfg["ws_url"], cfg["user_id"], ("已设置" if cfg["token"] else "空"),
           cfg["timeout"], args.target or "all", fmt, wc.DPI_MODE))

    # 屏幕画像：分辨率 / 缩放 / 多显示器（截图尺寸与坐标解读都按它）
    try:
        profile = wc.screen_profile()
        log("屏幕：" + wc.format_screen_profile(profile))
        log("自适应缩放(auto_fit)：%s（max_side=%s, max_pixels=%s）"
            % ("开" if cfg["auto_fit"]["enabled"] else "关",
               cfg["auto_fit"]["max_side"], cfg["auto_fit"]["max_pixels"]))
    except Exception as exc:  # noqa: BLE001 - 取不到画像不影响截图本身
        profile = None
        log("读取屏幕画像失败（不影响截图）：%s" % exc)

    if args.no_send:
        log("--no-send：只截图，不连 NapCat、不发 QQ")

    if args.delay and float(args.delay) > 0:
        log("先等待 %.2f 秒再截图（等界面稳定）" % float(args.delay))
        time.sleep(float(args.delay))

    # 1) 定位截图目标
    try:
        jobs = resolve_targets(args)
    except wc.WinCaptureUnsupported as exc:
        log(str(exc))
        return EXIT_UNSUPPORTED
    except wc.WinCaptureError as exc:
        log(str(exc))
        return EXIT_TARGET
    for job in jobs:
        log("目标：%s（%s）" % (job["label"], job["kind"]))

    # 2) 截图
    try:
        results = capture_jobs(jobs, args)
    except wc.WinCaptureError as exc:
        log(str(exc))
        return EXIT_CAPTURE
    except Exception as exc:  # noqa: BLE001
        log("截图失败：%s: %s" % (type(exc).__name__, exc))
        return EXIT_CAPTURE

    # 3) 缩放 + 落盘（临时文件给 NapCat 发；--out 的那份保留给用户/其它工具）
    do_resize = (args.scale is not None or args.max_width is not None
                 or args.max_height is not None)
    if not do_resize and cfg["auto_fit"]["enabled"]:
        log("图过大时会缩到最长边 %d / 总像素 %d 以内（--no-auto-fit 可关）"
            % (cfg["auto_fit"]["max_side"], cfg["auto_fit"]["max_pixels"]))
    elif do_resize and cfg["auto_fit"]["enabled"]:
        log("已显式指定缩放参数，本次跳过 auto_fit")

    temp_paths = []
    out_paths = []
    for idx, r in enumerate(results, start=1):
        img = r["image"]
        if do_resize:
            try:
                before = img.size
                img = wc.resize_image(
                    img,
                    scale=(1.0 if args.scale is None else args.scale),
                    max_width=args.max_width,
                    max_height=args.max_height,
                )
                r["image"] = img
                if img.size != before:
                    r["notes"] = list(r.get("notes") or []) + [
                        "已缩放 %dx%d → %dx%d" % (before[0], before[1], img.width, img.height)
                    ]
            except wc.WinCaptureError as exc:
                log("第 %d 张缩放失败，按原尺寸继续：%s" % (idx, exc))
        elif cfg["auto_fit"]["enabled"]:
            # 图过大时自动缩小
            fitted, fit_info = apply_auto_fit(img, cfg)
            if fit_info:
                r["image"] = fitted
                r["auto_fit"] = fit_info
                r["notes"] = list(r.get("notes") or []) + [
                    "超大图自适应缩放 %dx%d → %dx%d（auto_fit）"
                    % (fit_info["from"][0], fit_info["from"][1],
                       fit_info["to"][0], fit_info["to"][1])
                ]
                log("第 %d 张触发 auto_fit：%dx%d → %dx%d"
                    % (idx, fit_info["from"][0], fit_info["from"][1],
                       fit_info["to"][0], fit_info["to"][1]))
        img = r["image"]
        try:
            path = save_screen_to_temp(img, idx, cfg, fmt=fmt, quality=quality)
            temp_paths.append(path)
            log("第 %d 张已写临时文件：%s（%dx%d，%s）"
                % (idx, path, img.width, img.height, r["how"]))
        except Exception as exc:  # noqa: BLE001
            log("第 %d 张保存失败，已跳过：%s" % (idx, exc))
            continue
        if args.out:
            try:
                out_paths.append(
                    write_out_copy(img, args.out, r["label"], idx, len(results), fmt, quality)
                )
            except Exception as exc:  # noqa: BLE001
                log("第 %d 张另存到 --out 失败（不影响发送）：%s" % (idx, exc))

    if not temp_paths:
        log("所有截图都没能写出文件，没有可发送的内容")
        return EXIT_CAPTURE

    # 4) 发送（--no-send 时跳过）
    send_state = "未发送（--no-send，图片只留在本机）"
    rc = EXIT_OK
    if not args.no_send:
        segments = build_message_segments(temp_paths)
        if not segments:
            log("没有生成有效的消息段")
            cleanup_temp_files(temp_paths, cfg)
            state = "发送失败：没有生成有效的图片消息段"
            print(build_summary_json(results, out_paths, state, fmt, quality, profile) if args.json
                  else build_summary(results, out_paths, state, fmt, quality, profile))
            return EXIT_SEND

        rc = send_via_ws(cfg, segments)
        if rc == EXIT_OK:
            send_state = "已发送到 QQ %s（NapCat OneBot11，%d 张）" % (cfg["user_id"], len(segments))
        else:
            send_state = "发送失败（退出码 %d），详细原因见上面日志" % rc

    # 5) 清理临时文件（--keep-temp 时保留），然后打印结果摘要到 stdout
    cleanup_temp_files(temp_paths, cfg)
    if args.keep_temp:
        send_state += "；临时文件已保留（--keep-temp）"

    if args.json:
        print(build_summary_json(results, out_paths, send_state, fmt, quality, profile))
    else:
        print(build_summary(results, out_paths, send_state, fmt, quality, profile))
    return rc


if __name__ == "__main__":
    sys.exit(main())
