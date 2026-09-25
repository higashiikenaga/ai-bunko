"""作家AIがほかの作家の作品を読んで、刺激を受ける。
- 次の話を書くとき、ときどき同じジャンル(なければ全体)の評価の高い他作家の作品を「読んだ」ことにして、
  読者に好評だった点(評価AIの「良かった点」)を、自分の作風に合う形で参考にする
- 内容・設定・文章は真似しない。取り入れるのは技法だけ
- 参考にした作品は作品ページに「作者が刺激を受けた作品」として表示する
"""
from __future__ import annotations

import random

from ainovel.novel import Novel, all_novels
from ainovel.review import load_reviews

PROBABILITY = 0.3   # 1話書くたびに、他作家の作品を読んで参考にする確率
MIN_REVIEWS = 3
MIN_AVG = 3.5


def candidates(author: str, genre: str = "") -> list[tuple[Novel, float, list[str]]]:
    """参考にできる他作家の作品 (作品, ★平均, 好評だった点)。同じジャンルを先に。"""
    out = []
    for n in all_novels():
        if not n.chapters or n.meta.get("author") == author or n.meta.get("status") == "abandoned":
            continue
        reviews = load_reviews(n)
        if len(reviews) < MIN_REVIEWS:
            continue
        avg = sum(r["score"] for r in reviews) / len(reviews)
        if avg < MIN_AVG:
            continue
        goods = list(dict.fromkeys(r["good"] for r in reviews if r.get("good")))[-5:]
        out.append((n, avg, goods))
    same = [c for c in out if c[0].meta.get("genre") == genre]
    return same or out


def pick(author: str, genre: str = "", rng: random.Random | None = None) -> tuple[Novel, float, list[str]] | None:
    rng = rng or random.Random()
    cands = candidates(author, genre)
    if not cands:
        return None
    return rng.choices(cands, weights=[avg for _, avg, _ in cands])[0]


def prompt_block(novel: Novel, index: int, rng: random.Random | None = None) -> str:
    """次の話のプロンプトに入れる「最近読んだ他作家の作品」。参考にしない回は空文字。"""
    rng = rng or random.Random()
    if novel.meta.get("special") or rng.random() >= PROBABILITY:
        return ""
    picked = pick(novel.meta.get("author", ""), novel.meta.get("genre", ""), rng)
    if not picked:
        return ""
    peer, avg, goods = picked
    novel.meta.setdefault("inspired_by", []).append(
        {"novel": peer.id, "title": peer.meta["title"], "author": peer.meta.get("author", ""), "chapter": index})
    novel.meta["inspired_by"] = novel.meta["inspired_by"][-10:]
    novel.save_meta()
    print(f"  📚 {novel.meta.get('author')} は『{peer.meta['title']}』({peer.meta.get('author')})を読んで刺激を受けた")
    return f"""
# 最近読んだ、ほかの作家の作品(参考)
『{peer.meta['title']}』(作: {peer.meta.get('author', '')}、★{avg:.1f})。読者に好評だった点: {'、'.join(goods) or '(記録なし)'}
この作品の内容・設定・登場人物・文章は決して真似しない。好評だった理由(技法や工夫)だけを、あなた自身の作風とこの物語に合う形で取り入れてよい。
"""
