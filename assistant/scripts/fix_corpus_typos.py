"""用大模型清洗语料里的「字体识别错字」，生成干净语料（原文件自动备份 .typo-bak）。

背景：语料来自游戏字体「字形模板匹配」，会有少量字被识别成形近错字：
  形近字（范围→范囷、周围→周囷、黑洞→蕙洞）、日文汉字（対/気/鉄/酔/曽）、
  繁体字（論/質/橫/贊）、日文假名（な/の）、极少数乱码字。

用法：
    python scripts/fix_corpus_typos.py              # 清洗 style/ 下两份语料
    python scripts/fix_corpus_typos.py <输入文件>    # 只清洗指定文件
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import Settings  # noqa: E402
from llm.deepseek import DeepSeekClient  # noqa: E402

STYLE_DIR = BASE_DIR / "style"
DEFAULT_FILES = [
    STYLE_DIR / "corpus.txt",
    STYLE_DIR / "corpus_distill.txt",
]
CHUNK = 200

PROMPT = """下面是从游戏里提取的角色台词，**每行开头是行号**（格式 行号|台词）。
提取时用的是字形模板匹配，导致少量字被识别成形近的错字。常见错误类型：
1. 形近字（「范围」→「范囷」、「周围」→「周囷」、「黑洞」→「蕙洞」、「关门」→「弡门」）
2. 日文汉字（対/気/鉄/酔/曽/実/変）与日文假名（な/の/は）
3. 繁体字（論/質/橫/贊/倫/設/義）
4. 极少数整片乱码的字

任务：**只输出「需要修正的行」**，每行格式必须是：`行号|修正后的完整台词`
- 完全正确的行**不要输出**
- 行号必须原样照抄（我要靠它定位）
- 只把错字改成正确的字：不要改写句子、不要增删词语、不要改语气、不要动标点
- **英文行、数字、全角标点一律不要改**
- **不要输出任何解释、前言、代码块标记（```），也不要输出没问题的行**
- 如果整段都没有错字，就什么都不要输出

待检查台词：
"""


def _chat_text(client: DeepSeekClient, prompt: str, max_tokens: int = 8000, attempts: int = 3) -> str:
    for i in range(attempts):
        r = client.chat([{"role": "user", "content": prompt}], max_tokens=max_tokens * (i + 1))
        text = (r.content or "").strip()
        if text:
            return text
        print(f"[警告] 空内容（finish={r.finish_reason}），第 {i + 1} 次重试...", flush=True)
        time.sleep(1)
    return ""


def fix_file(client: DeepSeekClient, src: Path) -> None:
    raw = src.read_text(encoding="utf-8")
    lines = [x for x in raw.splitlines() if x.strip() and not x.startswith("#")]
    print(f"\n[信息] 清洗 {src.name}：{len(lines)} 句", flush=True)

    out = list(lines)  # 在副本上按行号打补丁
    changed = 0
    skipped = 0
    total = (len(lines) + CHUNK - 1) // CHUNK
    for i in range(0, len(lines), CHUNK):
        batch = lines[i : i + CHUNK]
        idx = i // CHUNK + 1
        numbered = "\n".join(f"{i + j + 1}|{t}" for j, t in enumerate(batch))
        text = _chat_text(client, PROMPT + numbered)
        n = 0
        for ln in text.splitlines():
            ln = ln.strip().strip("`").strip()
            if "|" not in ln:
                continue
            num_str, _, new_text = ln.partition("|")
            num_str = num_str.strip()
            if not num_str.isdigit():
                continue
            num = int(num_str)
            if not (i + 1 <= num <= i + len(batch)):
                skipped += 1
                continue  # 行号越界，忽略
            new_text = new_text.strip()
            old_text = out[num - 1]
            # 安全阀：新文本长度离原句太远，视为「改写」而不是「改错字」，拒绝
            if not new_text or len(new_text) > len(old_text) * 2 + 10:
                skipped += 1
                continue
            if new_text != old_text:
                out[num - 1] = new_text
                n += 1
        changed += n
        print(f"[信息]   {idx}/{total} 批：修正 {n} 行", flush=True)

    bak = src.with_name(src.name + ".typo-bak")
    if not bak.exists():
        bak.write_text(raw, encoding="utf-8")
        print(f"[信息] 原文件已备份 → {bak.name}", flush=True)
    src.write_text(
        f"# 已用大模型清洗错字：共 {len(out)} 句，修正 {changed} 行，跳过 {skipped} 条可疑输出\n"
        + "\n".join(out)
        + "\n",
        encoding="utf-8",
    )
    print(f"[完成] {src.name}：{len(out)} 句，修正 {changed} 行", flush=True)


def main() -> None:
    s = Settings.load()
    api_key = s.api_key_monitor or s.api_key_decision
    model = s.monitor_model or s.decision_model
    client = DeepSeekClient(api_key, model, s.deepseek_base_url)
    print(f"[信息] 清洗模型：{model}", flush=True)

    targets = [Path(sys.argv[1])] if len(sys.argv) > 1 else DEFAULT_FILES
    for f in targets:
        if f.is_file():
            fix_file(client, f)
        else:
            print(f"[跳过] 找不到 {f}", flush=True)
    print("\n[全部完成]", flush=True)


if __name__ == "__main__":
    main()
