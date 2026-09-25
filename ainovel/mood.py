"""AI作家の「やる気」。自作への最近の評価で上下する。
- 評価が低いと落ち込む: 更新ペースが落ち、新作では得意ジャンル以外に挑戦してみたりする。
  あまりに低い連載は、作者の判断で早めに畳む(打ち切り)ことがある
- 評価が高いと乗ってくる: 更新ペースが上がる
"""
from __future__ import annotations

import random

from ainovel.novel import Novel, all_novels
from ainovel.review import load_reviews

RECENT = 10        # 最近のレビュー何件で判断するか
MIN_REVIEWS = 3    # これ未満なら判断しない(普通)
LOW, HIGH = 2.8, 4.0
CUT_AVG, CUT_MIN_REVIEWS, CUT_PROB = 2.3, 4, 0.3

PACE_FACTOR = {"down": 0.6, "normal": 1.0, "up": 1.3}
LABEL = {"down": "スランプ気味", "normal": "", "up": "絶好調"}


def _recent_reviews(name: str, novels: list[Novel] | None = None) -> list[dict]:
    reviews = [r for n in (novels or all_novels()) if n.meta.get("author") == name for r in load_reviews(n)]
    return sorted(reviews, key=lambda r: r["created_at"])[-RECENT:]


def author_mood(name: str, novels: list[Novel] | None = None) -> dict:
    reviews = _recent_reviews(name, novels)
    if len(reviews) < MIN_REVIEWS:
        return {"level": "normal", "avg": None, "count": len(reviews)}
    avg = sum(r["score"] for r in reviews) / len(reviews)
    level = "down" if avg < LOW else "up" if avg >= HIGH else "normal"
    return {"level": level, "avg": round(avg, 1), "count": len(reviews)}


def all_moods(authors: list[dict]) -> dict[str, dict]:
    novels = all_novels()
    return {a["name"]: author_mood(a["name"], novels) for a in authors}


def mood_text(mood: dict) -> str:
    """AI広場の書き込みに反映する、作家の最近の気分。"""
    if mood["level"] == "down":
        return f"最近、自作の評価が低く(★{mood['avg']})落ち込み気味。自信をなくしかけている。"
    if mood["level"] == "up":
        return f"最近、自作の評価が高く(★{mood['avg']})ノッている。"
    return ""


def maybe_cut_short(novel: Novel, index: int, rng: random.Random | None = None) -> bool:
    """評価があまりに低い連載を、作者の判断で次の話で畳む(1作品1回)。畳んだら True。"""
    rng = rng or random.Random()
    total = int(novel.meta["target_chapters"])
    if novel.meta.get("cut_short") or novel.meta.get("extended") or index >= total or index < 3:
        return False
    reviews = load_reviews(novel)[-RECENT:]
    if len(reviews) < CUT_MIN_REVIEWS:
        return False
    avg = sum(r["score"] for r in reviews) / len(reviews)
    if avg >= CUT_AVG or rng.random() >= CUT_PROB:
        return False
    novel.meta["cut_short"] = {"from": total, "to": index, "avg": round(avg, 1)}
    novel.meta["target_chapters"] = index
    novel.save_meta()
    print(f"  評価が伸びない(★{avg:.1f})ため、作者はこの話で物語を畳むことにしました(全{total}話 → 全{index}話)")
    return True
