"""从语料库离线蒸馏出「风格说明书」。

用法：
    python scripts/build_style_guide.py                # 用默认路径
    python scripts/build_style_guide.py <语料> <输出>

把 style/corpus.txt 按批喂给大模型，让它归纳角色的口癖 / 句式 / 语气规则，
最后合并成一份紧凑的 style/style_guide.md（会被注入系统提示词）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config.settings import Settings  # noqa: E402
from llm.deepseek import DeepSeekClient  # noqa: E402

DEFAULT_LINES = BASE_DIR / "style" / "corpus_distill.txt"
FALLBACK_LINES = BASE_DIR / "style" / "corpus.txt"
DEFAULT_GUIDE = BASE_DIR / "style" / "style_guide.md"
CHUNK = 400  # 每批送给模型的台词条数


def load_lines(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    return [s.strip() for s in raw.splitlines() if s.strip() and not s.strip().startswith("#")]


def _chat_text(client: DeepSeekClient, prompt: str, max_tokens: int = 4000, attempts: int = 3) -> str:
    """调用模型并保证返回非空文本。

    推理模型偶尔会把 token 全花在「思考」上、导致 content 为空，
    这里自动加大额度重试，最后兜底退回思考内容（总比整段丢失强）。
    """
    messages = [{"role": "user", "content": prompt}]
    last_reasoning = ""
    for i in range(attempts):
        result = client.chat(messages, max_tokens=max_tokens * (i + 1))
        text = (result.content or "").strip()
        if text:
            return text
        last_reasoning = (result.reasoning_content or "").strip()
        print(f"[警告] 模型返回空内容（finish={result.finish_reason}），第 {i + 1} 次重试...", flush=True)
        time.sleep(1)
    return last_reasoning


def distill_chunk(client: DeepSeekClient, lines: list[str]) -> str:
    text = "\n".join(lines)
    prompt = (
        "下面是一个角色在作品里的台词集合。请你分析她的说话风格，"
        "归纳成简洁的要点，供另一个 AI 模仿她说话。\n"
        "只归纳「怎么说话」（口癖、句式、语气、情绪表达方式），不要复述剧情内容。\n"
        "输出分点，尽量具体、可执行（能直接照着写）。\n\n"
        f"台词：\n{text}"
    )
    return _chat_text(client, prompt)


def merge_notes(client: DeepSeekClient, notes: list[str]) -> str:
    joined = "\n\n---\n\n".join(notes)
    prompt = (
        "下面是分多批归纳出的同一个角色的说话风格要点，可能有重复。\n"
        "请合并去重，整理成一份紧凑、结构化的「风格说明书」（Markdown），"
        "包含四个部分：口癖与高频用语、句式特征、语气分寸、禁忌。\n"
        "控制在 1500 字以内，直接输出 Markdown 正文，不要额外解释。\n\n"
        f"{joined}"
    )
    return _chat_text(client, prompt)


def main() -> None:
    lines_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_LINES
    guide_path = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_GUIDE

    if not lines_path.is_file():
        # 没有「蒸馏语料」就退回「检索语料」
        if lines_path == DEFAULT_LINES and FALLBACK_LINES.is_file():
            print(f"[信息] 未找到 {lines_path.name}，改用 {FALLBACK_LINES.name}", flush=True)
            lines_path = FALLBACK_LINES
        else:
            print(f"[错误] 语料文件不存在：{lines_path}", flush=True)
            print("请先运行 scripts/import_sg_lines.py 生成语料。", flush=True)
            return

    lines = load_lines(lines_path)
    print(f"[信息] 读取到 {len(lines)} 条台词", flush=True)
    if not lines:
        print("[错误] 语料为空", flush=True)
        return

    settings = Settings.load()
    # 蒸馏用 flash 模型：够用且快得多（pro 推理模型每次要 30s+）
    api_key = settings.api_key_monitor or settings.api_key_decision
    model = settings.monitor_model or settings.decision_model
    if not api_key:
        print("[错误] 未配置 DEEPSEEK_API_KEY_MONITOR / _DECISION", flush=True)
        return
    client = DeepSeekClient(api_key, model, settings.deepseek_base_url)
    print(f"[信息] 使用模型：{model}", flush=True)

    notes: list[str] = []
    total = (len(lines) + CHUNK - 1) // CHUNK
    for i in range(0, len(lines), CHUNK):
        batch = lines[i : i + CHUNK]
        idx = i // CHUNK + 1
        print(f"[信息] 蒸馏第 {idx}/{total} 批（{len(batch)} 条）...", flush=True)
        note = distill_chunk(client, batch)
        print(f"[信息]   第 {idx} 批产出 {len(note)} 字", flush=True)
        if note:
            notes.append(note)

    if not notes:
        print("[错误] 所有批次都没产出内容，无法生成说明书", flush=True)
        return

    print("[信息] 合并各批要点...", flush=True)
    guide = merge_notes(client, notes)
    if not guide:
        print("[警告] 合并失败，改为直接拼接各批要点", flush=True)
        guide = "# 风格说明书（各批要点拼接）\n\n" + "\n\n---\n\n".join(notes)

    guide_path.parent.mkdir(parents=True, exist_ok=True)
    guide_path.write_text(guide + "\n", encoding="utf-8")
    print(f"[完成] 风格说明书已写入：{guide_path}（{len(guide)} 字）", flush=True)


if __name__ == "__main__":
    main()
