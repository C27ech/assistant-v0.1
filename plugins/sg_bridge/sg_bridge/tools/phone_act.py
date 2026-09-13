"""sg_bridge.tools.phone_act — 在"必须操作手机"的节点上自动完成手机操作（含回复）

S;G 有些剧情节点会**卡住等你去拿手机**（画面静止、按 ENTER 无效）。
本工具就是处理这种节点：

    1. 按 Z 打开手机 → OCR 判断是否出现手机界面（收件箱/发件箱）
    2. ENTER 进收件箱 → OCR 读邮件列表
    3. ENTER 打开最上面那封 → OCR 读正文与"操作项"（如「打开附件」「回复」）
    4. 若发现「回复」：
         ↓ 移到该项 → ENTER 进入回复选项 → OCR 读选项 → 选第一个 → ENTER 发送
         → OCR 验证是否出现「短信已发送」
    5. X（两次）收起手机，交还给剧情推进

用法::

    python -m sg_bridge.tools.phone_act --game SG
    python -m sg_bridge.tools.phone_act --game SG --reply-index 0
"""
from __future__ import annotations

import difflib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "out", "phone"))

from .. import api                                   # noqa: E402
from ..core import ocr as OCR                         # noqa: E402

PANEL_CROP = "0.55,0.02,0.45,0.96"


def _read(game: str, tag: str, crop: str = PANEL_CROP, scale: float = 2.4) -> list[dict]:
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"act_{tag}.png")
    api.call("focus", {"target": game})
    api.call("screenshot", {"path": path, "target": game, "client_only": True})
    return OCR.read_text(path, crop=crop, scale=scale)["lines"]


def _texts(lines: list[dict]) -> list[str]:
    return [ln["text"] for ln in lines]


def _find(lines: list[dict], needle: str, thr: float = 0.55) -> "dict | None":
    """在 OCR 行里模糊找文字，返回该行（含坐标）。"""
    best, best_score = None, 0.0
    for ln in lines:
        s = difflib.SequenceMatcher(None, needle, ln["text"]).ratio()
        if needle in ln["text"]:
            s = 1.0
        if s > best_score:
            best, best_score = ln, s
    return best if best and best_score >= thr else None


