"""入口：启动助手应用。

默认通过 QQ(OneBot) 运行；`--console` 进入控制台测试模式（不接渠道）。
"""
from __future__ import annotations

import argparse
import atexit
import ctypes
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from channel.base import IncomingMessage
from channel.qq_onebot import QQOneBotChannel
from config.settings import BASE_DIR, Settings
from supervisor.runtime import Runtime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("assistant")


# ---------------------------------------------------------------------------
# 单实例保护（命名 Mutex）+ PID 名册 + 心跳
#
# 病根：看门狗曾用「命令行正则猜身份」，把插件子进程 `python main.py ...`
# （cwd=controller_v2，见 plugins.json）误认成第二个主进程，执行 Stop-Process -Force
# 强杀，子进程退出码 0xFFFFFFFF。治本：进程身份不靠文本推断，只能靠「登记」：
#   1) 命名 Mutex（内核对象）：同一时刻只允许一个主实例；进程即使被 TerminateProcess
#      强杀，内核也会自动回收该对象，天然免疫「僵尸锁」，所以不用 msvcrt 文件锁。
#   2) PID 名册 runtime/assistant.pid.json：看门狗按 PID + exe + 启动时间 + 命令行
#      + 心跳做多重校验判活。
# 约束：只用标准库 ctypes（本机 C:\Python3xx 未装 pywin32），禁用 win32event。
# ---------------------------------------------------------------------------

ERROR_ACCESS_DENIED = 5
ERROR_ALREADY_EXISTS = 183      # GetLastError()：命名内核对象已存在

MUTEX_NAME = "Assistant.Main.qq.channel.v1"    # 先试 Global\，被拒再回退 Local\

# 模块级全局：持有 mutex 句柄直到进程结束，防止句柄被 GC 回收导致锁提前释放。
_MUTEX_HANDLE = None
_MUTEX_FULL_NAME = None

# 名册/心跳路径用 config/settings.py 的 BASE_DIR 锚定，绝不依赖 cwd
# （主进程与插件子进程的 cwd 各不相同，靠 cwd 定位一定出错）。
RUNTIME_DIR = BASE_DIR / "runtime"
PID_RECORD_PATH = RUNTIME_DIR / "assistant.pid.json"
HEARTBEAT_PATH = RUNTIME_DIR / "assistant.heartbeat"
HEARTBEAT_INTERVAL_SEC = 30

_RECORD_WRITTEN_PID = None          # 本进程写进名册的 PID；None = 没写过（退出时不清理）
_HEARTBEAT_STOP = threading.Event()
_HEARTBEAT_THREAD = None


def _kernel32():
    """取 kernel32 并显式声明签名：CreateMutexW 返回的 HANDLE 是指针宽度，
    不声明 restype 会按 c_int 截断，64 位下可能拿到非法句柄。"""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    k32.CreateMutexW.restype = ctypes.c_void_p
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    k32.CloseHandle.restype = ctypes.c_int
    return k32


def acquire_single_instance(name: str = MUTEX_NAME) -> bool:
    """创建命名 Mutex。

    返回 True = 本进程是唯一实例；False = 已有实例在跑（调用方应立即 sys.exit(0)）。
    先试 Global\\ 命名空间，被拒（ERROR_ACCESS_DENIED / WinError 5）则回退 Local\\。
    """
    global _MUTEX_HANDLE, _MUTEX_FULL_NAME
    if os.name != "nt":                  # 非 Windows 不做单例约束
        return True

    k32 = _kernel32()
    for prefix in ("Global", "Local"):
        full_name = rf"{prefix}\{name}"
        handle = k32.CreateMutexW(None, False, full_name)
        if handle:
            if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
                # 本进程这一侧新拿到的 duplicate 句柄必须关掉，否则泄露内核句柄。
                k32.CloseHandle(handle)
                LOG.warning("已有主实例在运行，本进程退出（Mutex=%s）", full_name)
                return False
            _MUTEX_HANDLE = handle      # 模块级持有到进程结束，防 GC 回收
            _MUTEX_FULL_NAME = full_name
            LOG.info("已获得单实例 Mutex：%s", full_name)
            return True

        err = ctypes.get_last_error()
        if prefix == "Global" and err == ERROR_ACCESS_DENIED:
            # Global 命名空间不可用（跨会话/权限不足）→ 回退 Local 再试一次。
            LOG.warning("Global 命名空间不可用（WinError %d），回退 Local 命名空间", err)
            continue
        # 闸门本身建不起来时放行：绝不因单例机制故障而拒绝启动主进程。
        LOG.warning("CreateMutexW 创建失败（WinError %d），本次不启用单例保护", err)
        return True

    LOG.warning("Global/Local 命名空间都不可用，本次不启用单例保护")
    return True


