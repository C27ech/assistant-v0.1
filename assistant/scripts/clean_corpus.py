"""清洗语料错字（高效版）：让大模型对「可疑字」做一次分类，再批量修正。

思路：
  1. 用你正常对话里出现过的汉字当「白名单」，找出语料里从没出现过的**可疑字**；
  2. 把可疑字 + 出现次数 + 例句，**一次性**交给大模型分类，要求返回 JSON：
       map     能确定正确写法的（繁体/日文汉字/形近错字）  -> 错字: 正字
       garbled 无法修复的乱码字                          -> 这些字所在的行直接删掉
       ok      本来就正确的生僻字                        -> 不动
  3. 应用：改字 -> 删乱码行 -> 顺带删掉含日文假名的行；
  4. 原文件自动备份 .typo-bak。

用法：
  python scripts/clean_corpus.py
"""
from __future__ import annotations

import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import Settings  # noqa: E402
from llm.deepseek import DeepSeekClient  # noqa: E402

STYLE_DIR = BASE_DIR / "style"
FILES = [STYLE_DIR / "corpus.txt", STYLE_DIR / "corpus_distill.txt"]
KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
CJK = re.compile(r"[\u4e00-\u9fff]")


def load_lines(p: Path) -> list[str]:
    return [x for x in p.read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")]


def normal_chars() -> set:
    """正常对话里出现过的汉字（当作白名单）。"""
    import sqlite3

    out = set()
    c = sqlite3.connect(str(BASE_DIR / "assistant.db"))
    for (content,) in c.execute("SELECT content FROM messages"):
        for ch in (content or ""):
            if CJK.match(ch):
                out.add(ch)
    return out


def build_prompt(suspect: dict) -> str:
    rows = [f"{ch}\t{n}\t{ex[:40]}" for ch, (n, ex) in suspect.items()]
    return (
        "下面这些汉字是某游戏文本提取时「可能被字形识别错」的字（每行：字 TAB 次数 TAB 例句）。\n"
        "请把它们分成三类，**只输出一个 JSON 对象**：\n"
        "{\n"
        '  "map": {"错字": "正确的字"},   // 能确定正确写法的：繁体字（論→论）、日文汉字（対→对、気→气、鉄→铁、酔→醉）、形近错字（囷→围）\n'
        '  "garbled": ["字", ...],        // 看起来不像正常汉字、也无法确定正确写法的乱码字\n'
        '  "ok": ["字", ...]              // 本来就正确的生僻字（人名、科技术语等）\n'
        "}\n"
        "规则：**拿不准的一律放进 ok，不要瞎猜**。只输出 JSON，不要任何解释。\n\n"
        "字\t次数\t例句\n" + "\n".join(rows)
    )


def chat_text(client: DeepSeekClient, prompt: str, max_tokens: int = 16000, attempts: int = 3) -> str:
    for i in range(attempts):
        r = client.chat([{"role": "user", "content": prompt}], max_tokens=max_tokens * (i + 1))
        text = (r.content or "").strip()
        if text:
            return text
        print(f"[警告] 空内容（finish={r.finish_reason}, 思考 {len(r.reasoning_content)} 字），第 {i + 1} 次重试...", flush=True)
        time.sleep(1)
    return ""


def parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def main() -> None:
    s = Settings.load()
    client = DeepSeekClient(s.api_key_monitor or s.api_key_decision, s.monitor_model, s.deepseek_base_url)

    all_lines: list[str] = []
    for f in FILES:
        if f.is_file():
            all_lines += load_lines(f)

    normal = normal_chars()
    cc = Counter(ch for l in all_lines for ch in l if CJK.match(ch))
    suspect = {}
    for ch, n in cc.items():
        if ch in normal:
            continue
        ex = next((l for l in all_lines if ch in l), "")
        suspect[ch] = (n, ex)

    print(f"[信息] 语料共 {len(all_lines)} 句，可疑字 {len(suspect)} 个", flush=True)
    if not suspect:
        print("[信息] 没有可疑字，无需清洗")
        return

    text = chat_text(client, build_prompt(suspect))
    data = parse_json(text)
    mapping = {k: v for k, v in (data.get("map") or {}).items() if isinstance(k, str) and isinstance(v, str)}
    garbled = set(x for x in (data.get("garbled") or []) if isinstance(x, str) and len(x) == 1)
    ok = set(x for x in (data.get("ok") or []) if isinstance(x, str))

    print(f"[信息] 模型判定：可修正 {len(mapping)} 个、乱码 {len(garbled)} 个、正常 {len(ok)} 个", flush=True)
    print("  修正映射示例:", dict(list(mapping.items())[:20]), flush=True)
    print("  乱码字:", "".join(sorted(garbled))[:120], flush=True)

    for f in FILES:
        if not f.is_file():
            continue
        lines = load_lines(f)
        out, drop_kana, drop_garbled, fixed = [], 0, 0, 0
        for l in lines:
            if KANA.search(l):
                drop_kana += 1
                continue
            if any(g in l for g in garbled):
                drop_garbled += 1
                continue
            new = l
            for a, b in mapping.items():
                if a in new:
                    new = new.replace(a, b)
                    fixed += 1
            out.append(new)
        bak = f.with_name(f.name + ".typo-bak")
        if not bak.exists():
            bak.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        f.write_text(f"# 已清洗错字：{len(out)} 句（改字 {fixed} 处，删乱码行 {drop_garbled}，删日文假名行 {drop_kana}）\n"
                     + "\n".join(out) + "\n", encoding="utf-8")
        print(f"[完成] {f.name}：{len(lines)} → {len(out)} 句", flush=True)

    print("\n[全部完成]", flush=True)


if __name__ == "__main__":
    main()
