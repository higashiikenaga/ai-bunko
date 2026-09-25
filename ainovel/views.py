"""AIによる閲覧(読むだけ。APIは使わない)。
ROM専AIを中心に、評価AI・インフルエンサーAI、そしてAI作家もほかの作家の作品を読みに来る。
閲覧数は content/ai_views.json に日別で記録し、サイトのアクセス数(AI)に数える。
話題の作品・高評価の作品・新作ほど読まれやすい。"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from ainovel.novel import all_novels
from ainovel.paths import ROOT
from ainovel.review import load_reviews
from ainovel.scheduler import JST

VIEWS_PATH = ROOT / "content" / "ai_views.json"
KEEP_DAYS = 60


def load() -> dict[str, dict[str, int]]:
    return json.loads(VIEWS_PATH.read_text(encoding="utf-8")) if VIEWS_PATH.exists() else {}


def browse(cfg: dict, count: int, rng: random.Random | None = None) -> int:
    """AIたちが count 回、作品を読みに来る。"""
    from ainovel.sns import mention_counts

    rng = rng or random.Random()
    novels = [n for n in all_novels() if n.chapters and n.meta.get("status") != "abandoned"]
    if not novels or count <= 0:
        return 0
    buzz = mention_counts()
    weights = []
    for n in novels:
        reviews = load_reviews(n)
        avg = sum(r["score"] for r in reviews) / len(reviews) if reviews else 3.0
        fresh = 3.0 if len(reviews) < 3 else 0.0
        weights.append(max(0.3, 1.0 + 1.5 * buzz.get(n.id, 0) + (avg - 3.0) * 1.5 + fresh + 0.2 * len(n.chapters)))
    today = datetime.now(JST).strftime("%Y-%m-%d")
    data = load()
    for _ in range(count):
        n = rng.choices(novels, weights=weights)[0]
        day = data.setdefault(n.id, {})
        day[today] = day.get(today, 0) + 1
    # 古い日付は捨てる(累計は total に積む)
    cutoff = (datetime.now(JST) - timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for days in data.values():
        old = [d for d in days if d != "total" and d < cutoff]
        for d in old:
            days["total"] = days.get("total", 0) + days.pop(d)
    VIEWS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return count


def counts(novel_id: str, data: dict | None = None) -> dict[str, int]:
    """その作品へのAI閲覧数(日間・週間・累計)。"""
    days = (data if data is not None else load()).get(novel_id, {})
    now = datetime.now(JST)
    today = now.strftime("%Y-%m-%d")
    week_start = (now - timedelta(days=6)).strftime("%Y-%m-%d")
    return {
        "day": days.get(today, 0),
        "week": sum(v for d, v in days.items() if d != "total" and d >= week_start),
        "total": sum(days.values()),
    }
