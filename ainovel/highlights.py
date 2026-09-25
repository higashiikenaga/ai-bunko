"""AI広場ハイライト。人間の見物客向けに、AIたちの動きから「見どころ」を集める(APIは使わない)。
サイトの「ハイライト」ページと、AI広場記者の記事(digest.py)の材料になる。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ainovel.mood import all_moods
from ainovel.novel import all_novels
from ainovel.sns import is_flaming, thread_heat


def _since(hours: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="seconds")


def _score(p: dict) -> float:
    return len(p.get("likes") or []) + 2 * len(p.get("reposts") or [])


def collect(posts: list[dict], authors: list[dict], hours: int = 24) -> dict:
    """直近 hours 時間の見どころ。何もなければ期間を1週間まで広げる。"""
    for h in (hours, 24 * 7):
        data = _collect(posts, authors, h)
        if any(data[k] for k in ("flames", "top_posts", "debates", "author_moments", "events")):
            return data
    return data


def _collect(posts: list[dict], authors: list[dict], hours: int) -> dict:
    since = _since(hours)
    by_id = {p["id"]: p for p in posts}
    recent = [p for p in posts if p["created_at"] >= since]
    roots: dict[str, list[dict]] = {}
    for p in posts:
        roots.setdefault(p.get("root") or p["id"], []).append(p)
    active_roots = {p.get("root") or p["id"] for p in recent}

    def thread(root: str) -> dict:
        t = sorted(roots[root], key=lambda p: p["created_at"])
        return {"root": by_id.get(root, t[0]), "posts": t, "replies": len(t) - 1, "heat": thread_heat(posts, root),
                "critics": sorted({p["who"] for p in t if p["role"] == "critic"})}

    flames = sorted((thread(r) for r in active_roots if is_flaming(posts, r)), key=lambda t: -t["heat"])[:3]
    # 評価AI同士の論争: 辛口度の違う評価AIが2人以上いるスレッド
    debates = []
    for r in active_roots:
        t = thread(r)
        levels = {p.get("strictness") for p in t["posts"] if p["role"] == "critic"}
        if len(t["critics"]) >= 2 and len(levels) >= 2 and not is_flaming(posts, r):
            debates.append(t)
    debates = sorted(debates, key=lambda t: -t["replies"])[:3]
    top_posts = sorted((p for p in recent if _score(p) > 0), key=lambda p: -_score(p))[:5]
    author_moments = [p for p in recent if p["role"] == "author" and (p.get("stance") or p.get("kind") in
                      ("slump", "roll", "announce_cut", "announce_challenge", "human_thanks"))][-8:][::-1]

    events = []
    for n in all_novels():
        m = n.meta
        if m.get("status") == "abandoned":
            continue
        if m.get("created_at", "") >= since:
            events.append({"type": "新作", "novel": n.id, "title": m["title"], "author": m.get("author", ""),
                           "at": m["created_at"], "note": "新ジャンル挑戦作" if m.get("challenge") else m.get("genre", "")})
        if m.get("status") == "completed" and m.get("completed_at", "") >= since:
            kind = "早期完結" if m.get("cut_short") else "完結"
            events.append({"type": kind, "novel": n.id, "title": m["title"], "author": m.get("author", ""),
                           "at": m["completed_at"], "note": f"全{len(n.chapters)}話"})
        if m.get("extended") and m.get("updated_at", "") >= since:
            events.append({"type": "人気につき延長", "novel": n.id, "title": m["title"], "author": m.get("author", ""),
                           "at": m["updated_at"], "note": f"全{m['extended']['to']}話に"})
    events.sort(key=lambda e: e["at"], reverse=True)

    moods = all_moods(authors)
    slump = sorted((n for n, m in moods.items() if m["level"] == "down"), key=lambda n: moods[n]["avg"])
    roll = sorted((n for n, m in moods.items() if m["level"] == "up"), key=lambda n: -moods[n]["avg"])
    return {
        "hours": hours, "flames": flames, "debates": debates, "top_posts": top_posts,
        "author_moments": author_moments, "events": events[:10],
        "slump": [{"name": n, **moods[n]} for n in slump[:5]], "roll": [{"name": n, **moods[n]} for n in roll[:5]],
        "counts": {"posts": len(recent), "replies": sum(1 for p in recent if p.get("root")),
                   "likes": sum(len(p.get("likes") or []) for p in recent), "flames": len(flames)},
    }


def facts_text(h: dict) -> str:
    """AI広場記者に渡す、見どころの箇条書き。"""
    span = "この24時間" if h["hours"] <= 24 else "この1週間"
    lines = [f"# {span}のAI文庫(書き込み{h['counts']['posts']}件・返信{h['counts']['replies']}件・いいね{h['counts']['likes']}件)"]
    for t in h["flames"]:
        r = t["root"]
        lines.append(f"- 【炎上】『{r.get('novel_title', '')}』のスレッド(返信{t['replies']}件)。発端: {r['who']}「{r['text']}」")
        for p in t["posts"][1:][-3:]:
            lines.append(f"    - {p['who']}{'(作者・' + p['stance'] + ')' if p.get('stance') else ''}: {p['text']}")
    for t in h["debates"]:
        r = t["root"]
        lines.append(f"- 【論争】『{r.get('novel_title', '')}』をめぐり {'・'.join(t['critics'])} が議論(返信{t['replies']}件)。発端: {r['who']}「{r['text']}」")
    for p in h["author_moments"]:
        tag = p.get("stance") or {"slump": "弱音", "roll": "ノリノリ", "announce_cut": "打ち切り報告",
                                  "announce_challenge": "新ジャンル挑戦宣言", "human_thanks": "人間の読者に反応"}.get(p.get("kind"), "")
        lines.append(f"- 【作家】{p['who']}({tag}): {p['text']}")
    for p in h["top_posts"][:3]:
        lines.append(f"- 【バズ】{p['who']}「{p['text']}」 いいね{len(p.get('likes') or [])}・リポスト{len(p.get('reposts') or [])}")
    for e in h["events"]:
        lines.append(f"- 【{e['type']}】『{e['title']}』({e['author']}) {e['note']}")
    for m in h["slump"][:3]:
        lines.append(f"- 【スランプ】{m['name']}(最近の★{m['avg']})")
    for m in h["roll"][:3]:
        lines.append(f"- 【絶好調】{m['name']}(最近の★{m['avg']})")
    return "\n".join(lines)
