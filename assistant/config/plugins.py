"""插件注册表：把外部 CLI 插件暴露成助手AI 可调用的工具。

插件定义在项目根目录 plugins.json，结构：
{
  "<工具名>": {
    "description": "给大模型看的工具说明",
    "command": ["python", "main.py", "{task}"],   // {占位符} 用调用参数替换
    "cwd": "C:\\\\path\\\\to\\\\plugin",
    "timeout": 300,
    "params": {
      "task": {"type": "string", "description": "...", "required": true}
    }
  }
}

新增插件只需改 plugins.json，无需改代码。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
PLUGINS_FILE = BASE_DIR / "plugins.json"

# 子进程 stdout/stderr 的落盘目录：logs/plugin_<插件名>_<时间戳>.{out,err}.log
LOG_DIR = BASE_DIR / "logs"


def load_plugins(path: Optional[Path] = None) -> dict:
    """读取 plugins.json，返回 {工具名: 插件定义}；文件不存在或解析失败返回空 dict。"""
    p = Path(path) if path else PLUGINS_FILE
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"[plugins] 读取 {p} 失败：{e}")
        return {}
    return data if isinstance(data, dict) else {}


def build_tools(plugins: dict) -> list[dict]:
    """把插件注册表转成 OpenAI function-calling 工具定义列表。"""
    tools: list[dict] = []
    for name, spec in plugins.items():
        if not isinstance(spec, dict):
            continue
        props: dict[str, Any] = {}
        required: list[str] = []
        for pname, pspec in (spec.get("params") or {}).items():
            if not isinstance(pspec, dict):
                continue
            props[pname] = {
                "type": pspec.get("type", "string"),
                "description": pspec.get("description", ""),
            }
            if pspec.get("required"):
                required.append(pname)
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": spec.get("description", f"调用外部插件 {name}"),
                    "parameters": {"type": "object", "properties": props, "required": required},
                },
            }
        )
    return tools


def safe_log_name(name: str) -> str:
    """把插件名安全化：去掉路径分隔符等非法字符，避免日志写出 logs 目录之外。"""
    safe = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(name or "")).strip(" .")
    return (safe or "plugin")[:80]


def describe_exit_code(returncode: int) -> str:
    """把子进程退出码翻译成人话，重点区分「程序自己 fail」还是「被外力杀掉」。

    - 4294967295（0xFFFFFFFF / -1）：Windows 上被外部强杀（TerminateProcess）的典型返回值；
    - 其他负值：通常是被信号/外部终止；
    - 大于 255 的值：超出正常退出码范围，多为异常码 / 被外部终止；
    - 1 / 2 等正常退出码：按原样说明（是程序自己 fail）。
    """
    rc = int(returncode)
    if rc == 4294967295 or rc == -1:
        return (
            f"执行失败（退出码 {rc} = 0xFFFFFFFF）：被外部强杀（0xFFFFFFFF），"
            "进程未正常退出，无最终输出，可能是外部终止/看门狗误杀"
        )
    if rc < 0:
        return (
            f"执行失败（退出码 {rc}）：疑似被强杀（负值退出码通常表示被信号/外部终止），"
            "进程未正常退出，无最终输出，可能是外部终止/看门狗误杀"
        )
    if rc > 255:
        return (
            f"执行失败（退出码 {rc} = 0x{rc & 0xFFFFFFFF:08X}）：疑似被强杀"
            "（超出正常退出码范围，多为 Windows 异常码/被外部终止），进程未正常退出"
        )
    return f"执行失败（退出码 {rc}）"


def _decode_stream(raw: Any) -> str:
    """把 TimeoutExpired 里可能捞到的 bytes/str 输出统一成 str。"""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return str(raw)


def write_plugin_logs(name: str, stdout: str, stderr: str) -> tuple[str, str]:
    """把子进程 stdout / stderr 分别落盘，返回 (out 日志路径, err 日志路径)。

    路径：logs/plugin_<安全化插件名>_<YYYYMMDD_HHMMSS>.out.log / .err.log
    写盘失败不抛异常（返回空串），以免影响插件调用本身的返回值逻辑。
    """
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        print(f"[plugins] 创建日志目录 {LOG_DIR} 失败：{e}")
        return "", ""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"plugin_{safe_log_name(name)}_{stamp}"
    out_path = LOG_DIR / f"{stem}.out.log"
    err_path = LOG_DIR / f"{stem}.err.log"
    try:
        with open(out_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(stdout or "")
        with open(err_path, "w", encoding="utf-8", errors="replace") as f:
            f.write(stderr or "")
    except Exception as e:  # noqa: BLE001
        print(f"[plugins] 写入插件日志失败：{type(e).__name__}: {e}")
        return "", ""
    return str(out_path), str(err_path)


def _fmt_value(v) -> str:
    """占位符取值格式化：布尔转 JSON 小写（true/false），避免 str(False)='False' 被当成真值。"""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _resolve_cwd(cwd):
    """把插件的 cwd 解析成绝对路径。

    相对路径按**仓库根目录**解析（也就是 assistant/ 的上一级），
    这样 plugins.json 里写 "plugins/web_tools" 就是仓库根下的那个目录，直觉一致。

    兼容回退：仓库根下不存在、但 assistant/ 下存在时，按 assistant/ 解析
    （方便把插件直接放在 assistant/ 里的小项目）。
    """
    if not cwd:
        return None
    p = Path(str(cwd))
    if p.is_absolute():
        return str(p)
    repo_root = BASE_DIR.parent
    cand = repo_root / p
    if cand.is_dir():
        return str(cand)
    inside = BASE_DIR / p
    if inside.is_dir():
        return str(inside)
    return str(cand)      # 都不存在也返回「仓库根下」这个预期路径，报错信息更直观


def run_plugin(name: str, spec: dict, args: dict) -> str:
    """执行一个插件命令，返回结果文本（失败也返回文本，不向上抛异常）。"""
    command = spec.get("command") or []
    if not command:
        return f"[插件 {name}] 未配置 command"

    cmd: list[str] = []
    for part in command:
        text = str(part)
        for k, v in (args or {}).items():
            text = text.replace("{" + k + "}", _fmt_value(v))
        if "{" in text and "}" in text:
            # 还有未提供的「可选参数」占位符 → 跳过这一段（如 target={game} 没传 game）
            continue
        cmd.append(text)

    try:
        timeout = float(spec.get("timeout", 300))
    except (TypeError, ValueError):
        timeout = 300.0

    try:
        proc = subprocess.run(
            cmd,
            cwd=_resolve_cwd(spec.get("cwd")),
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
    except subprocess.TimeoutExpired as e:
        # 超时的子进程同样会把已有输出随内存一起丢掉，能抓到多少就落盘多少。
        out_path, err_path = write_plugin_logs(
            name,
            _decode_stream(getattr(e, "stdout", None)),
            _decode_stream(getattr(e, "stderr", None)),
        )
        tip = f"（子进程输出已落盘：{out_path} / {err_path}）" if out_path else ""
        return f"[插件 {name}] 执行超时（>{int(timeout)}s）{tip}"
    except FileNotFoundError as e:
        return f"[插件 {name}] 命令不存在：{e}"
    except Exception as e:  # noqa: BLE001
        return f"[插件 {name}] 执行异常：{type(e).__name__}: {e}"

    # 先把原始 stdout/stderr（未 strip）落盘，进程被强杀时也留下最后输出。
    out_path, err_path = write_plugin_logs(name, proc.stdout or "", proc.stderr or "")

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = (err or out or "未知错误")[-1500:]
        log_tip = f"\n（子进程输出已落盘：{out_path} / {err_path}）" if out_path else ""
        return f"[插件 {name}] {describe_exit_code(proc.returncode)}：\n{detail}{log_tip}"
    return out if out else f"（插件 {name} 执行成功，无文本输出）"
