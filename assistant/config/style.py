"""风格引擎：把「语料蒸馏出的风格说明书 + 动态检索的例句」注入系统提示词。

设计（两步走，见仓库根 README 第八节）：
  1) 蒸馏：把海量语料压成紧凑的风格说明书（用 scripts/build_style_guide.py 生成）；
  2) 检索：每轮按用户当前说的话，从语料里召回最相关的几句原话当 few-shot 范例。

文件约定：
  - style/style_guide.md        风格说明书（常量，整段注入）
  - style/corpus.txt            检索语料，每行一句（用于动态检索）
  - style/corpus_distill.txt    蒸馏语料（可选，可中英混排，比检索语料更大）
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STYLE_DIR = BASE_DIR / "style"
GUIDE_FILE = STYLE_DIR / "style_guide.md"
LINES_FILE = STYLE_DIR / "corpus.txt"  # 检索语料（只用中文）
DISTILL_FILE = STYLE_DIR / "corpus_distill.txt"  # 蒸馏语料（中英文都有）

_MAX_LINES = 20000  # 语料库读取上限（防止超大文件拖慢检索）
_MIN_EXAMPLE_LEN = 12  # 过短的台词（如「哈？」「你……」）展示不出句式，不适合当 few-shot 范例

# 没有风格说明书时的极简兜底（正常情况下 style_guide.md 存在，走它）
_FALLBACK_GUIDE = (
    "- 理性、嘴快、逻辑强；措辞精确、喜欢分点\n"
    "- 傲娇：嘴硬但认真负责，先否认再默默把事做了\n"
    "- 毒舌：点到为止，不刻薄、不人身攻击\n"
    "- 结尾简短干脆，不要用「如需帮助请随时告诉我」「如果还有其他需求请提出」这类 AI 客套话"
)


def load_style_guide() -> str:
    """读取风格说明书；不存在则返回空串。"""
    if GUIDE_FILE.is_file():
        try:
            return GUIDE_FILE.read_text(encoding="utf-8", errors="replace").strip()
        except Exception as e:  # noqa: BLE001
            print(f"[style] 读取 {GUIDE_FILE} 失败：{e}")
    return ""


def load_lines(limit: int = _MAX_LINES) -> list[str]:
    """读取语料库：每行一句台词，跳过空行和 # 开头的注释行。"""
    if not LINES_FILE.is_file():
        return []
    try:
        raw = LINES_FILE.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        print(f"[style] 读取 {LINES_FILE} 失败：{e}")
        return []
    lines: list[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        lines.append(s)
        if len(lines) >= limit:
            break
    return lines


def retrieve_examples(query: str, k: int = 8) -> list[str]:
    """按语义相关度检索最相关的若干台词，作为 few-shot 范例。"""
    if not (query or "").strip():
        return []
    # 只挑「够长」的台词当范例：短句虽然也是口癖，但展示不出句式，当范例效果差
    lines = [ln for ln in load_lines() if len(ln) >= _MIN_EXAMPLE_LEN]
    if not lines:
        return []
    from storage.memory import search  # 复用已实现的 TF-IDF 检索

    idxs = search(lines, query, k=k)
    return [lines[i] for i in idxs]


def build_style_block(query: str = "", k: int = 8) -> str:
    """拼装要注入系统提示词的风格块（风格说明书 + 动态例句）。无内容时返回空串。"""
    parts: list[str] = []
    guide = load_style_guide() or _FALLBACK_GUIDE
    parts.append(
        "## 角色说话风格说明书（严格遵守）\n"
        "（注意：说明书里提到的角色名/称谓只是风格参照，实际对话中**不要出现**这些名字）\n"
        + guide
    )
    examples = retrieve_examples(query, k=k)
    if examples:
        parts.append(
            "## 说话范例（**只学语气和句式**）\n"
            "用法限制：**只模仿语气、口癖、句式**；不要照抄内容、不要提角色名、"
            "**不要跟着范例里的话题跑**（范例常是游戏里的情节台词，跟用户当前问题无关）。\n"
            + "\n".join(f"- {e}" for e in examples)
        )
    return "\n\n".join(parts)