def _atomic_write_text(path: Path, text: str) -> None:
    """原子写：先写同目录 .tmp 再 os.replace，避免看门狗读到半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _exe_path() -> str:
    """当前解释器绝对路径：看门狗拿它和 Win32_Process.ExecutablePath 逐字比对。"""
    exe = sys.executable or ""
    return str(Path(exe).resolve()) if exe else ""


def write_pid_record(channel: str) -> Path:
    """写 PID 名册（原子）。字段名与看门狗 assistant_watchdog.ps1 的读取严格一致。"""
    global _RECORD_WRITTEN_PID
    record = {
        "version": 1,
        "pid": os.getpid(),
        "started_at": datetime.now(timezone.utc).astimezone().isoformat(),  # 带时区 ISO8601
        "exe": _exe_path(),
        "main_py": str(Path(__file__).resolve()),
        "cwd": os.getcwd(),
        "channel": channel,
        "heartbeat_epoch": int(time.time()),
    }
    _atomic_write_text(PID_RECORD_PATH, json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    _RECORD_WRITTEN_PID = record["pid"]
    LOG.info(
        "PID 名册已登记：%s（pid=%d, channel=%s）",
        PID_RECORD_PATH, record["pid"], channel,
    )
    return PID_RECORD_PATH


def touch_heartbeat() -> None:
    """覆盖写心跳文件（内容=epoch 秒）。看门狗用它的 mtime 判定「卡死」。"""
    _atomic_write_text(HEARTBEAT_PATH, f"{int(time.time())}\n")


def _heartbeat_loop() -> None:
    while not _HEARTBEAT_STOP.wait(HEARTBEAT_INTERVAL_SEC):
        try:
            touch_heartbeat()
        except OSError:
            LOG.warning("写心跳文件失败：%s", HEARTBEAT_PATH, exc_info=True)


def start_heartbeat() -> None:
    """每 30s 打一次心跳（daemon 线程，不阻塞主进程退出）。"""
    global _HEARTBEAT_THREAD
    try:
        touch_heartbeat()
    except OSError:
        LOG.warning("写心跳文件失败：%s", HEARTBEAT_PATH, exc_info=True)
    _HEARTBEAT_THREAD = threading.Thread(
        target=_heartbeat_loop, name="assistant-heartbeat", daemon=True,
    )
    _HEARTBEAT_THREAD.start()


def _unlink_quiet(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        LOG.warning("删除运行时文件失败：%s", path, exc_info=True)


def cleanup_runtime_files() -> None:
    """退出时清理名册/心跳（atexit 与 try-finally 双保险，可重复调用）。"""
    _HEARTBEAT_STOP.set()
    if _RECORD_WRITTEN_PID is None:
        return
    # 名册可能已被继任者（看门狗重启出的新主进程）覆盖：只有里头的 pid 还是自己才删。
    try:
        current = json.loads(PID_RECORD_PATH.read_text(encoding="utf-8")).get("pid")
    except (OSError, ValueError):
        current = None
    if current is not None and current != _RECORD_WRITTEN_PID:
        LOG.info("名册已属于 pid=%s，本进程不清理", current)
        return
    _unlink_quiet(PID_RECORD_PATH)
    _unlink_quiet(HEARTBEAT_PATH)
    LOG.info("已清理运行时名册/心跳文件")


def run_console(runtime: Runtime) -> None:
    print("=== 控制台测试模式 ===")
    print("输入消息模拟用户（空行退出）。")
    while True:
        try:
            content = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not content:
            break
        reply = runtime.handle("console-user", "console", content)
        print(f"助手: {reply}\n")


def run_qq(runtime: Runtime, settings: Settings) -> None:
    channel = QQOneBotChannel(settings)
    runtime.channel = channel

    def on_message(msg: IncomingMessage) -> None:
        # QQ 私聊：用 QQ 号作为用户标识
        reply = runtime.handle(msg.sender_id, msg.sender_id, msg.content, images=msg.images)
        if not reply:
            return  # 空回复（未授权白名单 / 无需回复）→ 不发消息，避免发出空白消息
        if not channel.send_text(msg.sender_id, reply):
            LOG.error(f"回发失败给 {msg.sender_id}")

    runtime.run_monitor_loop()
    LOG.info(f"QQ 渠道启动，连接 OneBot WebSocket: {settings.qq_onebot_url} ...")
    channel.run(on_message)


def main() -> None:
    parser = argparse.ArgumentParser(description="AI 助手")
    parser.add_argument("--console", action="store_true", help="控制台测试模式（不接渠道）")
    parser.add_argument(
        "--channel", choices=["qq"], default=None,
        help="渠道类型（只剩 QQ；默认取 .env 的 CHANNEL）",
    )
    args = parser.parse_args()

    console_mode = bool(getattr(args, "console", False))

    # 单实例闸门：--console 豁免（方便与线上实例并行调试）。
    if not console_mode and not acquire_single_instance():
        LOG.warning("已有主实例在运行，本进程退出")
        sys.exit(0)      # 退出码必须 0：否则看门狗会当成崩溃，触发重启雪崩

    settings = Settings.load()
    runtime = Runtime(settings)

    channel = args.channel or settings.channel
    if channel != "qq":
        # 个人微信 / 企业微信渠道已移除：老 .env 写的 CHANNEL=wechat/wecom 一律按 QQ 启动
        LOG.warning("CHANNEL=%s 已不再支持（渠道只保留 QQ），本次按 QQ 启动", channel)
        channel = "qq"

    if not console_mode:
        # 抢到 Mutex 后才登记名册 + 起心跳：看门狗只认这份登记，不再猜命令行。
        # 写名册/心跳失败只告警，绝不让主进程崩溃（磁盘满/权限不足时仍要能跑）。
        try:
            write_pid_record(channel)
        except OSError:
            LOG.warning("写 PID 名册失败：%s", PID_RECORD_PATH, exc_info=True)
        atexit.register(cleanup_runtime_files)
        try:
            start_heartbeat()
        except OSError:
            LOG.warning("启动心跳线程失败：%s", HEARTBEAT_PATH, exc_info=True)

    try:
        if console_mode:
            run_console(runtime)
        else:
            run_qq(runtime, settings)
    finally:
        if not console_mode:
            cleanup_runtime_files()     # try-finally 兜底（atexit 之外的退出路径）


if __name__ == "__main__":
    main()