def handle_phone_node(game: str, reply_index: int = 0, verbose: bool = True) -> dict:
    """在手机节点上完成"打开→读信→回复→发送→收起"。返回每步的 OCR 证据。"""
    log: list[str] = []

    def emit(t: str) -> None:
        log.append(t)
        if verbose:
            print(t)

    api.call("attach", {"target": game, "focus": True})
    time.sleep(0.4)

    # 1) 打开手机：**必须开手机的节点按 Z（手机触发器）**；
    #    若当前只是剧情里"拿出手机"的一步，则按 ENTER（对话推进）即可 —— 两者都试一次，无害。
    emit(">> 打开手机（先 Z；若无效再按 ENTER 推进剧情）")
    api.call("game_action", {"action": "phone", "game": game})       # Z
    time.sleep(1.3)
    lines = _read(game, "01a_z")
    texts = _texts(lines)
    if not any(("收件箱" in t or "发件箱" in t or "打开信箱" in t) for t in texts):
        emit("   Z 后没看到手机界面 → 按 ENTER 推进（剧情里拿手机的那一步）")
        api.call("game_action", {"action": "confirm", "game": game})
        time.sleep(1.4)
        lines = _read(game, "01b_enter")
        texts = _texts(lines)
        if not any(("收件箱" in t or "发件箱" in t or "打开信箱" in t) for t in texts):
            emit("   再试一次 Z")
            api.call("game_action", {"action": "phone", "game": game})
            time.sleep(1.3)
            lines = _read(game, "01c_z2")
            texts = _texts(lines)
    emit("   手机界面 OCR: " + " / ".join(texts[:8]))

    in_phone = any(("收件箱" in t) or ("发件箱" in t) or ("打开信箱" in t) for t in texts)
    if not in_phone:
        emit("   ⚠️ 没读到手机界面文字 —— 可能手机没打开，或当前界面不同")
    result: dict = {"opened": in_phone, "steps": []}

    # 若还停在"打开信箱"提示上，再按一次 ENTER 进首页
    if in_phone and not any(("收件箱" in t and "打开" not in t) for t in texts):
        emit(">> 再按 ENTER 进入手机首页")
        api.call("game_action", {"action": "confirm", "game": game})
        time.sleep(1.4)
        texts = _texts(_read(game, "01c_home"))

    # 2) 进收件箱
    emit(">> ENTER 进入收件箱")
    api.call("game_action", {"action": "confirm", "game": game})
    time.sleep(1.4)
    lines = _read(game, "02_inbox")
    texts = _texts(lines)
    mails = [t for t in texts if any(ch.isdigit() for ch in t)][:12]
    emit("   收件箱 OCR: " + " / ".join(texts[:12]))
    result["steps"].append({"step": "inbox", "lines": texts})

    # 3) 打开最上面那封
    emit(">> ENTER 打开邮件")
    api.call("game_action", {"action": "confirm", "game": game})
    time.sleep(1.4)
    lines = _read(game, "03_mail")
    texts = _texts(lines)
    emit("   邮件 OCR: " + " / ".join(texts[:12]))
    result["steps"].append({"step": "mail", "lines": texts})

    # 4) 找"回复"操作项
    reply_line = _find(lines, "回复")
    attach_line = _find(lines, "打开附件")
    emit(f"   操作项检测: 回复={bool(reply_line)} 打开附件={bool(attach_line)}")

    if reply_line:
        # 操作项按列表排列：用 y 坐标推断需要按几次 ↓
        items = [ln for ln in lines if ln["bbox"][1] > reply_line["bbox"][1] - 5
                 and ln["bbox"][1] < reply_line["bbox"][1] + 400]
        down = max(0, len(items) - 1)
        emit(f">> 选中「回复」（按 ↓ × {down}）")
        for _ in range(down):
            api.call("key_press", {"key": "down", "ms": 40})
            time.sleep(0.4)
        api.call("game_action", {"action": "confirm", "game": game})
        time.sleep(1.5)
        lines = _read(game, "04_reply_options")
        texts = _texts(lines)
        emit("   回复选项 OCR: " + " / ".join(texts[:12]))
        result["steps"].append({"step": "reply_options", "lines": texts})

        # 选一个回复文本（默认第一个）
        for _ in range(max(0, reply_index)):
            api.call("key_press", {"key": "down", "ms": 40})
            time.sleep(0.4)
        emit(f">> 确认发送（第 {reply_index} 个选项）")
        api.call("game_action", {"action": "confirm", "game": game})
        time.sleep(1.8)
        lines = _read(game, "05_after_send")
        texts = _texts(lines)
        sent = any(("短信已发送" in t or "已发送" in t or "发送" in t) for t in texts)
        emit("   发送后 OCR: " + " / ".join(texts[:12]))
        emit(f"   ⇒ {'检测到「已发送」相关文字 ✓' if sent else '未检测到发送提示'}")
        result["steps"].append({"step": "after_send", "lines": texts})
        result["sent_hint"] = sent
    else:
        emit("   该邮件没有「回复」项（可能只是带附件的邮件）")
        result["sent_hint"] = False

    # 5) 收起手机（X 两次：一次退层，一次收起）
    emit(">> X 收起手机")
    for _ in range(2):
        api.call("game_action", {"action": "phone_close", "game": game})
        time.sleep(0.7)
    lines = _read(game, "06_closed")
    texts = _texts(lines)
    result["closed"] = not any(("收件箱" in t) or ("发件箱" in t) for t in texts)
    emit("   关闭后 OCR: " + " / ".join(texts[:6]))

    path = os.path.join(OUT_DIR, "phone_act_log.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(log))
    result["log"] = log
    result["log_path"] = path
    return result


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    game, reply_index = "SG", 0
    i = 0
    while i < len(argv):
        if argv[i] == "--game":
            game = argv[i + 1]; i += 2
        elif argv[i] == "--reply-index":
            reply_index = int(argv[i + 1]); i += 2
        else:
            i += 1
    print(f"== 处理手机节点：{game}（回复选项序号 {reply_index}）")
    handle_phone_node(game, reply_index)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
