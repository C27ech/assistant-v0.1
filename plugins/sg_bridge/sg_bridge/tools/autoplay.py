"""sg_bridge.tools.autoplay — 自动推进剧情，并用 OCR 监控内容（找"需要回复邮件"的点）

原理：
  * 按住 **CTRL（强行快进）** 让游戏自己往前跑；
  * 每隔几秒**截屏 + OCR**，把对话/面板文字写进日志；
  * 命中关键字（回复/短信/手机/收件箱/来电…）→ 停止并报告；
  * 连续两次"画面几乎不动" → 说明游戏停在某处等输入（选项/菜单/邮件）→ 停止并报告。

用法::

    python -m sg_bridge.tools.autoplay --game SG --minutes 5
    python -m sg_bridge.tools.autoplay --game SG --minutes 2 --watch 回复,短信,收件箱
    python -m sg_bridge.tools.autoplay --game SG --mode enter --minutes 3     # 改为逐句按 ENTER
"""
from __future__ import annotations

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "out", "autoplay"))

from .. import api                                   # noqa: E402
from ..core import ocr as OCR                         # noqa: E402
from .shot_diff import compare                        # noqa: E402

DEFAULT_WATCH = ["回复", "收到新短信", "您有新短信", "您有新的短信", "来电中",
                 "短信已发送", "RINE"]


def run(game: str, minutes: float = 5.0, mode: str = "skip",
        watch: "list[str] | None" = None, poll: float = 3.0,
        out: "str | None" = None, drift_limit: float = 0.003,
        presses: int = 3) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    watch = watch or DEFAULT_WATCH
    if out is None:
        out = os.path.join(OUT_DIR, "run.txt")
    log: list[str] = []

    def emit(text: str) -> None:
        print(text)
        log.append(text)

    api.call("attach", {"target": game, "focus": True})
    deadline = time.time() + minutes * 60
    # 注意：必须用**两个**文件轮流存放截图，否则 compare 会拿同一个文件和自己比（恒为 0%）
    shots = [os.path.join(OUT_DIR, "frame_a.png"), os.path.join(OUT_DIR, "frame_b.png")]
    idx = 0
    prev: "str | None" = None
    still_count = 0
    reason = "超时"

    emit(f">> 模式={mode}：{'脉冲式按住 CTRL（8s 快进 → 松开 → OCR 检查）' if mode == 'skip' else '逐句 ENTER'}")

    try:
        step = 0
        while time.time() < deadline:
            step += 1
            if mode == "skip":
                # 脉冲快进：按住 → 松开（松开后焦点稳定，OCR/截图才可靠）
                api.call("focus", {"target": game})
                api.call("key_down", {"key": "ctrl"})
                time.sleep(8.0)
                api.call("key_up", {"key": "ctrl"})
                time.sleep(0.8)
            elif mode == "watch":
                time.sleep(max(1.0, poll))          # 只观察，不做任何输入
            shot_path = shots[idx]
            idx ^= 1
            api.call("focus", {"target": game})          # 截图前确保游戏在最前，避免抓到别的窗口
            api.call("screenshot", {"path": shot_path, "target": game, "client_only": True})
            drift = compare(prev, shot_path)["changed_ratio"] if prev else 1.0
            prev = shot_path
            ocr = OCR.read_text(shot_path, scale=1.5)
            # 只保留"游戏里的文字"：位于对话区(y>600)或手机面板(x>1100)，且不太长；
            # 这样可排除截图抓到别的窗口（终端/编辑器）造成的假关键字命中。
            interesting = []
            for ln in ocr["lines"]:
                x, y, w, h = ln["bbox"]
                text = ln["text"]
                if len(text) < 2 or len(text) > 26:
                    continue
                if any(t in text for t in ("MAGES", "Nitroplus", "FPS", "Press", "print", "import")):
                    continue
                if y > 600 or x > 1100:
                    interesting.append(text)
            moved = drift > drift_limit
            emit(f"[{time.strftime('%H:%M:%S')}] 漂移={drift * 100:5.2f}% "
                 f"{'变动' if moved else '静止'} | " + (" / ".join(interesting[-3:]) or "（无文字）"))

            hit = [k for k in watch if any(k in ln for ln in interesting)]
            if hit:
                reason = f"命中关键字 {hit}"
                break
            if not moved:
                still_count += 1
                # 过场/黑屏瞬间也会静止，所以要求"连续多次静止"才判定为等待输入
                if still_count == 4:
                    emit(">> 画面持续静止 → 猜测是「必须操作手机」的节点，尝试自动处理手机…")
                    try:
                        from .phone_act import handle_phone_node
                        res = handle_phone_node(game, 0, verbose=False)
                        emit(f"   phone_act: 打开={res.get('opened')} "
                             f"发送提示={res.get('sent_hint')} 关闭={res.get('closed')}")
                        for line in res.get("log", [])[:24]:
                            emit("   | " + line)
                        still_count = 0
                        prev = None           # 手机操作后画面已变，重置比较基准
                        continue
                    except Exception as exc:  # noqa: BLE001
                        emit(f"   手机处理失败: {type(exc).__name__}: {exc}")
                if still_count >= 5:
                    reason = "画面连续静止（游戏在等待输入：选项/菜单/邮件？）"
                    break
            else:
                still_count = 0

            if mode != "skip":
                for _ in range(max(1, presses)):
                    api.call("game_action", {"action": "advance", "game": game})
                    time.sleep(0.45)
                time.sleep(poll)
    finally:
        if mode == "skip":
            api.call("key_up", {"key": "ctrl"})
            emit(">> 已松开 CTRL")

    emit(f"\n== 结束原因：{reason}  用时 {round((minutes * 60 - max(0, deadline - time.time())) / 60, 2)} 分钟")
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write("\n".join(log))
        print(f"日志: {out}")
    return {"reason": reason, "log": log}


def main(argv: "list[str] | None" = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    game, minutes, mode, watch, poll = "SG", 5.0, "skip", None, 3.0
    out, presses = None, 3
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--game":
            game = argv[i + 1]; i += 2
        elif a == "--minutes":
            minutes = float(argv[i + 1]); i += 2
        elif a == "--mode":
            mode = argv[i + 1]; i += 2
        elif a == "--watch":
            watch = [k.strip() for k in argv[i + 1].split(",") if k.strip()]; i += 2
        elif a == "--poll":
            poll = float(argv[i + 1]); i += 2
        elif a == "--presses":
            presses = int(argv[i + 1]); i += 2
        elif a == "--out":
            out = argv[i + 1]; i += 2
        else:
            i += 1
    print(f"== 自动推进：{game} 最多 {minutes} 分钟，模式={mode}，每次 {presses} 下，监控={watch or DEFAULT_WATCH}")
    run(game, minutes, mode, watch, poll, out, presses=presses)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
