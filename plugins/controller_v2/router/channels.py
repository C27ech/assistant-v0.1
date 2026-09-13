"""接口通道注册表（可扩展）。

每个接口通道是一个 :class:`ChannelSpec`，包含：
- name：唯一接口名（供 RouteDecision.interface_name 使用）；
- description：通道说明；
- channel：注册到 ``action.execute_interface`` 的底层通道（win32 / cli / http）；
- handler_name：在 action 层注册表中的处理器名；
- keywords / pattern：match 规则（关键词子串 + 可选正则）；
- probe：可用性探测函数（只做环境/依赖检查，不产生业务副作用）；
- build_params：从任务字符串提取 handler 参数；
- handler：接口处理器引用（接收完整 action dict，返回结果 dict）。

本模块导入时会把所有 handler 注册到 ``action.action`` 的接口注册表，
因此 ``execute_interface`` 后续可以真正执行这些通道。
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Pattern, Tuple

from action.action import register_interface_handler


# ---------------------------------------------------------------------------
# 通用辅助
# ---------------------------------------------------------------------------

def _extract_args(action: Dict[str, Any]) -> Dict[str, Any]:
    """兼容 execute_interface 的扁平 / 嵌套两种 action 形态，取出 args。"""
    if not isinstance(action, dict):
        return {}

    nested = action.get("interface")
    if isinstance(nested, dict):
        args = nested.get("args")
    else:
        args = action.get("args")

    if isinstance(args, dict):
        return args

    # 兜底：允许 caller 直接把 params 放进来。
    params = action.get("params")
    if isinstance(params, dict):
        return params

    return {}


def _clean_token(value: str) -> str:
    """去掉任务解析出来的引号、空白与常见中文标点。"""
    return value.strip().strip("'\"“”‘’ ").rstrip("。，,;；")


# ---------------------------------------------------------------------------
# ChannelSpec
# ---------------------------------------------------------------------------

@dataclass(eq=False)
class ChannelSpec:
    name: str
    description: str
    channel: str
    handler_name: str
    keywords: Tuple[str, ...]
    pattern: Optional[Pattern[str]]
    probe: Callable[[], bool]
    build_params: Callable[[str], Dict[str, Any]]
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]
    registered: bool = field(default=False, repr=False)
    param_requirements: str = field(default="", repr=False)
    destructive: bool = field(default=False, repr=False)

    def matches(self, task: str) -> bool:
        """关键词（大小写不敏感子串）优先，其次正则 search。"""
        if not isinstance(task, str):
            return False
        low = task.lower()
        for keyword in self.keywords:
            if keyword and keyword.lower() in low:
                return True
        if self.pattern is not None:
            return bool(self.pattern.search(task))
        return False

    def available(self) -> bool:
        """handler 引用存在、已注册且探测通过，才认为可用。"""
        if self.handler is None or not self.registered:
            return False
        try:
            return bool(self.probe())
        except Exception:  # noqa: BLE001 - 探测异常视为不可用。
            return False


# ---------------------------------------------------------------------------
# 可用性探测（只做依赖 / 环境检查，不产生业务副作用）
# ---------------------------------------------------------------------------

def _probe_win32() -> bool:
    try:
        import win32con  # noqa: F401
        import win32gui  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


def _probe_taskkill() -> bool:
    return sys.platform == "win32" and shutil.which("taskkill") is not None


def _probe_stdio() -> bool:
    # 文件系统操作只用标准库，默认可用。
    return True


def _probe_webbrowser() -> bool:
    try:
        return callable(getattr(webbrowser, "open", None))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# win32：窗口管理
# ---------------------------------------------------------------------------

def _find_hwnd(title: Optional[str]) -> Optional[int]:
    """按标题找窗口；找不到时返回 None。title 为空则取前台窗口。"""
    import win32gui

    if not title:
        hwnd = win32gui.GetForegroundWindow()
        return int(hwnd) if hwnd else None

    target = str(title)
    exact = win32gui.FindWindow(None, target)
    if exact:
        return int(exact)

    # 部分标题匹配（大小写不敏感）。
    matches: List[int] = []

    def _enum_cb(hwnd: int, _: Any) -> None:
        try:
            if target.lower() in win32gui.GetWindowText(hwnd).lower():
                matches.append(int(hwnd))
        except Exception:  # noqa: BLE001 - 某些窗口读取标题会失败。
            pass

    win32gui.EnumWindows(_enum_cb, None)
    return matches[0] if matches else None


def _handle_window_show(action: Dict[str, Any], mode: str) -> Dict[str, Any]:
    import win32con
    import win32gui

    args = _extract_args(action)
    title = args.get("window_title") or args.get("title")
    hwnd = _find_hwnd(title)
    if not hwnd:
        return {"ok": False, "reason": "未找到目标窗口"}

    flags = {
        "minimize": win32con.SW_MINIMIZE,
        "maximize": win32con.SW_MAXIMIZE,
        "restore": win32con.SW_RESTORE,
    }
    win32gui.ShowWindow(hwnd, flags[mode])
    return {"ok": True, "reason": f"window_{mode} applied", "result": {"hwnd": hwnd}}


def _handle_window_close(action: Dict[str, Any]) -> Dict[str, Any]:
    import win32con
    import win32gui

    args = _extract_args(action)
    title = args.get("window_title") or args.get("title")
    hwnd = _find_hwnd(title)
    if not hwnd:
        return {"ok": False, "reason": "未找到目标窗口"}

    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    return {"ok": True, "reason": "window_close posted", "result": {"hwnd": hwnd}}


def _build_window_params(_task: str) -> Dict[str, Any]:
    # 演示通道：默认操作当前前台窗口；如需指定标题，可扩展解析。
    return {"window_title": None}


def _handle_window_activate(action: Dict[str, Any]) -> Dict[str, Any]:
    """把指定标题窗口切到前台并激活（AttachThreadInput 绕过前台锁定）。"""
    import ctypes
    import time

    import win32con
    import win32gui
    import win32process

    args = _extract_args(action)
    title = args.get("window_title") or args.get("title")
    hwnd = _find_hwnd(title)
    if not hwnd:
        return {"ok": False, "reason": "未找到目标窗口"}

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # 若最小化则先还原，保证可被置顶 / 激活。
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_SHOWWINDOW = 0x0040

    # 临时置顶，确保窗口可见，提高 SetForegroundWindow 成功率。
    win32gui.SetWindowPos(
        hwnd, HWND_TOPMOST, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
    )
    time.sleep(0.15)

    try:
        foreground_hwnd = win32gui.GetForegroundWindow()
        our_tid = int(kernel32.GetCurrentThreadId())
        foreground_tid = (
            int(win32process.GetWindowThreadProcessId(foreground_hwnd)[0])
            if foreground_hwnd
            else 0
        )
        target_tid = int(win32process.GetWindowThreadProcessId(hwnd)[0])

        attached: List[int] = []
        if foreground_tid and foreground_tid != our_tid:
            if user32.AttachThreadInput(our_tid, foreground_tid, True):
                attached.append(foreground_tid)
        if target_tid and target_tid != our_tid:
            if user32.AttachThreadInput(our_tid, target_tid, True):
                attached.append(target_tid)

        try:
            win32gui.SetForegroundWindow(hwnd)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetActiveWindow(hwnd)
            try:
                win32gui.SetFocus(hwnd)
            except Exception:  # noqa: BLE001 - SetFocus 失败不影响置前台结果。
                pass
        finally:
            time.sleep(0.1)
            for tid in attached:
                user32.AttachThreadInput(our_tid, tid, False)
    finally:
        # 取消临时置顶，恢复窗口正常 z 序。
        win32gui.SetWindowPos(
            hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW,
        )

    foreground_now = win32gui.GetForegroundWindow()
    activated = bool(foreground_now and int(foreground_now) == int(hwnd))
    return {
        "ok": activated,
        "reason": "window_activate applied" if activated else "window_activate 未生效：目标窗口未到前台",
        "result": {
            "hwnd": int(hwnd),
            "foreground_hwnd": int(foreground_now) if foreground_now else None,
            "foreground_title": win32gui.GetWindowText(foreground_now) if foreground_now else None,
        },
    }


_WINDOW_ACTIVATE_TITLE_RE = re.compile(
    r"(?:把|将)?\s*['\"]?([^'\"\s，。]+?)['\"]?\s*(?:切到前台|切换到前台|切至前台|激活|前台化)"
)


def _build_window_activate_params(task: str) -> Dict[str, Any]:
    """从任务文本提取窗口标题；仅作 AI 漏参时的兜底。"""
    m = _WINDOW_ACTIVATE_TITLE_RE.search(task)
    return {"window_title": _clean_token(m.group(1)) if m else None}


# ---------------------------------------------------------------------------
# cli：进程管理
# ---------------------------------------------------------------------------

_PROCESS_NAME_RE = re.compile(
    r"(?:结束|关闭|终止|杀掉|强制结束)(?:进程)?\s*['\"]?"
    r"([A-Za-z0-9_.-]+(?:\.exe)?)['\"]?",
    re.IGNORECASE,
)


def _handle_process_kill(action: Dict[str, Any]) -> Dict[str, Any]:
    args = _extract_args(action)
    pid = args.get("pid")
    name = args.get("process_name") or args.get("name")

    if pid is not None:
        cmd = ["taskkill", "/PID", str(pid), "/F"]
    elif name:
        cmd = ["taskkill", "/IM", str(name), "/F"]
    else:
        return {"ok": False, "reason": "缺少 process_name 或 pid 参数"}

    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"taskkill 执行异常: {exc}"}

    output = (completed.stdout or completed.stderr).strip()
    return {
        "ok": completed.returncode == 0,
        "reason": output or ("ok" if completed.returncode == 0 else "taskkill failed"),
        "result": {"returncode": completed.returncode},
    }


def _build_process_kill_params(task: str) -> Dict[str, Any]:
    m = _PROCESS_NAME_RE.search(task)
    name = m.group(1) if m else None
    if name and not name.lower().endswith(".exe"):
        name = f"{name}.exe"
    return {"process_name": name}


# ---------------------------------------------------------------------------
# cli：文件系统操作（演示通道，标准库实现）
# ---------------------------------------------------------------------------

_FILE_REMOVE_RE = re.compile(
    r"(?:删除|移除)(?:文件|目录|文件夹)?\s*['\"]?([^'\"\s，。]+)['\"]?"
)
_DIR_CREATE_RE = re.compile(
    r"(?:新建|创建)(?:文件夹|目录)\s*['\"]?([^'\"\s，。]+)['\"]?"
)
_FILE_COPY_RE = re.compile(
    r"(?:复制|拷贝)\s*['\"]?(.+?)['\"]?\s*(?:到|至)\s*['\"]?(.+?)['\"]?\s*$"
)
_FILE_MOVE_RE = re.compile(
    r"(?:移动|剪切)\s*['\"]?(.+?)['\"]?\s*(?:到|至)\s*['\"]?(.+?)['\"]?\s*$"
)


def _handle_file_remove(action: Dict[str, Any]) -> Dict[str, Any]:
    args = _extract_args(action)
    path = args.get("path")
    if not path:
        return {"ok": False, "reason": "缺少 path 参数"}

    p = Path(str(path)).expanduser()
    try:
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"删除失败: {exc}"}

    return {"ok": True, "reason": "file_removed", "result": {"path": str(p)}}


def _handle_dir_create(action: Dict[str, Any]) -> Dict[str, Any]:
    args = _extract_args(action)
    path = args.get("path")
    if not path:
        return {"ok": False, "reason": "缺少 path 参数"}

    p = Path(str(path)).expanduser()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"创建目录失败: {exc}"}

    return {"ok": True, "reason": "directory_created", "result": {"path": str(p)}}


def _handle_file_copy(action: Dict[str, Any]) -> Dict[str, Any]:
    args = _extract_args(action)
    src = args.get("src")
    dst = args.get("dst")
    if not src or not dst:
        return {"ok": False, "reason": "缺少 src/dst 参数"}

    try:
        result = shutil.copy(Path(str(src)).expanduser(), Path(str(dst)).expanduser())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"复制失败: {exc}"}

    return {"ok": True, "reason": "file_copied", "result": {"path": str(result)}}


def _handle_file_move(action: Dict[str, Any]) -> Dict[str, Any]:
    args = _extract_args(action)
    src = args.get("src")
    dst = args.get("dst")
    if not src or not dst:
        return {"ok": False, "reason": "缺少 src/dst 参数"}

    try:
        result = shutil.move(Path(str(src)).expanduser(), Path(str(dst)).expanduser())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"移动失败: {exc}"}

    return {"ok": True, "reason": "file_moved", "result": {"path": str(result)}}


def _build_file_remove_params(task: str) -> Dict[str, Any]:
    m = _FILE_REMOVE_RE.search(task)
    return {"path": _clean_token(m.group(1)) if m else None}


def _build_dir_create_params(task: str) -> Dict[str, Any]:
    m = _DIR_CREATE_RE.search(task)
    return {"path": _clean_token(m.group(1)) if m else None}


def _build_file_copy_params(task: str) -> Dict[str, Any]:
    m = _FILE_COPY_RE.search(task)
    if not m:
        return {"src": None, "dst": None}
    return {
        "src": _clean_token(m.group(1)),
        "dst": _clean_token(m.group(2)),
    }


def _build_file_move_params(task: str) -> Dict[str, Any]:
    m = _FILE_MOVE_RE.search(task)
    if not m:
        return {"src": None, "dst": None}
    return {
        "src": _clean_token(m.group(1)),
        "dst": _clean_token(m.group(2)),
    }


# ---------------------------------------------------------------------------
# http：打开 URL（演示通道，用系统默认浏览器）
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s，。；;]+", re.IGNORECASE)


def _handle_open_url(action: Dict[str, Any]) -> Dict[str, Any]:
    args = _extract_args(action)
    url = args.get("url")
    if not url:
        return {"ok": False, "reason": "缺少 url 参数"}

    try:
        opened = webbrowser.open(str(url))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"打开 URL 异常: {exc}"}

    if not opened:
        return {"ok": False, "reason": "webbrowser.open 返回 False，无法打开 URL"}
    return {"ok": True, "reason": "url_opened", "result": {"url": str(url)}}


def _build_open_url_params(task: str) -> Dict[str, Any]:
    m = _URL_RE.search(task)
    return {"url": m.group(0) if m else None}


# ---------------------------------------------------------------------------
# 通道注册表
# ---------------------------------------------------------------------------

CHANNELS: Tuple[ChannelSpec, ...] = (
    ChannelSpec(
        name="window_minimize",
        description="Win32 ShowWindow 最小化窗口",
        channel="win32",
        handler_name="window_minimize",
        keywords=("最小化窗口", "最小化"),
        pattern=None,
        probe=_probe_win32,
        build_params=_build_window_params,
        handler=lambda action: _handle_window_show(action, "minimize"),
        param_requirements='{"window_title": "可选，目标窗口标题；留空表示当前前台窗口"}',
    ),
    ChannelSpec(
        name="window_maximize",
        description="Win32 ShowWindow 最大化窗口",
        channel="win32",
        handler_name="window_maximize",
        keywords=("最大化窗口", "最大化"),
        pattern=None,
        probe=_probe_win32,
        build_params=_build_window_params,
        handler=lambda action: _handle_window_show(action, "maximize"),
        param_requirements='{"window_title": "可选，目标窗口标题；留空表示当前前台窗口"}',
    ),
    ChannelSpec(
        name="window_restore",
        description="Win32 ShowWindow 还原窗口",
        channel="win32",
        handler_name="window_restore",
        keywords=("还原窗口", "恢复窗口"),
        pattern=None,
        probe=_probe_win32,
        build_params=_build_window_params,
        handler=lambda action: _handle_window_show(action, "restore"),
        param_requirements='{"window_title": "可选，目标窗口标题；留空表示当前前台窗口"}',
    ),
    ChannelSpec(
        name="window_activate",
        description="Win32 SetForegroundWindow 把窗口切到前台并激活",
        channel="win32",
        handler_name="window_activate",
        keywords=("切到前台", "切换到前台", "激活窗口", "前台化", "激活"),
        pattern=None,
        probe=_probe_win32,
        build_params=_build_window_activate_params,
        handler=_handle_window_activate,
        param_requirements='{"window_title": "目标窗口标题，例如 Steam"}',
    ),
    ChannelSpec(
        name="window_close",
        description="Win32 WM_CLOSE 关闭窗口",
        channel="win32",
        handler_name="window_close",
        keywords=("关闭窗口", "关闭当前窗口", "关窗口"),
        destructive=True,
        pattern=None,
        probe=_probe_win32,
        build_params=_build_window_params,
        handler=_handle_window_close,
    ),
    ChannelSpec(
        name="process_kill",
        description="taskkill 结束进程",
        channel="cli",
        handler_name="process_kill",
        keywords=("结束进程", "关闭进程", "终止进程", "杀掉进程", "强制结束", "杀进程"),
        destructive=True,
        pattern=_PROCESS_NAME_RE,
        probe=_probe_taskkill,
        build_params=_build_process_kill_params,
        handler=_handle_process_kill,
    ),
    ChannelSpec(
        name="file_remove",
        description="删除文件 / 目录",
        channel="cli",
        handler_name="file_remove",
        keywords=("删除文件", "删除目录", "删除文件夹", "移除文件", "删掉文件", "删文件", "删除", "删掉", "移除"),
        destructive=True,
        pattern=_FILE_REMOVE_RE,
        probe=_probe_stdio,
        build_params=_build_file_remove_params,
        handler=_handle_file_remove,
    ),
    ChannelSpec(
        name="file_copy",
        description="复制文件",
        channel="cli",
        handler_name="file_copy",
        keywords=("复制文件", "拷贝文件"),
        pattern=_FILE_COPY_RE,
        probe=_probe_stdio,
        build_params=_build_file_copy_params,
        handler=_handle_file_copy,
        param_requirements='{"src": "源文件路径", "dst": "目标文件路径"}',
    ),
    ChannelSpec(
        name="file_move",
        description="移动文件",
        channel="cli",
        handler_name="file_move",
        keywords=("移动文件", "剪切文件"),
        pattern=_FILE_MOVE_RE,
        probe=_probe_stdio,
        build_params=_build_file_move_params,
        handler=_handle_file_move,
        param_requirements='{"src": "源文件路径", "dst": "目标文件路径"}',
    ),
    ChannelSpec(
        name="dir_create",
        description="创建文件夹 / 目录",
        channel="cli",
        handler_name="dir_create",
        keywords=("新建文件夹", "创建文件夹", "新建目录", "创建目录", "建目录", "建文件夹"),
        destructive=True,
        pattern=_DIR_CREATE_RE,
        probe=_probe_stdio,
        build_params=_build_dir_create_params,
        handler=_handle_dir_create,
    ),
    ChannelSpec(
        name="open_url",
        description="用系统默认浏览器打开 URL",
        channel="http",
        handler_name="open_url",
        keywords=("打开网址", "打开网页", "访问网址", "浏览网页", "打开链接"),
        pattern=_URL_RE,
        probe=_probe_webbrowser,
        build_params=_build_open_url_params,
        handler=_handle_open_url,
        param_requirements='{"url": "要打开的完整 http(s) 链接"}',
    ),
)


def _register_all() -> None:
    """把预置通道注册进 action.action 的接口执行器注册表。"""
    for spec in CHANNELS:
        try:
            register_interface_handler(spec.channel, spec.handler_name, spec.handler)
            spec.registered = True
        except Exception:  # noqa: BLE001 - 注册失败标记为不可用，route 会降级。
            spec.registered = False


_register_all()


# ---------------------------------------------------------------------------
# 对外查询 API
# ---------------------------------------------------------------------------

def get_channel(name: str) -> Optional[ChannelSpec]:
    """按唯一接口名查找通道。"""
    for spec in CHANNELS:
        if spec.name == name:
            return spec
    return None


def get_channels(enabled: Optional[Iterable[str]] = None) -> List[ChannelSpec]:
    """返回可用作路由判定的通道列表。

    :param enabled: 可选白名单（接口名集合）；为 None 表示全部启用。
    """
    if enabled is None:
        return list(CHANNELS)
    enabled_set = {str(item) for item in enabled}
    return [spec for spec in CHANNELS if spec.name in enabled_set]


def build_interface_action(
    interface_name: str,
    params: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """把 RouteDecision 转成 ``action.execute_interface`` 可直接执行的 action dict。"""
    spec = get_channel(interface_name)
    if spec is None:
        return None
    return {
        "type": "interface",
        "channel": spec.channel,
        "name": spec.handler_name,
        "args": dict(params or {}),
    }


__all__ = [
    "ChannelSpec",
    "CHANNELS",
    "get_channel",
    "get_channels",
    "build_interface_action",
]
