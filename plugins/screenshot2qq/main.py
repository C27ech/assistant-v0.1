#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
截图发QQ —— 独立 CLI 插件

功能：
    1. 截取当前所有显示器屏幕（PIL.ImageGrab.grab(all_screens=True)）
    2. 通过 NapCat 的 OneBot11 正向 WebSocket 通道发送到指定 QQ 私聊
    3. 发送完成后清理临时截图文件

依赖：
    - Pillow（已安装）
    - websocket-client（已安装）
"""

import argparse
import base64
import json
import os
import sys
import tempfile
import time

from PIL import ImageGrab
import websocket

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
}

# 外部调用可依据退出码判断结果
EXIT_OK = 0        # 成功
EXIT_CONFIG = 1    # 参数 / 配置错误
EXIT_CAPTURE = 2   # 截图失败（没有任何可用屏幕截图）
EXIT_CONNECT = 3   # WebSocket 连接失败 / 超时
EXIT_SEND = 4      # 发送（或读取截图数据）失败
EXIT_RESPONSE = 5  # NapCat 返回错误


class CaptureError(Exception):
    """截图阶段错误"""


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

    # 校验
    try:
        cfg["user_id"] = _to_int(cfg["user_id"])
        cfg["timeout"] = float(cfg["timeout"])
    except Exception as exc:
        log("配置值类型错误：%s" % exc)
        return None

    if cfg["user_id"] <= 0:
        log("user_id 必须为正整数，当前为 %r —— 请在 config.json 里填你自己的 QQ 号" % cfg["user_id"])
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

    if cfg["temp_dir"] is not None:
        cfg["temp_dir"] = os.path.abspath(str(cfg["temp_dir"]))
        try:
            os.makedirs(cfg["temp_dir"], exist_ok=True)
        except Exception as exc:
            log("无法创建临时目录 %s：%s" % (cfg["temp_dir"], exc))
            return None

    return cfg


def capture_screens():
    """截取所有显示器，返回 PIL.Image 列表。

    - 优先使用 ImageGrab.grab(all_screens=True)；
    - 若 all_screens 不被支持/整体失败，回退到单屏截取，避免直接不可用；
    - 单个屏幕为 None 的项会被过滤。
    """
    try:
        result = ImageGrab.grab(all_screens=True)
    except Exception as exc:
        log("all_screens=True 截取失败（%s），尝试回退单屏截取" % exc)
        try:
            result = ImageGrab.grab()
        except Exception as exc2:
            raise CaptureError("截图失败：%s" % exc2) from exc2

    if isinstance(result, (list, tuple)):
        items = list(result)
    else:
        items = [result]

    items = [img for img in items if img is not None]
    if not items:
        raise CaptureError("未获取到任何屏幕图像")
    return items


def save_screen_to_temp(img, index, cfg):
    """把单个屏幕保存为临时 PNG 文件，返回路径；失败抛异常并清理残留。"""
    image_format = cfg["image_format"]
    suffix = "." + image_format.lower()
    fd, path = tempfile.mkstemp(
        prefix="screenshot2qq_screen%d_" % index,
        suffix=suffix,
        dir=cfg["temp_dir"],
    )
    os.close(fd)
    try:
        img.save(path, format=image_format)
    except Exception:
        try:
            os.remove(path)
        except Exception:
            pass
        raise
    return path


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
        description="截取所有显示器屏幕，并通过 NapCat OneBot11 正向 WebSocket 发送到指定 QQ 私聊。",
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
    return parser


def main(argv=None):
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    cfg = load_config(args)
    if cfg is None:
        return EXIT_CONFIG

    log("配置：ws_url=%s, user_id=%s, token=%s, delete_temp_files=%s, timeout=%s"
        % (cfg["ws_url"], cfg["user_id"],
           ("已设置" if cfg["token"] else "空"), cfg["delete_temp_files"], cfg["timeout"]))

    try:
        screens = capture_screens()
    except CaptureError as exc:
        log(str(exc))
        return EXIT_CAPTURE

    log("共获取到 %d 个屏幕，开始保存临时截图" % len(screens))

    temp_paths = []
    for idx, img in enumerate(screens, start=1):
        try:
            path = save_screen_to_temp(img, idx, cfg)
            temp_paths.append(path)
            log("第 %d 屏截图已保存：%s" % (idx, path))
        except Exception as exc:
            # 某屏保存失败：跳过并继续其他屏
            log("第 %d 屏截图保存失败，已跳过：%s" % (idx, exc))
            continue

    if not temp_paths:
        log("所有屏幕截图均失败，没有可发送的内容")
        return EXIT_CAPTURE

    segments = build_message_segments(temp_paths)
    if not segments:
        log("没有生成有效的消息段")
        cleanup_temp_files(temp_paths, cfg)
        return EXIT_SEND

    rc = send_via_ws(cfg, segments)

    # 无论发送成功/失败，都按配置清理临时文件（避免残留泄漏）
    cleanup_temp_files(temp_paths, cfg)
    return rc


if __name__ == "__main__":
    sys.exit(main())
