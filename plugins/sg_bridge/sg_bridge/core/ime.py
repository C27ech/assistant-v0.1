"""sg_bridge.core.ime — 输入法（IME）检测与"切到英文"

为什么需要：如果前台窗口挂着中文输入法（IME 处于组字状态），注入的按键可能被 IME 吃掉：
ENTER 变成"确认候选"、字母键进入组字框 —— 游戏就会表现得很古怪。
所以每次操作游戏前，应确保：**键盘布局＝英文（en-US）且 IME 处于半角/英文（alphanumeric）状态**。

实现要点：
* ``GetKeyboardLayout`` 查窗口线程当前布局（LOWORD = LANGID，如 0x0804=zh-CN，0x0409=en-US）
* ``ImmGetContext/ImmGetConversionStatus/ImmGetOpenStatus`` 查 IME 是否打开、是否组字模式
* 切英文：优先 ``WM_INPUTLANGCHANGEREQUEST`` 请求切到 en-US 布局；
  否则退化为把当前 IME 设为"半角英文"（``ImmSetConversionStatus`` + 关闭 IME）
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
imm32 = ctypes.WinDLL("imm32", use_last_error=True)

WM_INPUTLANGCHANGEREQUEST = 0x0050
KLF_ACTIVATE = 0x0001

IME_CMODE_ALPHANUMERIC = 0x0000
IME_CMODE_NATIVE = 0x0001
IME_CMODE_KATAKANA = 0x0002
IME_CMODE_FULLSHAPE = 0x0008

LANG_NAMES = {
    0x0409: "en-US", 0x0809: "en-GB", 0x0404: "zh-TW", 0x0804: "zh-CN",
    0x0C04: "zh-HK", 0x0411: "ja-JP", 0x0412: "ko-KR", 0x0407: "de-DE",
    0x040C: "fr-FR", 0x0419: "ru-RU",
}

user32.GetKeyboardLayout.argtypes = (wintypes.DWORD,)
user32.GetKeyboardLayout.restype = wintypes.HKL
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.LoadKeyboardLayoutW.argtypes = (wintypes.LPCWSTR, wintypes.UINT)
user32.LoadKeyboardLayoutW.restype = wintypes.HKL
user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
user32.SendMessageW.restype = wintypes.LPARAM
user32.GetForegroundWindow.restype = wintypes.HWND

imm32.ImmGetContext.argtypes = (wintypes.HWND,)
imm32.ImmGetContext.restype = wintypes.HANDLE
imm32.ImmReleaseContext.argtypes = (wintypes.HWND, wintypes.HANDLE)
imm32.ImmGetConversionStatus.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                         ctypes.POINTER(wintypes.DWORD))
imm32.ImmGetConversionStatus.restype = wintypes.BOOL
imm32.ImmSetConversionStatus.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD)
imm32.ImmSetConversionStatus.restype = wintypes.BOOL
imm32.ImmGetOpenStatus.argtypes = (wintypes.HANDLE,)
imm32.ImmGetOpenStatus.restype = wintypes.BOOL
imm32.ImmSetOpenStatus.argtypes = (wintypes.HANDLE, wintypes.BOOL)
imm32.ImmSetOpenStatus.restype = wintypes.BOOL

EN_US_KLID = "00000409"          # 美式键盘


def lang_of_hkl(hkl: int) -> int:
    return int(hkl) & 0xFFFF


def lang_name(langid: int) -> str:
    return LANG_NAMES.get(langid, f"0x{langid:04X}")


def window_thread_layout(hwnd: int) -> dict:
    """该窗口所属线程当前的键盘布局。"""
    tid = user32.GetWindowThreadProcessId(int(hwnd), None)
    hkl = user32.GetKeyboardLayout(tid)
    lang = lang_of_hkl(hkl)
    return {"hwnd": int(hwnd), "thread_id": int(tid), "hkl": int(hkl),
            "langid": lang, "lang": lang_name(lang)}


def ime_state(hwnd: int) -> dict:
    """该窗口的 IME 状态：是否打开、是否处于组字（native）模式。"""
    himc = imm32.ImmGetContext(int(hwnd))
    if not himc:
        return {"ime_available": False}
    try:
        conv = wintypes.DWORD(0)
        sent = wintypes.DWORD(0)
        ok = imm32.ImmGetConversionStatus(himc, ctypes.byref(conv), ctypes.byref(sent))
        opened = imm32.ImmGetOpenStatus(himc)
        return {
            "ime_available": bool(ok),
            "ime_open": bool(opened),
            "conversion": int(conv.value),
            "native_mode": bool(conv.value & IME_CMODE_NATIVE),
            "fullshape": bool(conv.value & IME_CMODE_FULLSHAPE),
            "alphanumeric_mode": not bool(conv.value & IME_CMODE_NATIVE),
        }
    finally:
        imm32.ImmReleaseContext(int(hwnd), himc)


def status(hwnd: "int | None" = None) -> dict:
    """一次拿到"布局 + IME"的状态；``hwnd=None`` 用前台窗口。"""
    if hwnd is None:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return {"error": "没有前台窗口"}
    info = window_thread_layout(hwnd)
    info.update(ime_state(hwnd))
    info["need_english"] = not (
        info.get("langid") == 0x0409 or (
            info.get("ime_available") and info.get("alphanumeric_mode") and not info.get("ime_open")
        )
    )
    return info


def ensure_english(hwnd: int, verbose: bool = False) -> dict:
    """尽量把目标窗口切到"英文输入"状态，并返回前后对比。

    ⚠️ 重要：如果**已经是英文**就什么都不做 —— 反复向 DirectInput 游戏发送
    `WM_INPUTLANGCHANGEREQUEST` / `ImmSet*` 可能导致其输入设备异常（实测踩过）。
    """
    before = status(hwnd)
    if not before.get("need_english", True):
        if verbose:
            print("已是英文输入，跳过切换")
        return {"before": before, "actions": ["already-English (no-op)"],
                "after": before, "ok": True}

    actions: list[str] = []

    # 1) 请求把该窗口线程切到 en-US 布局
    hkl_en = user32.LoadKeyboardLayoutW(EN_US_KLID, KLF_ACTIVATE)
    if hkl_en:
        user32.SendMessageW(int(hwnd), WM_INPUTLANGCHANGEREQUEST, 0, hkl_en)
        actions.append(f"WM_INPUTLANGCHANGEREQUEST→en-US(hkl=0x{int(hkl_en):X})")

    # 2) 把当前 IME 设为半角英文并关闭（双保险）
    himc = imm32.ImmGetContext(int(hwnd))
    if himc:
        try:
            ok_conv = imm32.ImmSetConversionStatus(himc, IME_CMODE_ALPHANUMERIC, 0)
            ok_open = imm32.ImmSetOpenStatus(himc, False)
            actions.append(f"ImmSetConversionStatus(alphanumeric)={bool(ok_conv)}, "
                           f"ImmSetOpenStatus(False)={bool(ok_open)}")
        finally:
            imm32.ImmReleaseContext(int(hwnd), himc)
    else:
        actions.append("ImmGetContext 失败（该窗口可能没挂 IME，也可能是游戏自绘窗口）")

    after = status(hwnd)
    if verbose:
        print("IME before:", before)
        print("actions  :", actions)
        print("IME after :", after)
    return {"before": before, "actions": actions, "after": after,
            "ok": not after.get("need_english", True)}


if __name__ == "__main__":
    import sys

    from . import games as G
    from . import wininput as W

    target = sys.argv[1] if len(sys.argv) > 1 else "SG"
    try:
        win = G.resolve_window(target)
    except Exception:
        win = W.foreground_window()
    print("窗口:", win["title"] if win else None)
    res = ensure_english(win["hwnd"], verbose=True)
    print("\n结论:", "已是英文 ✓" if res["ok"] else "未能切到英文，请手动按 Win+Space / Shift 切一下")
