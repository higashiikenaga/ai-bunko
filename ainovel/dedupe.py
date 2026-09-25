"""AIが同じ内容を繰り返していないかの判定。

- 章本文: 同じ作品の過去の章と、文字5-gramの重なり(Jaccard係数)で比べる。
  日本語は単語区切りがないので文字単位のn-gramで見る。普通に書き進めた章同士は 0.01〜0.03、
  前章の段落を3〜5割使い回した章は 0.1〜0.4 になる(実測)。基準は 0.15。
- タイトル: 既存作品と同じ、またはほぼ同じ(記号・空白を除いて一致率が高い)ものを弾く。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

CHAPTER_SIMILARITY_LIMIT = 0.15
TITLE_SIMILARITY_LIMIT = 0.8


def _shingles(text: str, n: int = 5) -> set[str]:
    t = re.sub(r"\s+", "", text)
    return {t[i : i + n] for i in range(max(0, len(t) - n + 1))}


def chapter_similarity(a: str, b: str) -> float:
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def most_similar_chapter(text: str, previous: list[tuple[int, str]]) -> tuple[int | None, float]:
    """(最も似ている章の番号, 類似度)。"""
    best = (None, 0.0)
    for index, other in previous:
        s = chapter_similarity(text, other)
        if s > best[1]:
            best = (index, s)
    return best


def _norm_title(title: str) -> str:
    return re.sub(r"[\s、。,.・!！?？「」『』()（）ー―\-]", "", title)


def similar_title(title: str, existing: list[str]) -> str | None:
    """既存作品とほぼ同じタイトルなら、その既存タイトルを返す。"""
    t = _norm_title(title)
    for other in existing:
        o = _norm_title(other)
        if t == o or (t and o and SequenceMatcher(None, t, o).ratio() >= TITLE_SIMILARITY_LIMIT):
            return other
    return None
