# -*- coding: utf-8 -*-
"""语义记忆：基于 TF-IDF（字符 n-gram）的内容相关性检索。

把历史对话按「内容相关性」召回，而不是只取最近 N 条。
纯 Python 实现，不依赖外部 embedding 服务。
"""
from __future__ import annotations

import math
import re
from collections import Counter


def tokenize(text: str) -> list[str]:
    """切 token：中文按字符 unigram + bigram，英文/数字按整词小写。"""
    text = text or ""
    tokens: list[str] = []
    for seg in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(seg)  # unigram
        tokens.extend(seg[i : i + 2] for i in range(len(seg) - 1))  # bigram
    tokens.extend(w.lower() for w in re.findall(r"[a-zA-Z0-9_]+", text))
    return tokens


def _tf(text: str) -> Counter:
    return Counter(tokenize(text))


def _build_idf(docs: list[str]) -> dict[str, float]:
    """文档频率 IDF：越常见的词权重越低。"""
    df: Counter = Counter()
    for doc in docs:
        df.update(set(tokenize(doc)))
    n = max(len(docs), 1)
    return {t: math.log((n + 1) / (c + 1)) + 1.0 for t, c in df.items()}


def _cosine(a: Counter, b: Counter, idf: dict[str, float]) -> float:
    """两个 TF 向量在 IDF 加权下的余弦相似度。"""
    dot = 0.0
    for t, ca in a.items():
        cb = b.get(t)
        if cb:
            w = idf.get(t, 1.0)
            dot += (ca * w) * (cb * w)
    if dot == 0.0:
        return 0.0
    na = math.sqrt(sum((c * idf.get(t, 1.0)) ** 2 for t, c in a.items()))
    nb = math.sqrt(sum((c * idf.get(t, 1.0)) ** 2 for t, c in b.items()))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def search(docs: list[str], query: str, k: int = 5) -> list[int]:
    """返回与 query 内容最相关的文档下标（按相似度降序）。"""
    if not docs or not (query or "").strip():
        return []
    qt = _tf(query)
    idf = _build_idf(docs)
    scored = [(_cosine(qt, _tf(d), idf), i) for i, d in enumerate(docs)]
    scored = [(s, i) for s, i in scored if s > 0]
    scored.sort(reverse=True)
    return [i for _, i in scored[:k]]
