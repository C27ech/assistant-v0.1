"""轻量 BM25 检索器（纯 Python，无重依赖）。

为满足「中文用字符 2-gram 切词，无需 jieba」的要求：
- 连续 CJK 片段 -> 逐字 2-gram（单字片段保留单字）；
- 连续 ASCII/数字片段 -> 按非字母数字切词，保留完整英文/数字词；
- 同时保留 CJK 单字与英文词的原始形态，避免短文本检索完全失效。

BM25 采用标准公式：IDF * (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avgdl))。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+")
_NON_ASCII_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def _cjk_bigrams(text: str) -> List[str]:
    """把连续 CJK 文本切为 2-gram；长度 1 时保留单字。"""
    tokens: List[str] = []
    for chunk in _CJK_RE.findall(text):
        chunk = chunk.lower()
        if len(chunk) == 1:
            tokens.append(chunk)
            continue
        for i in range(len(chunk) - 1):
            tokens.append(chunk[i:i + 2])
    return tokens


def _ascii_tokens(text: str) -> List[str]:
    """把英文/数字连续片段作为完整词元（小写）。"""
    return [m.group(0).lower() for m in _ASCII_TOKEN_RE.finditer(text)]


def tokenize(text: Optional[str]) -> List[str]:
    """返回 query / document 的词元列表。

    CJK 部分走 2-gram，ASCII 部分走整词。两种词元均保留，
    这样「窗口标题 / window_activate」这类中英混合文本也能命中。
    """
    if not isinstance(text, str) or not text.strip():
        return []
    text = text.strip()
    tokens = _cjk_bigrams(text)
    tokens.extend(_ascii_tokens(text))
    if not tokens:
        # 纯符号 / 空格文本：退回单字，避免空文档。
        tokens = [ch for ch in text.lower() if not ch.isspace()]
    return tokens


@dataclass
class _IndexedDoc:
    doc_id: int
    text: str
    tokens: List[str]


class BM25:
    """纯 Python BM25 索引。

    用法：
        index = BM25()
        index.add(doc_id, text)
        results = index.search(query, top_k=50)
        # results: [(doc_id, score), ...]
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._docs: List[_IndexedDoc] = []
        self._doc_by_id: Dict[int, _IndexedDoc] = {}
        self._df: Dict[str, int] = {}
        self._avgdl: float = 0.0

    def add(self, doc_id: int, text: str) -> None:
        """添加或更新一篇文档。doc_id 重复时替换旧文档并更新统计。"""
        tokens = tokenize(text)
        old = self._doc_by_id.get(doc_id)
        if old is not None:
            # 先从 df 中移除旧文档贡献。
            for tok in set(old.tokens):
                self._df[tok] = max(0, self._df.get(tok, 0) - 1)
            self._docs = [d for d in self._docs if d.doc_id != doc_id]
            self._doc_by_id.pop(doc_id, None)

        doc = _IndexedDoc(doc_id=doc_id, text=text, tokens=tokens)
        self._docs.append(doc)
        self._doc_by_id[doc_id] = doc
        for tok in set(tokens):
            self._df[tok] = self._df.get(tok, 0) + 1

        lengths = [len(d.tokens) for d in self._docs]
        self._avgdl = float(sum(lengths)) / max(1, len(lengths))

    def _idf(self, term: str) -> float:
        df = self._df.get(term, 0)
        n = len(self._docs)
        return math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 50) -> List[Tuple[int, float]]:
        """返回按 BM25 分数降序排列的 (doc_id, score) 列表。"""
        if not self._docs:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        avgdl = self._avgdl or 1.0
        scored: List[Tuple[int, float]] = []
        for doc in self._docs:
            score = 0.0
            dl = float(len(doc.tokens))
            term_counts: Dict[str, int] = {}
            for tok in doc.tokens:
                term_counts[tok] = term_counts.get(tok, 0) + 1

            for term in query_tokens:
                if term not in term_counts:
                    continue
                tf = float(term_counts[term])
                idf = self._idf(term)
                denom = tf + self.k1 * (1.0 - self.b + self.b * dl / avgdl)
                score += idf * (tf * (self.k1 + 1.0)) / denom

            if score > 0.0:
                scored.append((doc.doc_id, score))

        scored.sort(key=lambda item: (item[1], item[0]), reverse=True)
        top_k = max(1, int(top_k))
        return scored[:top_k]
