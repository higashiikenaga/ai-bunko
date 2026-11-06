"""全年齢向けサイトのための表現チェックと伏字。

config.yaml の moderation(カテゴリ別の語リスト)を使う。投稿時(functions/api/submit.js・lib/moderation.js)は
該当すると投稿を拒否し、ビルド時はここで伏字(○)にする。後から語を足すと、過去の作品にも反映される。
照合は lib/moderation.js と同じ規則:
  - 全角/半角・大文字小文字・ひらがな/カタカナの違いを無視する(文字数は変えない)
  - 語の途中に空白・記号・ゼロ幅文字を挟んでも一致とみなす
語の一致だけを見るので、言い換えや文脈は判定できない(完全な検閲ではない)。
"""
from __future__ import annotations

import re
import unicodedata

SEP = r"[\s​-‍⁠﻿・･.,_*＊※\-－—　]*"
MASK = "○"


def fold(text: str) -> str:
    """文字数を変えずに表記ゆれをそろえる(全角→半角英数、カタカナ→ひらがな、小文字化)。"""
    out = []
    for ch in text:
        c = unicodedata.normalize("NFKC", ch)
        if len(c) != 1:
            c = ch
        if "ァ" <= c <= "ヶ":
            c = chr(ord(c) - 0x60)
        low = c.lower()
        out.append(low if len(low) == 1 else c)
    return "".join(out)


def compile_words(words: list[str]) -> list[tuple[str, re.Pattern]]:
    pats = []
    for w in dict.fromkeys(w for w in words if w):
        chars = [re.escape(c) for c in fold(w)]
        pats.append((w, re.compile(SEP.join(chars))))
    return pats


def all_words(cfg: dict) -> list[str]:
    mod = cfg.get("moderation") or {}
    return [w for words in mod.values() for w in words]


def find(text: str, pats: list[tuple[str, re.Pattern]]) -> list[str]:
    folded = fold(text)
    return [w for w, p in pats if p.search(folded)]


def mask(text: str, pats: list[tuple[str, re.Pattern]]) -> tuple[str, int]:
    """該当箇所を○に置き換える(改行は残す)。置き換えた箇所の数も返す。"""
    folded = fold(text)
    spans = sorted((m.start(), m.end()) for _, p in pats for m in p.finditer(folded))
    if not spans:
        return text, 0
    merged = [list(spans[0])]
    for s, e in spans[1:]:
        if s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, pos = [], 0
    for s, e in merged:
        out.append(text[pos:s])
        out.append("".join(c if c == "\n" else MASK for c in text[s:e]))
        pos = e
    out.append(text[pos:])
    return "".join(out), len(merged)
